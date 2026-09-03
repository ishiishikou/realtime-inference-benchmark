#!/usr/bin/env python3
from __future__ import annotations

import argparse, json, queue, shutil, statistics, subprocess, sys, threading, time, urllib.parse, urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

ROOT = Path(__file__).resolve().parent
BROWSER_DIR = ROOT / 'browser'
SIZE, GAP, X0, Y0, BLOCKS = 50, 10, 20, 20, 16
CROP_W = X0 + BLOCKS * (SIZE + GAP)
CROP_H = Y0 + SIZE + 10
SYNC = (1, 0, 1, 0)

MARKER_JS = f'''(() => {{
 const v=document.querySelector('video');
 if(!v || v.readyState<2 || !v.videoWidth || !v.videoHeight) return null;
 if(!window.__mc){{window.__mc=document.createElement('canvas');__mc.width={CROP_W};__mc.height={CROP_H};window.__mx=__mc.getContext('2d',{{willReadFrequently:true}});}}
 if(v.videoWidth<__mc.width || v.videoHeight<__mc.height) return {{error:'marker-outside-video',width:v.videoWidth,height:v.videoHeight}};
 __mx.drawImage(v,0,0,__mc.width,__mc.height,0,0,__mc.width,__mc.height);
 const d=__mx.getImageData(0,0,__mc.width,__mc.height).data,bits=[],samples=[];
 for(let i=0;i<{BLOCKS};i++){{
  const cx={X0}+i*({SIZE}+{GAP})+{SIZE//2},cy={Y0}+{SIZE//2}; let s=0,n=0;
  for(const dy of [-8,0,8]) for(const dx of [-8,0,8]){{const o=((cy+dy)*__mc.width+cx+dx)*4;s+=(d[o]+d[o+1]+d[o+2])/3;n++;}}
  const m=s/n;samples.push(m);bits.push(m>=128?1:0);
 }}
 if(bits[0]!==1||bits[1]!==0||bits[2]!==1||bits[3]!==0) return {{invalid:true,bits,samples,currentTime:v.currentTime,width:v.videoWidth,height:v.videoHeight}};
 let id=0;for(let i=4;i<16;i++)id=(id<<1)|bits[i];
 return {{id,currentTime:v.currentTime,width:v.videoWidth,height:v.videoHeight}};
}})()'''

@dataclass
class Ev:
    t: float
    frame_id: int
    source: str
    media_time: float | None = None


def decode_marker(frame: bytes, width: int, height: int) -> int | None:
    bits=[]
    for i in range(BLOCKS):
        cx=X0+i*(SIZE+GAP)+SIZE//2; cy=Y0+SIZE//2
        if cx>=width or cy>=height:return None
        vals=[]
        for dy in (-8,0,8):
            for dx in (-8,0,8):
                off=((cy+dy)*width+cx+dx)*3
                b,g,r=frame[off:off+3];vals.append((int(b)+int(g)+int(r))/3)
        bits.append(1 if sum(vals)/len(vals)>=128 else 0)
    if tuple(bits[:4])!=SYNC:return None
    fid=0
    for bit in bits[4:]:fid=(fid<<1)|bit
    return fid


def read_exact(pipe,size,stop):
    data=bytearray()
    while len(data)<size and not stop.is_set():
        chunk=pipe.read(size-len(data))
        if not chunk:return None
        data.extend(chunk)
    return bytes(data) if len(data)==size else None


class FF(threading.Thread):
    def __init__(self,url,w,h,q):
        super().__init__(daemon=True);self.url=url;self.w=w;self.h=h;self.q=q
        self.proc=None;self.t0=None;self.stderr=[];self.failures=0;self.cmd=[];self.stop_event=threading.Event()
    def drain_stderr(self):
        for raw in iter(self.proc.stderr.readline,b''):
            s=raw.decode(errors='replace').rstrip()
            if s:self.stderr.append(s);self.stderr=self.stderr[-200:]
    def run(self):
        vf=f'fps=2,scale={self.w}:{self.h}:flags=fast_bilinear,format=bgr24'
        self.cmd=['ffmpeg','-hide_banner','-loglevel','warning','-rtsp_transport','tcp','-i',self.url,'-map','0:v:0','-an','-vf',vf,'-f','rawvideo','-pix_fmt','bgr24','pipe:1']
        self.t0=time.monotonic();self.proc=subprocess.Popen(self.cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        threading.Thread(target=self.drain_stderr,daemon=True).start();size=self.w*self.h*3
        while not self.stop_event.is_set():
            raw=read_exact(self.proc.stdout,size,self.stop_event)
            if raw is None:break
            fid=decode_marker(raw,self.w,self.h)
            if fid is None:self.failures+=1
            else:self.q.put(Ev(time.monotonic(),fid,'rtsp_ffmpeg'))
    def stop(self):
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(2)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(2)


def driver(fake=None):
    chrome=shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium')
    if not chrome:raise RuntimeError('Chrome not found')
    o=Options();o.binary_location=chrome
    for a in ['--headless=new','--no-sandbox','--disable-dev-shm-usage','--autoplay-policy=no-user-gesture-required','--disable-features=WebRtcHideLocalIpsWithMdns','--window-size=1280,2100']:o.add_argument(a)
    if fake:
        o.add_argument('--use-fake-ui-for-media-stream');o.add_argument('--use-fake-device-for-media-stream');o.add_argument(f'--use-file-for-fake-video-capture={Path(fake).resolve()}')
    dp=shutil.which('chromedriver');d=webdriver.Chrome(service=Service(dp) if dp else Service(),options=o);d.set_page_load_timeout(15);return d


def marker(d):
    try:r=d.execute_script(f'return {MARKER_JS};')
    except Exception:return None
    return r if isinstance(r,dict) else None


def video_meta(d):
    try:return d.execute_script("const v=document.querySelector('video');return v?{currentTime:v.currentTime,width:v.videoWidth,height:v.videoHeight,readyState:v.readyState,paused:v.paused}:null")
    except Exception:return None


def wait_probe(d,key,timeout):
    end=time.monotonic()+timeout;last=None
    while time.monotonic()<end:
        last=d.execute_script('return window.__probe')
        if last and last.get('error'):raise RuntimeError(last['error'])
        if last and last.get(key):return last
        time.sleep(.05)
    raise TimeoutError(f'publisher timeout {key}: {last}')


def wait_path(name,timeout=10):
    end=time.monotonic()+timeout;last=''
    while time.monotonic()<end:
        try:
            with urllib.request.urlopen('http://127.0.0.1:9997/v3/paths/list',timeout=1) as r:last=r.read().decode(errors='replace')
            if name in last:return
        except Exception as e:last=repr(e)
        time.sleep(.1)
    raise RuntimeError(f'path not ready {name}: {last[:500]}')


def wait_marker(d,timeout,label):
    end=time.monotonic()+timeout;last=None
    while time.monotonic()<end:
        last=marker(d)
        if last and isinstance(last.get('id'),int):return last
        time.sleep(.05)
    raise RuntimeError(f'{label} marker not visible; marker={last}; video={video_meta(d)}')


def uniq(xs):
    out=[];last=None
    for e in sorted(xs,key=lambda x:x.t):
        if e.frame_id!=last:out.append(e);last=e.frame_id
    return out


def drain(q,out):
    while True:
        try:out.append(q.get_nowait())
        except queue.Empty:return


def step(a,b,mod):return (b-a)%mod

def signed_lag(ref,rx,mod):
    d=(ref-rx)%mod
    if d>mod//2:d-=mod
    return d


def annotated(xs,ref,mod):
    if not ref:return []
    rows=[]
    for e in uniq(xs):
        r=min(ref,key=lambda z:abs(z.t-e.t));s=signed_lag(r.frame_id,e.frame_id,mod)
        rows.append({**asdict(e),'reference_frame_id':r.frame_id,'signed_lag_frames':s,'lag_frames':max(0,s)})
    return rows


def logical_2fps(xs,ready_at):
    xs=uniq(xs);chosen=[]
    for i in range(6):
        target=ready_at+i*.5;c=[e for e in xs if abs(e.t-target)<=.16]
        if c:chosen.append(min(c,key=lambda e:abs(e.t-target)))
    return uniq(chosen)


def metrics(name,t0,xs,ref,mod,fps,native2,failures):
    a=annotated(xs,ref,mod)
    if not a:return {'name':name,'ok':False,'reason':'no physical frames','marker_failures':failures}
    first=a[0];ready=next((x for x in a if x['lag_frames']<=2),None)
    base={'name':name,'first_frame_ms':round((first['t']-t0)*1000,3),'first_frame_id':first['frame_id'],'first_frame_lag_frames':first['lag_frames'],'first_frame_lag_ms_est':round(first['lag_frames']*1000/fps,3),'marker_failures':failures,'event_count':len(uniq(xs))}
    if not ready:return {**base,'ok':False,'reason':'did not reach live edge'}
    rt=ready['t'];sel=[e for e in uniq(xs) if e.t>=rt][:6] if native2 else logical_2fps(xs,rt);ids=[e.frame_id for e in sel];steps=[step(a,b,mod) for a,b in zip(ids,ids[1:])]
    return {**base,'ok':len(ids)>=4,'ready_ms':round((rt-t0)*1000,3),'ready_frame_id':ready['frame_id'],'ready_reference_frame_id':ready['reference_frame_id'],'ready_lag_frames':ready['lag_frames'],'frames_before_ready':sum(e.t<rt for e in uniq(xs)),'post_ready_2fps_frame_ids':ids,'post_ready_source_frame_steps':steps,'physical_sampling_ok':len(ids)>=4 and len(ids)==len(set(ids)) and all(s in (7,8) for s in steps)}


def ref_quality(xs,mod):
    ids=[e.frame_id for e in uniq(xs)];steps=[step(a,b,mod) for a,b in zip(ids,ids[1:])]
    return {'event_count':len(ids),'source_frame_ids_head':ids[:12],'max_step':max(steps) if steps else None,'usable':len(ids)>=20 and bool(steps) and max(steps)<=3}


def run_once(n,refd,cand,rtsp_url,reader_url,w,h,fps,mod,duration,outdir):
    cand.get('about:blank');time.sleep(.2);q=queue.Queue();f=FF(rtsp_url,w,h,q);f.start()
    while f.t0 is None:time.sleep(.001)
    t0a=f.t0;t0b=time.monotonic();cand.get(reader_url)
    refs=[];we=[];fe=[];lr=lw=None;rfail=wfail=0;end=time.monotonic()+duration
    while time.monotonic()<end:
        now=time.monotonic();r=marker(refd)
        if r and isinstance(r.get('id'),int):
            fid=int(r['id'])
            if fid!=lr:refs.append(Ev(now,fid,'reference_whep',float(r['currentTime'])));lr=fid
        elif r and r.get('invalid'):rfail+=1
        now=time.monotonic();x=marker(cand)
        if x and isinstance(x.get('id'),int):
            fid=int(x['id'])
            if fid!=lw:we.append(Ev(now,fid,'webrtc_whep',float(x['currentTime'])));lw=fid
        elif x and x.get('invalid'):wfail+=1
        drain(q,fe);time.sleep(.03)
    drain(q,fe);f.stop();f.join(3);drain(q,fe)
    rq=ref_quality(refs,mod);A=metrics('rtsp_ffmpeg',t0a,fe,refs,mod,fps,True,f.failures);B=metrics('webrtc_whep',t0b,we,refs,mod,fps,False,wfail)
    if not rq['usable']:
        A.update(ok=False,reason='steady-state reference unusable');B.update(ok=False,reason='steady-state reference unusable')
    res={'repeat':n,'start_skew_ms':round((t0b-t0a)*1000,3),'reference':{**rq,'marker_failures':rfail},'rtsp_ffmpeg':A,'webrtc_whep':B}
    if A.get('ok') and B.get('ok'):res['delta_ready_ms_webrtc_minus_rtsp']=round(B['ready_ms']-A['ready_ms'],3)
    d=outdir/f'repeat_{n}';d.mkdir(parents=True,exist_ok=True)
    (d/'metrics.json').write_text(json.dumps(res,indent=2)+'\n');(d/'ffmpeg_stderr.log').write_text('\n'.join(f.stderr)+'\n');(d/'ffmpeg_command.json').write_text(json.dumps(f.cmd,indent=2)+'\n');(d/'candidate_video_meta.json').write_text(json.dumps(video_meta(cand),indent=2)+'\n')
    for name,evs in [('reference',refs),('rtsp_ffmpeg',fe),('webrtc_whep',we)]:(d/f'{name}_events.json').write_text(json.dumps([asdict(e) for e in evs],indent=2)+'\n')
    print(json.dumps(res),flush=True);return res


def aggregate(rs):
    out={'repeat_count':len(rs)}
    for k in ['rtsp_ffmpeg','webrtc_whep']:
        good=[r[k] for r in rs if r[k].get('ok')]
        out[k]={'successful_repeats':len(good),'ready_ms_median':round(statistics.median(x['ready_ms'] for x in good),3) if good else None,'first_frame_ms_median':round(statistics.median(x['first_frame_ms'] for x in good),3) if good else None,'first_frame_lag_ms_est_median':round(statistics.median(x['first_frame_lag_ms_est'] for x in good),3) if good else None,'physical_sampling_ok_all':bool(good) and all(x['physical_sampling_ok'] for x in good)}
    ds=[r['delta_ready_ms_webrtc_minus_rtsp'] for r in rs if 'delta_ready_ms_webrtc_minus_rtsp' in r]
    if ds:out['ready_delta_webrtc_minus_rtsp_ms_median']=round(statistics.median(ds),3);out['webrtc_ready_faster_in_repeats']=sum(d<0 for d in ds)
    out['all_repeats_valid']=all(r['reference'].get('usable') and r['rtsp_ffmpeg'].get('ok') and r['webrtc_whep'].get('ok') for r in rs);return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--fake-video',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--repeats',type=int,default=3);p.add_argument('--duration',type=float,default=8);p.add_argument('--width',type=int,default=1080);p.add_argument('--height',type=int,default=1920);p.add_argument('--source-fps',type=int,default=15);p.add_argument('--modulus',type=int,default=300);p.add_argument('--http-port',type=int,default=18080);p.add_argument('--path',default='benchmark/live');a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
    pub=driver(a.fake_video);refd=driver();cand=driver();http=None;rs=[]
    pathq=urllib.parse.quote(a.path,safe='');pub_ms=int((a.repeats*a.duration+30)*1000);pub_url=f'http://127.0.0.1:{a.http_port}/commercial_camera.html?fps={a.source_fps}&durationMs={pub_ms}&path={pathq}';reader=f'http://127.0.0.1:8889/{a.path}?controls=false&muted=true&autoplay=true';rtsp=f'rtsp://127.0.0.1:8554/{a.path}'
    try:
        http=subprocess.Popen([sys.executable,'-m','http.server',str(a.http_port),'--bind','127.0.0.1','--directory',str(BROWSER_DIR)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);time.sleep(.3)
        pub.get(pub_url);state=wait_probe(pub,'connectionEstablishedAtEpochMs',35);wait_path(a.path,8);(a.output_dir/'publisher_state.json').write_text(json.dumps(state,indent=2)+'\n')
        refd.get(reader);m1=wait_marker(refd,15,'steady-state WHEP reference');time.sleep(1);m2=wait_marker(refd,5,'steady-state WHEP reference')
        if m1['id']==m2['id']:raise RuntimeError(f'reference marker not advancing: {m1}')
        for i in range(1,a.repeats+1):rs.append(run_once(i,refd,cand,rtsp,reader,a.width,a.height,a.source_fps,a.modulus,a.duration,a.output_dir));time.sleep(.5)
    finally:
        for d in [cand,refd,pub]:
            try:d.quit()
            except Exception:pass
        if http and http.poll() is None:http.terminate();http.wait(timeout=2)
    s={'source':{'width':a.width,'height':a.height,'fps':a.source_fps,'marker':'validated 16 blocks: sync 1010 + 12-bit sourceFrameId','marker_modulus':a.modulus},'live_edge_reference':'pre-connected steady-state MediaMTX WebRTC/WHEP reader decoding the same physical sourceFrameId','paths':{'A':'Browser WHIP -> MediaMTX -> RTSP -> FFmpeg(fps=2)','B':'Browser WHIP -> MediaMTX -> WebRTC/WHEP -> Chrome decoder -> logical 2fps'},'repeats':rs,'aggregate':aggregate(rs)};(a.output_dir/'summary.json').write_text(json.dumps(s,indent=2)+'\n');print(json.dumps(s['aggregate'],indent=2));return 0 if s['aggregate']['all_repeats_valid'] else 2

if __name__=='__main__':raise SystemExit(main())
