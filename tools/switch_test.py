"""回归验证：模拟 EVE-O-Preview 的窗口切换，检查浮层是否自动跟随、是否会永久隐藏。

用法：先启动 EveMumbleOverlay，再运行本脚本。
"""

import ctypes
import ctypes.wintypes as wt
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

SW_MINIMIZE = 6
SW_RESTORE = 9
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)
]
k32.CloseHandle.argtypes = [wt.HANDLE]


def exe_name(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        k32.CloseHandle(h)


def overlay_state():
    h = user32.FindWindowW("EveMumbleOverlayWnd", None)
    if not h:
        return "浮层窗口不存在"
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return "visible=%s pos=(%d,%d) size=%dx%d" % (
        "yes" if user32.IsWindowVisible(h) else "no",
        r.left, r.top, r.right - r.left, r.bottom - r.top,
    )


def eve_clients():
    out = []

    def cb(hwnd, _):
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if exe_name(pid.value) != "exefile.exe":
            return True
        t = title(hwnd)
        if not t.startswith("EVE - "):
            return True
        out.append((hwnd, pid.value, "min" if user32.IsIconic(hwnd) else "normal"))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def title(hwnd):
    b = ctypes.create_unicode_buffer(128)
    user32.GetWindowTextW(hwnd, b, 128)
    return b.value


print("== 初始 ==")
clients = eve_clients()
for h, pid, st in clients:
    print("  0x%X pid=%d %s %s" % (h, pid, st, title(h)))
print("  前台=0x%X" % int(user32.GetForegroundWindow()))
print("  浮层:", overlay_state())
normal = [c for c in clients if c[2] == "normal"]
others = [c for c in clients if c[2] == "min"]
if not normal or not others:
    print("需要 1 个正常 + 1 个最小化的 EVE 客户端才能测切换")
    raise SystemExit(1)
first, second = normal[0], others[0]

print("\n== 切到第二个客户端（恢复 + 置前台），模拟 EVE-O-Preview ==")
user32.ShowWindow(second[0], SW_RESTORE)
user32.SetForegroundWindow(second[0])
time.sleep(2.5)
print("  前台=0x%X" % int(user32.GetForegroundWindow()))
print("  浮层:", overlay_state())

print("\n== 把第二个最小化（模拟 EVE-O-Preview 隐藏非当前客户端）==")
user32.ShowWindow(second[0], SW_MINIMIZE)
time.sleep(1.5)
print("  浮层:", overlay_state())

print("\n== 再切回第一个 ==")
user32.ShowWindow(first[0], SW_RESTORE)
user32.SetForegroundWindow(first[0])
time.sleep(2.5)
print("  前台=0x%X" % int(user32.GetForegroundWindow()))
print("  浮层:", overlay_state())
