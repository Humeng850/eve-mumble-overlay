"""抓取系统范围的 OutputDebugString（DebugView 那套 DBWIN 机制）。

用途：Mumble 的 mumble_ol*.dll 用 ods() 输出诊断信息，其中包含
  ExcludeCheck: ... Overlay disabled.        <- 排除逻辑把关掉了
  ExcludeCheck: ... Overlay enabled.
  DXGI: Injecting Present                    <- 挂钩成功
  D3D11: Successfully found Present offset
只要抓到这里的内容，就能判定"原生 overlay 在 EVE 里到底卡在哪一环"。

用法：
    python tools/ods_capture.py --selftest        自检（会自己发一条消息验证能收到）
    python tools/ods_capture.py --seconds 60      抓 60 秒，输出到屏幕和 ods.log
    python tools/ods_capture.py --seconds 60 --filter ExcludeCheck

注意：同一时间只能有一个监听者（DebugView 开着就会抢不到）。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PAGE_READWRITE = 0x04
FILE_MAP_READ = 0x0004
WAIT_OBJECT_0 = 0
INFINITE = 0xFFFFFFFF
BUF_SIZE = 4096

k32.CreateFileMappingW.restype = wt.HANDLE
k32.CreateFileMappingW.argtypes = [
    wt.HANDLE, wt.LPVOID, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPCWSTR,
]
k32.MapViewOfFile.restype = ctypes.c_void_p
k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
k32.CreateEventW.restype = wt.HANDLE
k32.CreateEventW.argtypes = [wt.LPVOID, wt.BOOL, wt.BOOL, wt.LPCWSTR]
k32.SetEvent.argtypes = [wt.HANDLE]
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.GetCurrentProcessId.restype = wt.DWORD


class Listener:
    """DBWIN_BUFFER 协议：我们 SetEvent(READY)，对方写完后 SetEvent(DATA_READY)。"""

    def __init__(self):
        self.hmap = k32.CreateFileMappingW(
            ctypes.c_void_p(-1), None, PAGE_READWRITE, 0, BUF_SIZE, "DBWIN_BUFFER"
        )
        if not self.hmap:
            raise OSError(ctypes.get_last_error(), "CreateFileMapping(DBWIN_BUFFER) 失败（可能已有监听者）")
        self.ptr = k32.MapViewOfFile(self.hmap, FILE_MAP_READ, 0, 0, BUF_SIZE)
        if not self.ptr:
            raise OSError(ctypes.get_last_error(), "MapViewOfFile 失败")
        self.evt_ready = k32.CreateEventW(None, False, False, "DBWIN_BUFFER_READY")
        self.evt_data = k32.CreateEventW(None, False, False, "DBWIN_DATA_READY")
        if not self.evt_ready or not self.evt_data:
            raise OSError(ctypes.get_last_error(), "CreateEvent 失败")

    def read(self, timeout_ms):
        k32.SetEvent(self.evt_ready)
        rc = k32.WaitForSingleObject(self.evt_data, timeout_ms)
        if rc != WAIT_OBJECT_0:
            return None
        head = ctypes.string_at(self.ptr, 4)
        pid = int.from_bytes(head, "little")
        text = ctypes.string_at(self.ptr + 4, BUF_SIZE - 4).split(b"\x00", 1)[0]
        return pid, text.decode("mbcs", "replace")

    def close(self):
        if self.ptr:
            k32.UnmapViewOfFile(self.ptr)
            self.ptr = None
        for h in (self.hmap, self.evt_ready, self.evt_data):
            if h:
                k32.CloseHandle(h)


def emit(text):
    """让当前进程发一条 OutputDebugString（自检用）。"""
    k32.OutputDebugStringW.argtypes = [wt.LPCWSTR]
    k32.OutputDebugStringW(text)


def selftest():
    import threading

    listener = Listener()
    marker = "[ODS-SELFTEST] hello from pid %d" % os.getpid()

    def burst():
        # 本机有程序在疯狂刷 OutputDebugString，单条消息会被覆盖，所以连发一串
        for _ in range(40):
            emit(marker)
            time.sleep(0.05)

    th = threading.Thread(target=burst, daemon=True)
    th.start()
    try:
        deadline = time.time() + 8
        seen = 0
        while time.time() < deadline:
            got = listener.read(300)
            if not got:
                continue
            seen += 1
            pid, text = got
            if "ODS-SELFTEST" in text:
                print("收到自己发的消息: pid=%d %s" % (pid, text))
                print("[OK] 监听器工作正常（期间还收到 %d 条其他程序的调试输出）" % (seen - 1))
                return 0
        print("[FAIL] 8 秒内没收到自己发的调试输出（共收到 %d 条其他消息）" % seen)
        return 1
    finally:
        listener.close()


def capture(seconds, pattern=None, out_path="ods.log"):
    listener = Listener()
    started = time.time()
    hits = 0
    total = 0
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("# ODS capture start %s\n" % time.ctime())
            while time.time() - started < seconds:
                got = listener.read(300)
                if not got:
                    continue
                pid, text = got
                total += 1
                line = "[%d] %s" % (pid, text)
                if pattern and pattern.lower() not in text.lower():
                    continue
                hits += 1
                print(line, flush=True)
                f.write(line + "\n")
                f.flush()
            f.write("# total %d lines, matched %d\n" % (total, hits))
    finally:
        listener.close()
    print("\n抓取结束：共 %d 条，匹配 %d 条，已写入 %s" % (total, hits, out_path))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--filter", default=None)
    ap.add_argument("--out", default="ods.log")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    print("开始抓取 %g 秒… 现在去重启一个 EVE 客户端（overlay DLL 的日志在进程启动时输出）" % args.seconds)
    return capture(args.seconds, args.filter, args.out)


if __name__ == "__main__":
    sys.exit(main())
