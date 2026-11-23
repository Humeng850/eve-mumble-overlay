"""阶段1：验证 Mumble 覆盖层命名管道协议。

连接 \\\\.\\pipe\\MumbleOverlayPipe，按 overlay/overlay.h (v1.5.915) 的协议
发送 INIT，接收 SHMEM/BLIT，并把 Mumble 渲染好的 ARGB 位图 dump 成 BMP。

用法：python probe_pipe.py [宽] [高] [秒数]
"""

import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time

PIPE_NAME = r"\\.\pipe\MumbleOverlayPipe"
MAGIC = 0x00000005

MSG_INIT = 0
MSG_SHMEM = 1
MSG_BLIT = 2
MSG_ACTIVE = 3
MSG_PID = 4
MSG_FPS = 5
MSG_INTERACTIVE = 6

MSG_NAME = {
    MSG_INIT: "INIT",
    MSG_SHMEM: "SHMEM",
    MSG_BLIT: "BLIT",
    MSG_ACTIVE: "ACTIVE",
    MSG_PID: "PID",
    MSG_FPS: "FPS",
    MSG_INTERACTIVE: "INTERACTIVE",
}

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FILE_MAP_READ = 0x0004
FILE_MAP_WRITE = 0x0002

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
k32.ReadFile.restype = wt.BOOL
k32.WriteFile.argtypes = [wt.HANDLE, wt.LPCVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
k32.WriteFile.restype = wt.BOOL
k32.PeekNamedPipe.argtypes = [
    wt.HANDLE, wt.LPVOID, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
]
k32.PeekNamedPipe.restype = wt.BOOL
k32.OpenFileMappingW.restype = wt.HANDLE
k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
k32.MapViewOfFile.restype = wt.LPVOID
k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
k32.UnmapViewOfFile.argtypes = [wt.LPVOID]


def connect_pipe():
    h = k32.CreateFileW(
        PIPE_NAME,
        GENERIC_READ | GENERIC_WRITE,
        0,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if h == INVALID_HANDLE_VALUE or h is None:
        raise OSError(
            ctypes.get_last_error(),
            "无法连接 Mumble 覆盖层管道（Mumble 是否在运行、游戏内显示是否启用？）",
        )
    return h


def send_msg(h, mtype, payload=b""):
    hdr = struct.pack("<IiI", MAGIC, len(payload), mtype)
    buf = ctypes.create_string_buffer(hdr + payload, len(hdr) + len(payload))
    written = wt.DWORD(0)
    if not k32.WriteFile(h, buf, len(hdr) + len(payload), ctypes.byref(written), None):
        raise OSError(ctypes.get_last_error(), "WriteFile 失败")
    return written.value


def peek(h):
    avail = wt.DWORD(0)
    if not k32.PeekNamedPipe(h, None, 0, None, ctypes.byref(avail), None):
        return -1
    return avail.value


def read_bytes(h, n):
    buf = ctypes.create_string_buffer(n)
    got = wt.DWORD(0)
    k32.ReadFile(h, buf, n, ctypes.byref(got), None)
    return buf.raw[: got.value]


# --- 共享内存名兜底：Qt 的 QSharedMemory 会改写名字，必要时枚举 BaseNamedObjects ---

def try_open_mapping(name, expect_size):
    candidates = [name]
    if not name.startswith("qipc_sharedmemory_"):
        candidates.append("qipc_sharedmemory_" + name)
    for cand in candidates:
        hmap = k32.OpenFileMappingW(FILE_MAP_READ | FILE_MAP_WRITE, False, cand)
        if hmap:
            return hmap, cand
    return None, None


def write_bmp(path, w, h, raw, bg=48):
    """把 BGRA 预乘位图合成到灰底后写成 BMP（便于肉眼看内容）。"""
    out = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        a = raw[i + 3]
        if a == 255:
            out[i] = raw[i]
            out[i + 1] = raw[i + 1]
            out[i + 2] = raw[i + 2]
        else:
            inv = 255 - a
            out[i] = min(255, raw[i] + (bg * inv) // 255)
            out[i + 1] = min(255, raw[i + 1] + (bg * inv) // 255)
            out[i + 2] = min(255, raw[i + 2] + (bg * inv) // 255)
        out[i + 3] = 255
    info = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, w * h * 4, 2835, 2835, 0, 0)
    head = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(out), 0, 0, 54)
    with open(path, "wb") as f:
        f.write(head + info + bytes(out))


def main():
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 1280
    height = int(sys.argv[2]) if len(sys.argv) > 2 else 720
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0

    h = connect_pipe()
    print(f"[OK] 已连接 {PIPE_NAME}")
    send_msg(h, MSG_INIT, struct.pack("<II", width, height))
    send_msg(h, MSG_PID, struct.pack("<I", os.getpid()))
    print(f"[->] INIT {width}x{height}, PID {os.getpid()}")

    buf = b""
    shm_handle = None
    shm_ptr = None
    shm_name = None
    blits = []
    counts = {}
    deadline = time.time() + seconds

    while time.time() < deadline:
        avail = peek(h)
        if avail < 0:
            print("[!!] 管道已断开")
            break
        if avail == 0:
            time.sleep(0.01)
            continue
        buf += read_bytes(h, avail)
        while True:
            if len(buf) < 12:
                break
            magic, length, mtype = struct.unpack("<IiI", buf[:12])
            if magic != MAGIC or length < 0 or length > 2048:
                print(f"[!!] 协议不匹配 magic=0x{magic:X} len={length} type={mtype}")
                return 1
            if len(buf) < 12 + length:
                break
            payload = buf[12 : 12 + length]
            buf = buf[12 + length :]
            counts[MSG_NAME.get(mtype, f"UNKNOWN{mtype}")] = (
                counts.get(MSG_NAME.get(mtype, f"UNKNOWN{mtype}"), 0) + 1
            )
            if mtype == MSG_SHMEM:
                shm_name = payload.split(b"\x00")[0].decode("utf-8", "replace")
                print(f"[<-] SHMEM 名字 = {shm_name!r}")
                hmap, used = try_open_mapping(shm_name, width * height * 4)
                if hmap:
                    shm_handle = hmap
                    shm_ptr = k32.MapViewOfFile(hmap, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, 0)
                    print(f"[OK] 共享内存已打开（名字 {used!r}），映射地址 0x{shm_ptr:X}")
                else:
                    print("[!!] 共享内存打不开，Qt 改名了，需要枚举 BaseNamedObjects")
            elif mtype == MSG_BLIT:
                blits.append(struct.unpack("<IIII", payload))
            elif mtype == MSG_ACTIVE:
                x, y, w, hh = struct.unpack("<IIII", payload)
                print(f"[<-] ACTIVE 有效区域 = ({x},{y}) {w}x{hh}")
            elif mtype == MSG_FPS:
                print(f"[<-] FPS = {struct.unpack('<f', payload)[0]:.2f}")

    print(f"[统计] {counts}")
    print(f"[统计] BLIT {len(blits)} 次，前 5 个：{blits[:5]}")

    if shm_ptr:
        size = width * height * 4
        raw = ctypes.string_at(shm_ptr, size)
        nonzero = sum(1 for i in range(3, size, 4) if raw[i] != 0)
        print(f"[统计] 位图非透明像素 = {nonzero} / {width*height} ({nonzero/(width*height)*100:.2f}%)")
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "overlay_dump.bmp")
        write_bmp(out_path, width, height, raw)
        print(f"[OK] 已导出 {out_path}")
        k32.UnmapViewOfFile(shm_ptr)
    else:
        print("[!!] 没有拿到共享内存，无法导出位图")

    send_msg(h, MSG_INTERACTIVE, b"\x00")
    k32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
