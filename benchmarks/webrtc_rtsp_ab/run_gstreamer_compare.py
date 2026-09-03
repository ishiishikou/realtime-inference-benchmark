#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import asdict
from pathlib import Path

from run_ab_compare import (
    BROWSER_DIR,
    Ev,
    annotated,
    decode_marker,
    drain,
    driver,
    marker,
    read_exact,
    ref_quality,
    step,
    uniq,
    wait_marker,
    wait_path,
    wait_probe,
)


class GStreamerReceiver(threading.Thread):
    def __init__(self, url: str, width: int, height: int, outq: queue.Queue):
        super().__init__(daemon=True)
        self.url = url
        self.width = width
        self.height = height
        self.outq = outq
        self.proc = None
        self.t0 = None
        self.stderr: list[str] = []
        self.failures = 0
        self.stop_event = threading.Event()
        self.cmd = [
            "gst-launch-1.0",
            "-q",
            "whepsrc",
            f"whep-endpoint={url}",
            "use-link-headers=true",
            "video-caps=application/x-rtp,media=video,encoding-name=H264,payload=127,clock-rate=90000",
            "audio-caps=application/x-rtp,media=audio,encoding-name=PCMU,payload=0,clock-rate=8000",
            "!",
            "rtph264depay",
            "!",
            "h264parse",
            "!",
            "avdec_h264",
            "!",
            "queue",
            "max-size-buffers=1",
            "max-size-bytes=0",
            "max-size-time=0",
            "leaky=downstream",
            "!",
            "videoconvert",
            "!",
            "videoscale",
            "!",
            f"video/x-raw,format=BGR,width={width},height={height}",
            "!",
            "fdsink",
            "fd=1",
            "sync=false",
        ]

    def drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for raw in iter(self.proc.stderr.readline, b""):
            text = raw.decode(errors="replace").rstrip()
            if text:
                self.stderr.append(text)
                self.stderr = self.stderr[-300:]

    def run(self) -> None:
        self.t0 = time.monotonic()
        self.proc = subprocess.Popen(
            self.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(target=self.drain_stderr, daemon=True).start()
        assert self.proc.stdout
        frame_size = self.width * self.height * 3
        while not self.stop_event.is_set():
            raw = read_exact(self.proc.stdout, frame_size, self.stop_event)
            if raw is None:
                break
            frame_id = decode_marker(raw, self.width, self.height)
            if frame_id is None:
                self.failures += 1
            else:
                self.outq.put(Ev(time.monotonic(), frame_id, "webrtc_gstreamer"))

    def stop(self) -> None:
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(2)


def write_events(path: Path, events: list[Ev]) -> None:
    path.write_text(json.dumps([asdict(e) for e in events], indent=2) + "\n")


def latest_2fps(events: list[Ev], ready_at: float) -> list[Ev]:
    """Model the inference loop: every 500 ms consume the newest decoded frame."""
    events = uniq(events)
    selected: list[Ev] = []
    for i in range(6):
        target = ready_at + i * 0.5
        candidates = [event for event in events if event.t <= target + 1e-9]
        if not candidates:
            continue
        event = candidates[-1]
        if not selected or event.frame_id != selected[-1].frame_id:
            selected.append(event)
    return selected


def product_metrics(
    t0: float,
    events: list[Ev],
    refs: list[Ev],
    modulus: int,
    fps: int,
    failures: int,
) -> dict:
    """Ready = live-edge plus stable source cadence; startup burst is not monitored."""
    rows = annotated(events, refs, modulus)
    unique_events = uniq(events)
    if not rows:
        return {
            "name": "webrtc_gstreamer",
            "ok": False,
            "reason": "no physical frames",
            "marker_failures": failures,
        }

    first = rows[0]
    base = {
        "name": "webrtc_gstreamer",
        "first_frame_ms": round((first["t"] - t0) * 1000, 3),
        "first_frame_id": first["frame_id"],
        "first_frame_lag_frames": first["lag_frames"],
        "first_frame_lag_ms_est": round(first["lag_frames"] * 1000 / fps, 3),
        "marker_failures": failures,
        "event_count": len(unique_events),
    }

    # At 15 fps the nominal interval is 66.7 ms. Require four consecutive
    # intervals in a deliberately broad 40-105 ms window and stay <=2 frames
    # from the live-edge reference. This excludes the short startup burst.
    ready_index = None
    for index in range(4, len(rows)):
        intervals = [
            rows[pos]["t"] - rows[pos - 1]["t"]
            for pos in range(index - 3, index + 1)
        ]
        recent = rows[index - 3 : index + 1]
        cadence_stable = all(0.040 <= value <= 0.105 for value in intervals)
        live_edge_stable = all(item["lag_frames"] <= 2 for item in recent)
        if cadence_stable and live_edge_stable:
            ready_index = index
            break

    if ready_index is None:
        return {**base, "ok": False, "reason": "stable live edge not reached"}

    ready = rows[ready_index]
    ready_at = ready["t"]
    selected = latest_2fps(unique_events, ready_at)
    ids = [event.frame_id for event in selected]
    steps = [step(a, b, modulus) for a, b in zip(ids, ids[1:])]
    physical_sampling_ok = (
        len(ids) >= 4
        and len(ids) == len(set(ids))
        and all(value in (7, 8) for value in steps)
    )

    full_ids = [event.frame_id for event in unique_events]
    full_steps = [step(a, b, modulus) for a, b in zip(full_ids, full_ids[1:])]
    full_rate_continuous = bool(full_steps) and max(full_steps) <= 2

    return {
        **base,
        "ok": physical_sampling_ok and full_rate_continuous,
        "ready_ms": round((ready_at - t0) * 1000, 3),
        "ready_frame_id": ready["frame_id"],
        "ready_reference_frame_id": ready["reference_frame_id"],
        "ready_lag_frames": ready["lag_frames"],
        "frames_before_ready": ready_index,
        "post_ready_2fps_frame_ids": ids,
        "post_ready_source_frame_steps": steps,
        "physical_sampling_ok": physical_sampling_ok,
        "full_rate_max_source_frame_step": max(full_steps) if full_steps else None,
        "full_rate_continuous": full_rate_continuous,
    }


def run_once(
    repeat: int,
    refd,
    whep_url: str,
    width: int,
    height: int,
    fps: int,
    modulus: int,
    duration: float,
    output_dir: Path,
) -> dict:
    outq: queue.Queue = queue.Queue()
    receiver = GStreamerReceiver(whep_url, width, height, outq)
    refs: list[Ev] = []
    frames: list[Ev] = []
    last_ref = None
    ref_failures = 0

    receiver.start()
    while receiver.t0 is None:
        time.sleep(0.001)
    end = time.monotonic() + duration

    while time.monotonic() < end:
        now = time.monotonic()
        current = marker(refd)
        if current and isinstance(current.get("id"), int):
            frame_id = int(current["id"])
            if frame_id != last_ref:
                refs.append(
                    Ev(
                        now,
                        frame_id,
                        "reference_whep",
                        float(current["currentTime"]),
                    )
                )
                last_ref = frame_id
        elif current and current.get("invalid"):
            ref_failures += 1

        drain(outq, frames)
        time.sleep(0.03)

    drain(outq, frames)
    receiver.stop()
    receiver.join(3)
    drain(outq, frames)

    reference = {
        **ref_quality(refs, modulus),
        "marker_failures": ref_failures,
    }
    result = product_metrics(
        receiver.t0,
        frames,
        refs,
        modulus,
        fps,
        receiver.failures,
    )
    measurement_valid = reference["usable"] and len(uniq(frames)) > 0
    result["measurement_valid"] = measurement_valid
    if not reference["usable"]:
        result.update(ok=False, reason="steady-state reference unusable")

    repeat_dir = output_dir / f"gstreamer_repeat_{repeat}"
    repeat_dir.mkdir(parents=True, exist_ok=True)
    (repeat_dir / "metrics.json").write_text(
        json.dumps(
            {"repeat": repeat, "reference": reference, "webrtc_gstreamer": result},
            indent=2,
        )
        + "\n"
    )
    (repeat_dir / "gstreamer_command.json").write_text(
        json.dumps(receiver.cmd, indent=2) + "\n"
    )
    (repeat_dir / "gstreamer_stderr.log").write_text(
        "\n".join(receiver.stderr) + "\n"
    )
    write_events(repeat_dir / "reference_events.json", refs)
    write_events(repeat_dir / "webrtc_gstreamer_events.json", frames)

    print(
        json.dumps(
            {"repeat": repeat, "reference": reference, "webrtc_gstreamer": result}
        ),
        flush=True,
    )
    return {"repeat": repeat, "reference": reference, "webrtc_gstreamer": result}


def median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    n = len(values)
    value = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2
    return round(value, 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--duration", type=float, default=8)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--source-fps", type=int, default=15)
    parser.add_argument("--modulus", type=int, default=300)
    parser.add_argument("--http-port", type=int, default=18081)
    parser.add_argument("--path", default="benchmark/live")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    pub = driver(args.fake_video)
    refd = driver()
    http = None
    results = []
    pathq = urllib.parse.quote(args.path, safe="")
    publish_ms = int((args.repeats * args.duration + 30) * 1000)
    publisher_url = (
        f"http://127.0.0.1:{args.http_port}/commercial_camera.html"
        f"?fps={args.source_fps}&durationMs={publish_ms}&path={pathq}"
    )
    reader_url = (
        f"http://127.0.0.1:8889/{args.path}"
        "?controls=false&muted=true&autoplay=true"
    )
    whep_url = f"http://127.0.0.1:8889/{args.path}/whep"

    try:
        http = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(args.http_port),
                "--bind",
                "127.0.0.1",
                "--directory",
                str(BROWSER_DIR),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.3)
        pub.get(publisher_url)
        state = wait_probe(pub, "connectionEstablishedAtEpochMs", 35)
        wait_path(args.path, 8)
        (args.output_dir / "gstreamer_publisher_state.json").write_text(
            json.dumps(state, indent=2) + "\n"
        )

        refd.get(reader_url)
        first = wait_marker(refd, 15, "steady-state WHEP reference")
        time.sleep(1)
        second = wait_marker(refd, 5, "steady-state WHEP reference")
        if first["id"] == second["id"]:
            raise RuntimeError(f"reference marker not advancing: {first}")

        for repeat in range(1, args.repeats + 1):
            results.append(
                run_once(
                    repeat,
                    refd,
                    whep_url,
                    args.width,
                    args.height,
                    args.source_fps,
                    args.modulus,
                    args.duration,
                    args.output_dir,
                )
            )
            time.sleep(0.5)
    finally:
        for browser in (refd, pub):
            try:
                browser.quit()
            except Exception:
                pass
        if http and http.poll() is None:
            http.terminate()
            http.wait(timeout=2)

    measured = [
        item["webrtc_gstreamer"]
        for item in results
        if item["webrtc_gstreamer"].get("measurement_valid")
    ]
    successful = [item for item in measured if item.get("ok")]
    aggregate = {
        "repeat_count": len(results),
        "measurement_valid_repeats": len(measured),
        "successful_repeats": len(successful),
        "first_frame_ms_median": median([x["first_frame_ms"] for x in measured]),
        "ready_ms_median": median([x["ready_ms"] for x in successful]),
        "ready_lag_frames_median": median(
            [float(x["ready_lag_frames"]) for x in successful]
        ),
        "physical_sampling_ok_all": bool(successful)
        and all(x.get("physical_sampling_ok") for x in successful),
        "full_rate_continuous_all": bool(successful)
        and all(x.get("full_rate_continuous") for x in successful),
        "all_measurements_valid": len(measured) == args.repeats,
        "all_repeats_successful": len(successful) == args.repeats,
    }
    summary = {
        "source": {
            "width": args.width,
            "height": args.height,
            "fps": args.source_fps,
            "marker": "validated 16 blocks: sync 1010 + 12-bit sourceFrameId",
        },
        "path": (
            "Browser WHIP -> MediaMTX -> WebRTC/WHEP -> GStreamer whepsrc -> "
            "rtph264depay -> avdec_h264 -> leaky latest-frame queue -> inference-side 2fps"
        ),
        "ready_definition": (
            "within 2 source frames of live edge and 4 consecutive 15fps intervals "
            "between 40-105ms; frames before this boundary are startup-only"
        ),
        "live_edge_reference": "steady-state Chrome WHEP reader using the same physical sourceFrameId",
        "repeats": results,
        "aggregate": aggregate,
    }
    (args.output_dir / "gstreamer_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(json.dumps(aggregate, indent=2))
    return 0 if aggregate["all_repeats_successful"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
