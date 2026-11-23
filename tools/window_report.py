"""诊断：列出现在所有 EVE 客户端窗口的位置/可见性/最小化状态与前台的窗口。"""

import ctypes
import ctypes.wintypes as wt
import os

user32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
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
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(h)


fg = user32.GetForegroundWindow()
print(f"前台窗口 = 0x{int(fg):X}")
rows = []


def cb(hwnd, _):
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = exe_name(pid.value)
    if name not in ("exefile.exe", "eve-online.exe", "EveMumbleOverlay.exe"):
        return True
    r = wt.RECT()
    c = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    user32.GetClientRect(hwnd, ctypes.byref(c))
    title = ctypes.create_unicode_buffer(128)
    user32.GetWindowTextW(hwnd, title, 128)
    style = user32.GetWindowLongW(hwnd, -16)
    ex = user32.GetWindowLongW(hwnd, -20)
    rows.append(
        (
            "FG" if int(hwnd) == int(fg) else "  ",
            f"0x{int(hwnd):X}",
            name,
            pid.value,
            f"win=({r.left},{r.top})-({r.right},{r.bottom}) {r.right-r.left}x{r.bottom-r.top}",
            f"client={c.right-c.left}x{c.bottom-c.top}",
            "vis" if user32.IsWindowVisible(hwnd) else "HID",
            "MIN" if user32.IsIconic(hwnd) else "   ",
            f"style=0x{style & 0xFFFFFFFF:08X}",
            f"ex=0x{ex & 0xFFFFFFFF:08X}",
            title.value[:28],
        )
    )
    return True


user32.EnumWindows(WNDENUMPROC(cb), 0)
for row in rows:
    print(" | ".join(str(v) for v in row))
