#!/usr/bin/env python3
"""Generate the same physical frame marker used by commercialization validation."""
from __future__ import annotations

import argparse
from pathlib import Path

SIZE = 50
GAP = 10
X0 = 20
Y0 = 20


def paint_rect(plane: bytearray, width: int, x: int, y: int, w: int, h: int, value: int) -> None:
    row = bytes([value]) * w
    for yy in range(y, y + h):
        off = yy * width + x
        plane[off:off + w] = row


def draw_marker(plane: bytearray, width: int, frame_id: int) -> None:
    # 4-bit sync 1010 + 12-bit sourceFrameId, matching the validated decoder.
    bits = [1, 0, 1, 0]
    bits.extend((frame_id >> bit) & 1 for bit in range(11, -1, -1))
    for i, bit in enumerate(bits):
        paint_rect(plane, width, X0 + i * (SIZE + GAP), Y0, SIZE, SIZE, 235 if bit else 16)


def make_frame(width: int, height: int, frame_id: int) -> bytes:
    y_value = 48 + ((frame_id * 11) % 144)
    y = bytearray([y_value]) * (width * height)
    box_w = 300
    box_h = 300
    x = 40 + ((frame_id * 37) % max(1, width - box_w - 80))
    y_pos = 220 + ((frame_id * 61) % max(1, height - box_h - 260))
    paint_rect(y, width, x, y_pos, box_w, box_h, 220 if y_value < 128 else 32)
    draw_marker(y, width, frame_id)
    chroma_size = (width // 2) * (height // 2)
    return bytes(y) + bytes([128]) * chroma_size + bytes([128]) * chroma_size


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--width', type=int, default=1080)
    p.add_argument('--height', type=int, default=1920)
    p.add_argument('--fps', type=int, default=15)
    p.add_argument('--frames', type=int, default=300)
    a = p.parse_args()
    if a.width % 2 or a.height % 2:
        raise SystemExit('YUV420 requires even width and height')
    if not 1 <= a.frames <= 4095:
        raise SystemExit('12-bit marker supports 1..4095 frames')
    if X0 + 16 * (SIZE + GAP) >= a.width:
        raise SystemExit('frame width is too small for marker')

    a.output.parent.mkdir(parents=True, exist_ok=True)
    header = f'YUV4MPEG2 W{a.width} H{a.height} F{a.fps}:1 Ip A1:1 C420jpeg\n'.encode('ascii')
    with a.output.open('wb') as f:
        f.write(header)
        for frame_id in range(1, a.frames + 1):
            f.write(b'FRAME\n')
            f.write(make_frame(a.width, a.height, frame_id))
    print(f'generated={a.output} width={a.width} height={a.height} fps={a.fps} frames={a.frames} size_mb={a.output.stat().st_size/(1024*1024):.1f}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
