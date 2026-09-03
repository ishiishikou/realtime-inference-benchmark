#!/usr/bin/env python3
"""Generate a looping Y4M fake-camera source with a compression-resistant frame marker."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


BLOCK = 32
GAP = 8
START_X = 16
START_Y = 16
BITS = 16  # low 8 bits: frame id, high 8 bits: bitwise inverse


def paint_marker(y_plane: np.ndarray, frame_id: int) -> None:
    marker = (frame_id & 0xFF) | (((~frame_id) & 0xFF) << 8)
    for bit in range(BITS):
        x0 = START_X + bit * (BLOCK + GAP)
        value = 235 if ((marker >> bit) & 1) else 16
        y_plane[START_Y : START_Y + BLOCK, x0 : x0 + BLOCK] = value


def generate(path: Path, width: int, height: int, fps: int, frames: int) -> None:
    if width % 2 or height % 2:
        raise ValueError("width and height must be even for YUV420p")
    if frames > 256:
        raise ValueError("frame marker uses an 8-bit id; frames must be <= 256")
    marker_width = START_X + BITS * (BLOCK + GAP)
    if marker_width >= width:
        raise ValueError(f"width {width} is too small for marker width {marker_width}")

    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.full((height, width), 96, dtype=np.uint8)
    u = np.full((height // 2, width // 2), 128, dtype=np.uint8)
    v = np.full((height // 2, width // 2), 128, dtype=np.uint8)

    # Add broad fixed structures so the stream is not an almost-empty synthetic picture.
    y[height // 3 : height // 3 + 8, :] = 150
    y[2 * height // 3 : 2 * height // 3 + 8, :] = 48

    with path.open("wb") as fh:
        fh.write(f"YUV4MPEG2 W{width} H{height} F{fps}:1 Ip A1:1 C420jpeg\n".encode("ascii"))
        for frame_id in range(frames):
            frame_y = y.copy()
            paint_marker(frame_y, frame_id)
            # A moving bar provides visible motion and avoids a fully static payload.
            bar_x = (frame_id * 23) % max(1, width - 160)
            frame_y[height // 2 : height // 2 + 48, bar_x : bar_x + 160] = 190
            fh.write(b"FRAME\n")
            fh.write(frame_y.tobytes())
            fh.write(u.tobytes())
            fh.write(v.tobytes())

    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"generated={path} width={width} height={height} fps={fps} frames={frames} size_mb={size_mb:.1f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--frames", type=int, default=120)
    args = ap.parse_args()
    generate(args.output, args.width, args.height, args.fps, args.frames)


if __name__ == "__main__":
    main()
