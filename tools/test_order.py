"""回归测试：启动顺序无关性。

覆盖三种"后到的"情况，验证插件先启动也能自动接管：
  A. Mumble 后启动（先连不上管道，Mumble 起来后自动连上）
  B. EVE 后启动（先没有客户端窗口，窗口出现后自动锁定并显示）
用例通过替换模块里的 find_eve_windows / PIPE_NAME 来模拟，不依赖真实游戏进程。

用法：python tools/test_order.py   （需要 Mumble 正在运行，用于 A 用例的"后启动"）
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
SW_SHOW = 5
SW_SHOWNOACTIVATE = 4
CB = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32.EnumWindows.argtypes = [CB, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
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
_wc.lpszClassName = "EveOrderTestWindow"
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW(ctypes.byref(_wc))


def make_window(x, y, w, h):
    hwnd = user32.CreateWindowExW(
        0, "EveOrderTestWindow", "fake eve", WS_OVERLAPPEDWINDOW,
        x, y, w, h, None, None, _wc.hInstance, None,
    )
    user32.ShowWindow(hwnd, SW_SHOW)
    return hwnd


def overlay_windows():
    rows = []

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, buf, 128)
        if buf.value.startswith("EveMumbleOverlayWnd"):
            rows.append(bool(user32.IsWindowVisible(h)))
        return True

    user32.EnumWindows(CB(cb), 0)
    return rows


def load_module():
    spec = importlib.util.spec_from_file_location("ovl", MODULE, loader=SourceFileLoader("ovl", MODULE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sample(result, seconds=10.0):
    """持续采样：浮层只在"有人说话"时可见，所以要取整个窗口期内的可见峰值。"""
    best = 0
    seen = 0
    end = time.time() + seconds
    while time.time() < end:
        vis = overlay_windows()
        seen = max(seen, len(vis))
        best = max(best, sum(1 for v in vis if v))
        time.sleep(0.4)
    result["visible"] = best
    result["windows"] = seen


class Args:
    def __init__(self, duration=18.0):
        self.pid = None
        self.duration = duration
        self.autostart = None
        self.multi = False
        self.single = True


def run_case(name, setup_thread):
    mod = load_module()
    lines = []
    real_log = mod.log

    def capture(msg):
        lines.append(str(msg))
        real_log(msg)

    mod.log = capture
    mod.list_process_pids = lambda exe: []
    mod.find_eve_windows = lambda pid_filter=None: []  # 默认没有客户端，用例线程里再放出来

    result = {}
    th = threading.Thread(target=setup_thread, args=(mod, result), daemon=True)
    th.start()
    mod.App(Args()).run()
    th.join(timeout=8)

    joined = "\n".join(lines)
    print("\n===== %s =====" % name)
    for line in lines:
        if any(k in line for k in ("等待", "已连接", "锁定", "跟踪", "检测到", "新建", "自测")):
            print("   " + line)
    return joined, result


def case_mumble_late(mod, result):
    """先让管道连不上，5 秒后"Mumble 启动"。"""
    real_pipe = mod.PIPE_NAME
    mod.PIPE_NAME = r"\\.\pipe\MumbleOverlayPipe_NotRunningYet"
    w = make_window(120, 120, 820, 620)
    rc = wt.RECT()
    user32.GetClientRect(w, ctypes.byref(rc))
    size = (rc.right - rc.left, rc.bottom - rc.top)
    mod.find_eve_windows = lambda pid_filter=None: [(int(w), os.getpid(), size[0], size[1])]
    time.sleep(5)
    mod.PIPE_NAME = real_pipe
    sample(result, 10.0)
    user32.DestroyWindow(w)


def case_eve_late(mod, result):
    """先没有客户端窗口，5 秒后才出现。"""
    box = {}

    def fake(pid_filter=None):
        if "w" not in box:
            return []
        w = box["w"]
        rc = wt.RECT()
        user32.GetClientRect(w, ctypes.byref(rc))
        return [(int(w), os.getpid(), rc.right - rc.left, rc.bottom - rc.top)]

    mod.find_eve_windows = fake
    time.sleep(5)
    box["w"] = make_window(200, 200, 820, 620)
    sample(result, 10.0)
    user32.DestroyWindow(box["w"])


def case_neither(mod, result):
    """Mumble 和 EVE 都没启动：先空跑，5 秒后两者一起出现。"""
    real_pipe = mod.PIPE_NAME
    mod.PIPE_NAME = r"\\.\pipe\MumbleOverlayPipe_NeitherRunning"
    box = {}

    def fake(pid_filter=None):
        if "w" not in box:
            return []
        w = box["w"]
        rc = wt.RECT()
        user32.GetClientRect(w, ctypes.byref(rc))
        return [(int(w), os.getpid(), rc.right - rc.left, rc.bottom - rc.top)]

    mod.find_eve_windows = fake
    time.sleep(5)
    mod.PIPE_NAME = real_pipe
    box["w"] = make_window(320, 240, 820, 620)
    sample(result, 10.0)
    user32.DestroyWindow(box["w"])


def main():
    ok = True

    joined, result = run_case("A. Mumble 后启动", case_mumble_late)
    if "等待 Mumble 覆盖层管道" not in joined:
        print("[FAIL] 没有出现「等待 Mumble 覆盖层管道」的等待日志")
        ok = False
    if "已连接覆盖层管道" not in joined:
        print("[FAIL] Mumble「启动」后没有自动连上管道")
        ok = False
    elif "等待 Mumble 覆盖层管道" in joined:
        wait_at = joined.find("等待 Mumble 覆盖层管道")
        conn_at = joined.find("已连接覆盖层管道")
        if conn_at < wait_at:
            print("[FAIL] 顺序不对：先连上后等待")
            ok = False
        else:
            print("[OK] 先等待 → Mumble 出现后自动连上")
    if not result.get("visible"):
        print("[FAIL] 整个采样期都没有可见的浮层窗口")
        ok = False
    else:
        print("[OK] 采样期内浮层可见（峰值 %d 个窗口）" % result["visible"])

    joined, result = run_case("B. EVE 后启动", case_eve_late)
    if "锁定 EVE 客户端" not in joined:
        print("[FAIL] 客户端窗口出现后没有自动锁定")
        ok = False
    else:
        print("[OK] 客户端窗口出现后自动锁定")
    if not result.get("visible"):
        print("[FAIL] 整个采样期都没有可见的浮层窗口")
        ok = False
    else:
        print("[OK] 采样期内浮层可见（峰值 %d 个窗口）" % result["visible"])

    joined, result = run_case("C. Mumble 和 EVE 都没启动", case_neither)
    # 用例 C 里"等待管道"不会出现：没有客户端窗口时根本没有画布，自然不会去连管道
    if "锁定 EVE 客户端" not in joined:
        print("[FAIL] 客户端出现后没有自动锁定")
        ok = False
    if "已连接覆盖层管道" not in joined:
        print("[FAIL] 没有自动连上 Mumble")
        ok = False
    if not result.get("visible"):
        print("[FAIL] 最终没有可见的浮层窗口")
        ok = False
    if ok:
        print("[OK] 两者都没启动时也能空跑等待，随后自动接管")

    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
