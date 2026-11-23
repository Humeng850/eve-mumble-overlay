"""生成托盘/快捷方式用的图标：32x32 与 16x16，圆形底 + 三根白色竖条。"""

import struct
import sys


def render(size):
    px = bytearray(size * size * 4)
    r = size / 2.0 - 0.4
    c = size / 2.0 - 0.5
    for y in range(size):
        for x in range(size):
            dx = x - c
            dy = y - c
            if dx * dx + dy * dy <= r * r:
                i = (y * size + x) * 4
                px[i + 0] = 0x56
                px[i + 1] = 0x6E
                px[i + 2] = 0x0F
                px[i + 3] = 255
    bars = [(0.24, 0.36, 0.62), (0.44, 0.24, 0.76), (0.64, 0.40, 0.60)]
    bar_w = max(1, int(round(size * 0.09)))
    for fx, fy0, fy1 in bars:
        x0 = int(round(size * fx))
        y0 = int(round(size * fy0))
        y1 = int(round(size * fy1))
        for y in range(y0, y1):
            for x in range(x0, x0 + bar_w):
                if 0 <= x < size and 0 <= y < size:
                    i = (y * size + x) * 4
                    px[i + 0] = px[i + 1] = px[i + 2] = 255
                    px[i + 3] = 255
    return px


def ico_image(size):
    px = render(size)
    row = size * 4
    xor = b"".join(bytes(px[y * row:(y + 1) * row]) for y in range(size - 1, -1, -1))
    mask_row = ((size + 31) // 32) * 4
    and_mask = b"\x00" * (mask_row * size)
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, len(xor), 0, 0, 0, 0
    )
    return header + xor + and_mask


def main(path="overlay.ico"):
    sizes = [32, 16]
    images = [ico_image(s) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for size, img in zip(sizes, images):
        entries += struct.pack(
            "<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0,
            0, 0, 1, 32, len(img), offset
        )
        offset += len(img)
    with open(path, "wb") as f:
        f.write(out + entries + b"".join(images))
    print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "overlay.ico")
