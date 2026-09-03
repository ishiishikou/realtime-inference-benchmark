#!/usr/bin/env python3
"""A/B: Browser->WebRTC->MediaMTX->(RTSP+FFmpeg | WebRTC/WHEP)."""
from __future__ import annotations

import argparse, json, queue, shutil, statistics, subprocess, sys, threading, time, urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

BLOCK, GAP, X0, Y0, BITS = 32, 8, 16, 16, 16
CROP_W, CROP_H = X0 + BITS * (BLOCK + GAP) + 8, Y0 + BLOCK + 8
MARKER_JS = f"""
(() => {{
 const v=document.querySelector('video'); if(!v||v.readyState<2||!v.videoWidth)return null;
 if(!window.__bc){{window.__bc=document.createElement('canvas');__bc.width={CROP_W};__bc.height={CROP_H};window.__bx=__bc.getContext('2d',{{willReadFrequently:true}});}}
 __bx.drawImage(v,0,0,__bc.width,__bc.height,0,0,__bc.width,__bc.height); const d=__bx.getImageData(0,0,__bc.width,__bc.height).data; let m=0;
 for(let b=0;b<{BITS};b++){{const x={X0}+b*({BLOCK}+{GAP})+{BLOCK//2},y={Y0}+{BLOCK//2};let s=0,n=0;for(let dy=-2;dy<=2;dy++)for(let dx=-2;dx<=2;dx++){{const o=((y+dy)*__bc.width+x+dx)*4;s+=(d[o]+d[o+1]+d[o+2])/3;n++;}}if(s/n>128)m|=(1<<b);}}
 const lo=m&255, inv=(m>>>8)&255; if(inv!==((~lo)&255))return null; return {{id:lo,currentTime:v.currentTime,width:v.videoWidth,height:v.videoHeight}};
}})()
"""

@dataclass
class Ev:
    t: float
    frame_id: int
    source: str
    current_time: float | None = None


def decode_gray(buf: bytes, width: int) -> int | None:
    m=0
    for b in range(BITS):
        x=X0+b*(BLOCK+GAP)+BLOCK//2; y=Y0+BLOCK//2
        vals=[buf[(y+dy)*width+x+dx] for dy in range(-2,3) for dx in range(-2,3)]
        if statistics.fmean(vals)>128:m|=1<<b
    lo=m&255; inv=(m>>8)&255
    return lo if inv==((~lo)&255) else None


class FF(threading.Thread):
    def __init__(self,url:str,w:int,h:int,q:queue.Queue[Ev]):
        super().__init__(daemon=True); self.url=url; self.w=w; self.h=h; self.q=q; self.proc=None; self.t0=None; self.stderr=""
    def run(self):
        cmd=["ffmpeg","-hide_banner","-loglevel","warning","-rtsp_transport","tcp","-i",self.url,"-an","-vf","fps=2,format=gray","-f","rawvideo","pipe:1"]
        self.t0=time.monotonic(); self.proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE); size=self.w*self.h
        while True:
            b=self.proc.stdout.read(size)
            if len(b)!=size:break
            fid=decode_gray(b,self.w)
            if fid is not None:self.q.put(Ev(time.monotonic(),fid,"rtsp_ffmpeg"))
        if self.proc.stderr:self.stderr=self.proc.stderr.read().decode(errors="replace")
    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(2)


def driver(fake:Path|None=None):
    chrome=shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:raise RuntimeError("Chrome not found")
    o=Options();o.binary_location=chrome
    for a in ["--headless=new","--no-sandbox","--disable-dev-shm-usage","--autoplay-policy=no-user-gesture-required","--window-size=1280,720"]:o.add_argument(a)
    if fake:
        o.add_argument("--use-fake-device-for-media-stream");o.add_argument("--use-fake-ui-for-media-stream");o.add_argument(f"--use-file-for-fake-video-capture={fake.resolve()}")
    dp=shutil.which("chromedriver"); d=webdriver.Chrome(service=Service(dp) if dp else Service(),options=o);d.set_page_load_timeout(15);return d


def marker(d)->dict[str,Any]|None:
    try:r=d.execute_script(f"return {MARKER_JS};")
    except Exception:return None
    return r if isinstance(r,dict) and "id" in r else None


def wait_path(name:str,timeout=12):
    end=time.monotonic()+timeout; last=""
    while time.monotonic()<end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:9997/v3/paths/list",timeout=1) as r:last=r.read().decode(errors="replace")
            if name in last:return
        except Exception as e:last=repr(e)
        time.sleep(.2)
    raise RuntimeError(f"path not ready: {name}; {last[:300]}")


def start_publisher(d,base,w,h,fps):
    d.get(f"{base}/publish?video-framerate={fps}&video-width={w}&video-height={h}&audio-device=none&video-codec=h264/90000&video-bitrate=5000")
    end=time.monotonic()+10
    while time.monotonic()<end:
        try:
            codecs=Select(d.find_element(By.ID,"video-codec")); vals=[x.get_attribute("value") for x in codecs.options]
            if not vals:raise ValueError()
            if "h264/90000" not in vals:raise RuntimeError(f"H264 unavailable: {vals}")
            codecs.select_by_value("h264/90000");Select(d.find_element(By.ID,"audio-device")).select_by_value("none")
            for i,v in [("video-framerate",fps),("video-width",w),("video-height",h),("video-bitrate",5000)]:
                e=d.find_element(By.ID,i);e.clear();e.send_keys(str(v))
            d.find_element(By.ID,"publish-button").click();break
        except RuntimeError:raise
        except Exception:time.sleep(.2)
    else:raise RuntimeError("publisher UI not ready")
    wait_path("benchmark/live")
    end=time.monotonic()+8
    while time.monotonic()<end:
        if marker(d):return
        time.sleep(.1)
    raise RuntimeError("publisher marker not visible")


def uniq(xs:list[Ev])->list[Ev]:
    out=[];last=None
    for e in sorted(xs,key=lambda x:x.t):
        if e.frame_id!=last:out.append(e);last=e.frame_id
    return out


def drain(q:queue.Queue[Ev],out:list[Ev]):
    while True:
        try:out.append(q.get_nowait())
        except queue.Empty:return


def lag(src:int,rx:int,mod:int)->int:
    half=mod//2;d=((src-rx+half)%mod)-half
    return max(0,d) if d>=-2 else mod+d


def annotated(xs:list[Ev],src:list[Ev],mod:int):
    if not src:return []
    out=[]
    for e in uniq(xs):
        s=min(src,key=lambda z:abs(z.t-e.t)).frame_id
        out.append({**asdict(e),"source_frame_id":s,"lag_frames":lag(s,e.frame_id,mod)})
    return out


def steps(ids,mod):return [((b-a)%mod) for a,b in zip(ids,ids[1:])]


def metrics(name,t0,xs,src,mod,fps,native2):
    a=annotated(xs,src,mod)
    if not a:return {"name":name,"ok":False,"reason":"no frames"}
    first=a[0]; ready=next((e for e in a if e["lag_frames"]<=2),None)
    if not ready:return {"name":name,"ok":False,"reason":"no live-edge","first_frame_ms":round((first["t"]-t0)*1000,3),"first_frame_lag_frames":first["lag_frames"]}
    ev=uniq(xs); rt=ready["t"]
    if native2:chosen=[e for e in ev if e.t>=rt][:6]
    else:
        chosen=[]
        for i in range(6):
            target=rt+i*.5;c=[e for e in ev if abs(e.t-target)<=.13]
            if c:chosen.append(min(c,key=lambda e:abs(e.t-target)))
        chosen=uniq(chosen)
    ids=[e.frame_id for e in chosen];st=steps(ids,mod)
    return {"name":name,"ok":len(ids)>=4,"first_frame_ms":round((first["t"]-t0)*1000,3),"first_frame_id":first["frame_id"],"first_frame_lag_frames":first["lag_frames"],"first_frame_lag_ms_est":round(first["lag_frames"]*1000/fps,3),"ready_ms":round((rt-t0)*1000,3),"ready_frame_id":ready["frame_id"],"ready_lag_frames":ready["lag_frames"],"frames_before_ready":sum(1 for e in ev if e.t<rt),"post_ready_2fps_frame_ids":ids,"post_ready_source_frame_steps":st,"physical_sampling_ok":len(ids)>=4 and all(x in (7,8) for x in st),"event_count":len(ev)}


def run_once(n,pub,rd,w,h,fps,mod,duration,outdir):
    rd.get("about:blank");time.sleep(.2);q=queue.Queue();f=FF("rtsp://127.0.0.1:8554/benchmark/live",w,h,q);f.start()
    while f.t0 is None:time.sleep(.001)
    t0a=f.t0;t0b=time.monotonic();rd.get("http://127.0.0.1:8889/benchmark/live?controls=false&muted=true&autoplay=true")
    src=[];we=[];fe=[];ls=lw=None;end=time.monotonic()+duration
    while time.monotonic()<end:
        now=time.monotonic();s=marker(pub)
        if s and s["id"]!=ls:src.append(Ev(now,int(s["id"]),"publisher",float(s["currentTime"])));ls=int(s["id"])
        now=time.monotonic();r=marker(rd)
        if r and r["id"]!=lw:we.append(Ev(now,int(r["id"]),"webrtc_whep",float(r["currentTime"])));lw=int(r["id"])
        drain(q,fe);time.sleep(.035)
    drain(q,fe);f.stop();f.join(3);drain(q,fe)
    A=metrics("rtsp_ffmpeg",t0a,fe,src,mod,fps,True);B=metrics("webrtc_whep",t0b,we,src,mod,fps,False)
    res={"repeat":n,"start_skew_ms":round((t0b-t0a)*1000,3),"rtsp_ffmpeg":A,"webrtc_whep":B}
    if A.get("ok") and B.get("ok"):res["delta_ready_ms_webrtc_minus_rtsp"]=round(B["ready_ms"]-A["ready_ms"],3)
    d=outdir/f"repeat_{n}";d.mkdir(parents=True,exist_ok=True);(d/"metrics.json").write_text(json.dumps(res,indent=2),encoding="utf-8");(d/"ffmpeg_stderr.log").write_text(f.stderr,encoding="utf-8")
    for name,evs in [("publisher",src),("rtsp_ffmpeg",fe),("webrtc_whep",we)]:
        (d/f"{name}_events.json").write_text(json.dumps([asdict(e) for e in evs],indent=2),encoding="utf-8")
    print(json.dumps(res));return res


def aggregate(rs):
    out={"repeat_count":len(rs)}
    for k in ["rtsp_ffmpeg","webrtc_whep"]:
        good=[r[k] for r in rs if r[k].get("ok")];out[k]={"successful_repeats":len(good),"ready_ms_median":round(statistics.median([x["ready_ms"] for x in good]),3) if good else None,"first_frame_ms_median":round(statistics.median([x["first_frame_ms"] for x in good]),3) if good else None,"first_frame_lag_ms_est_median":round(statistics.median([x["first_frame_lag_ms_est"] for x in good]),3) if good else None,"physical_sampling_ok_all":bool(good) and all(x["physical_sampling_ok"] for x in good)}
    ds=[r["delta_ready_ms_webrtc_minus_rtsp"] for r in rs if "delta_ready_ms_webrtc_minus_rtsp" in r]
    if ds:out["ready_delta_webrtc_minus_rtsp_ms_median"]=round(statistics.median(ds),3);out["webrtc_ready_faster_in_repeats"]=sum(d<0 for d in ds)
    out["all_repeats_valid"]=all(r["rtsp_ffmpeg"].get("ok") and r["webrtc_whep"].get("ok") for r in rs);return out


def main():
    a=argparse.ArgumentParser();a.add_argument("--fake-video",type=Path,required=True);a.add_argument("--output-dir",type=Path,required=True);a.add_argument("--repeats",type=int,default=3);a.add_argument("--duration",type=float,default=10);a.add_argument("--width",type=int,default=1920);a.add_argument("--height",type=int,default=1080);a.add_argument("--source-fps",type=int,default=15);a.add_argument("--modulus",type=int,default=180);x=a.parse_args();x.output_dir.mkdir(parents=True,exist_ok=True)
    pub,rd=driver(x.fake_video),driver();rs=[]
    try:
        start_publisher(pub,"http://127.0.0.1:8889/benchmark/live",x.width,x.height,x.source_fps)
        for i in range(1,x.repeats+1):rs.append(run_once(i,pub,rd,x.width,x.height,x.source_fps,x.modulus,x.duration,x.output_dir));time.sleep(.5)
    finally:
        for d in [rd,pub]:
            try:d.quit()
            except Exception:pass
    s={"source":{"width":x.width,"height":x.height,"fps":x.source_fps,"marker_modulus":x.modulus},"paths":{"A":"Browser WebRTC -> MediaMTX -> RTSP -> FFmpeg(fps=2)","B":"Browser WebRTC -> MediaMTX -> WebRTC/WHEP -> Chrome decoder -> logical 2fps"},"repeats":rs,"aggregate":aggregate(rs)};(x.output_dir/"summary.json").write_text(json.dumps(s,indent=2),encoding="utf-8");print(json.dumps(s["aggregate"],indent=2));return 0 if s["aggregate"]["all_repeats_valid"] else 2

if __name__=="__main__":sys.exit(main())
