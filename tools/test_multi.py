"""回归测试：多客户端模式。

EVE-O-Preview 会强制只保留一个可见客户端，没法用它测"同时显示多个"。
所以这里造两个合成窗口，把 find_eve_windows 换成返回它们，然后验证：
  单客户端模式 -> 1 个浮层窗口
  多客户端模式 -> 2 个浮层窗口，且分别贴在这两个窗口的客户区原点

用法：python tools/test_multi.py
"""

import ctypes
import ctypes.wintypes as wt
import importlib.util
import os
import sys
import threading
import time
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(os.path.dirname(HERE), "EveMumbleOverlay.pyw")

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_POPUP = 0x80000000
SW_SHOW = 5
CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32.EnumWindows.argtypes = [CB, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
k32.GetModuleHandleW.restype = wt.HMODULE
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


_proc = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
_wc = WNDCLASSW()
_wc.lpfnWndProc = _proc
_wc.hInstance = k32.GetModuleHandleW(None)
_wc.lpszClassName = "EveOverlayTestWindow"
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW(ctypes.byref(_wc))


def make_window(x, y, w, h):
    return user32.CreateWindowExW(
        0, "EveOverlayTestWindow", "fake eve client", WS_OVERLAPPEDWINDOW,
        x, y, w, h, None, None, _wc.hInstance, None,
    )


def pump():
    msg = wt.MSG()
    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def overlay_windows():
    rows = []

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        if buf.value.startswith("EveMumbleOverlayWnd"):
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            rows.append(
                (bool(user32.IsWindowVisible(h)), r.left, r.top, r.right - r.left, r.bottom - r.top)
            )
        return True

    user32.EnumWindows(CB(cb), 0)
    return rows


def load_module():
    spec = importlib.util.spec_from_file_location("ovl", MODULE, loader=SourceFileLoader("ovl", MODULE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Args:
    def __init__(self, multi):
        self.pid = None
        self.duration = 6.0
        self.autostart = None
        self.multi = multi
        self.single = not multi


def run_case(multi):
    mod = load_module()
    w1 = make_window(80, 80, 820, 620)
    w2 = make_window(960, 80, 820, 620)
    user32.ShowWindow(w1, SW_SHOW)
    user32.ShowWindow(w2, SW_SHOW)
    pump()
    time.sleep(0.3)

    def client_size(h):
        rc = wt.RECT()
        user32.GetClientRect(h, ctypes.byref(rc))
        return rc.right - rc.left, rc.bottom - rc.top

    c1, c2 = client_size(w1), client_size(w2)
    fake = [(int(w1), os.getpid(), c1[0], c1[1]), (int(w2), os.getpid(), c2[0], c2[1])]
    mod.find_eve_windows = lambda pid_filter=None: fake
    mod.list_process_pids = lambda name: []

    result = {}

    def watch():
        time.sleep(3.0)
        result["wins"] = overlay_windows()
        result["origins"] = [mod.client_origin(w1), mod.client_origin(w2)]
        result["sizes"] = [c1, c2]

    th = threading.Thread(target=watch, daemon=True)
    th.start()
    mod.App(Args(multi)).run()
    th.join(timeout=5)
    user32.DestroyWindow(w1)
    user32.DestroyWindow(w2)

    label = "多客户端" if multi else "单客户端"
    wins = result.get("wins", [])
    print("\n[%s] 浮层窗口数 = %d" % (label, len(wins)))
    for v, x, y, w, h in wins:
        print("    visible=%-5s pos=(%d,%d) %dx%d" % ("yes" if v else "no", x, y, w, h))
    return len(wins), wins, result.get("origins"), result.get("sizes")


def main():
    n_multi, wins, origins, sizes = run_case(True)
    n_single, _, _, _ = run_case(False)

    ok = True
    if n_multi != 2:
        print("[FAIL] 多客户端模式应产生 2 个浮层窗口，实际 %d" % n_multi)
        ok = False
    if n_single != 1:
        print("[FAIL] 单客户端模式应产生 1 个浮层窗口，实际 %d" % n_single)
        ok = False
    if origins and sizes:
        want = {(origins[0][0], origins[0][1], sizes[0][0], sizes[0][1]),
                (origins[1][0], origins[1][1], sizes[1][0], sizes[1][1])}
        got = {(x, y, w, h) for _, x, y, w, h in wins}
        missing = want - got
        if missing:
            print("[FAIL] 以下客户区原点没有对应的浮层窗口: %s" % missing)
            ok = False
        else:
            print("[OK] 两个浮层窗口分别贴在各自客户区原点: %s" % sorted(got))

    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
