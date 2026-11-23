"""开发自测用：从 32 位 BMP 里裁一块出来，便于核对浮层区域。"""

import struct
import sys


def read_bmp(path):
    with open(path, "rb") as f:
        data = f.read()
    off = struct.unpack("<I", data[10:14])[0]
    w, h = struct.unpack("<ii", data[18:26])
    return w, abs(h), data[off:]


def write_bmp(path, w, h, raw, scale=1):
    out = bytearray()
    for y in range(0, h, scale):
        for x in range(0, w, scale):
            i = (y * w + x) * 4
            out += raw[i : i + 4]
    ow, oh = w // scale, h // scale
    info = struct.pack("<IiiHHIIiiII", 40, ow, -oh, 1, 32, 0, ow * oh * 4, 2835, 2835, 0, 0)
    head = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(out), 0, 0, 54)
    with open(path, "wb") as f:
        f.write(head + info + bytes(out))


def main():
    src, dst = sys.argv[1], sys.argv[2]
    x, y, w, h = (int(v) for v in sys.argv[3:7])
    scale = int(sys.argv[7]) if len(sys.argv) > 7 else 1
    W, H, raw = read_bmp(src)
    crop = bytearray()
    for row in range(y, min(y + h, H)):
        start = (row * W + x) * 4
        crop += raw[start : start + w * 4]
    write_bmp(dst, w, h, crop, scale)
    print(f"{dst} {w}x{h} scale={scale}")


if __name__ == "__main__":
    main()
