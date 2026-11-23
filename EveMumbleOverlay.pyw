"""EVE Online + Mumble 说话浮层 —— 托盘常驻单文件版。

双击本文件（.pyw 用 pythonw 打开）即：无控制台窗口、托盘出现图标、随 Mumble / EVE 自动接管。
原理：Mumble 客户端在 \\\\.\\pipe\\MumbleOverlayPipe 上做服务端，把整个浮层渲染成
ARGB 预乘位图放进共享内存，再用 BLIT 消息告诉客户端贴哪块。本程序扮演"虚拟 overlay
客户端"，把位图贴到置顶、鼠标穿透的分层窗口上。不注入、不 hook DirectX。

多客户端：Mumble 对同一个画布尺寸渲染出来的位图是完全一样的，所以"一个画布 + N 个窗口"即可，
不必给每个客户端各连一条管道。托盘菜单「全部客户端同时显示」可切换，开关会记进 config.json。

启动顺序无关：每 0.4 秒扫一次窗口，游戏后开也会在 1 秒内自动接管。

命令行（可选）：
    pythonw EveMumbleOverlay.pyw                 正常常驻（单客户端，跟随前台）
    python  EveMumbleOverlay.pyw --multi         所有可见客户端同时显示
    python  EveMumbleOverlay.pyw --single        强制只跟随前台客户端
    python  EveMumbleOverlay.pyw --duration 10   自测，10 秒后自动退出
    python  EveMumbleOverlay.pyw --pid 32256     只跟指定 EVE 进程
    python  EveMumbleOverlay.pyw --autostart on  写开机自启（off 取消）
日志：%LOCALAPPDATA%\\EveMumbleOverlay\\overlay.log
配置：%LOCALAPPDATA%\\EveMumbleOverlay\\config.json

许可说明：本程序独立实现 Mumble 覆盖层的通信协议（仅使用协议常量与字段宽度这类互操作
必需的事实信息，见 mumble 仓库 overlay/overlay.h，BSD-3-Clause，版权归 The Mumble
Developers），未复制、未链接、未分发 Mumble 的任何源码或二进制。详见 NOTICE.md。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time

# ------------------------------------------------------------------ 常量

APP_TITLE = "EVE 语音浮层"
APP_NAME = "EveMumbleOverlay"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
TH32CS_SNAPPROCESS = 0x00000002

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_MAP_READ = 0x0004
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
WM_HOTKEY = 0x0312
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
PM_REMOVE = 0x0001
DIB_RGB_COLORS = 0
BI_RGB = 0

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002

IDM_TOGGLE = 1001
IDM_AUTOSTART = 1002
IDM_LOG = 1003
IDM_EXIT = 1004
IDM_MULTI = 1005
WM_TRAY = 0x8001

# Mumble overlay 协议（mumble 仓库 overlay/overlay.h，v1.5.915 与 master 一致）
PIPE_NAME = r"\\.\pipe\MumbleOverlayPipe"
MAGIC = 0x00000005
MSG_INIT = 0
MSG_SHMEM = 1
MSG_BLIT = 2
MSG_ACTIVE = 3
MSG_PID = 4
MSG_FPS = 5
MSG_INTERACTIVE = 6

LOG_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "EveMumbleOverlay"
)
LOG_PATH = os.path.join(LOG_DIR, "overlay.log")
CONFIG_PATH = os.path.join(LOG_DIR, "config.json")

EVE_EXE = "exefile.exe"
SCAN_INTERVAL = 0.4
SWITCH_DEBOUNCE = 0.3
TOPMOST_INTERVAL = 1.0
RECONNECT_INTERVAL = 3.0
MAX_CANVASES = 3

# ------------------------------------------------------------------ Win32

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

# 窗口类只注册一次，回调对象常驻（否则回调被 GC 后，已注册的窗口类会调用野指针直接崩进程）
_current_app = None
_keep_alive = []
_classes_ready = False


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
        ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wt.HICON),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.WCHAR * 260),
    ]


user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wt.ATOM
user32.UpdateLayeredWindow.restype = wt.BOOL
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    wt.HDC, ctypes.POINTER(wt.POINT), wt.COLORREF, ctypes.POINTER(BLENDFUNCTION), wt.DWORD,
]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.IsWindow.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.GetClientRect.restype = wt.BOOL
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.restype = wt.BOOL
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT,
]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wt.HWND]
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [
    wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID,
]
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CreateIconIndirect.restype = wt.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.DestroyIcon.argtypes = [wt.HICON]
user32.SetProcessDPIAware.restype = wt.BOOL

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [
    wt.HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
    ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD,
]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateBitmap.restype = wt.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p]

k32.CreateFileW.restype = wt.HANDLE
k32.CreateFileW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE,
]
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.ReadFile.argtypes = [wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
k32.WriteFile.argtypes = [wt.HANDLE, wt.LPCVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID]
k32.PeekNamedPipe.argtypes = [
    wt.HANDLE, wt.LPVOID, wt.DWORD,
    ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
]
k32.OpenFileMappingW.restype = wt.HANDLE
k32.OpenFileMappingW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
k32.MapViewOfFile.restype = ctypes.c_void_p
k32.MapViewOfFile.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.QueryFullProcessImageNameW.restype = wt.BOOL
k32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
]
k32.GetModuleHandleW.restype = wt.HMODULE
k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
k32.CreateMutexW.restype = wt.HANDLE
k32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
k32.Process32FirstW.restype = wt.BOOL
k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = wt.BOOL
k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

MSG_CLASS = "EveMumbleOverlayMsg"
OVERLAY_CLASS = "EveMumbleOverlayWnd"


def _overlay_proc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _msg_proc(hwnd, msg, wparam, lparam):
    app = _current_app
    if app is not None:
        return app.handle_message(hwnd, msg, wparam, lparam)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def register_window_classes():
    """注册浮层窗口类与消息窗口类（进程内只做一次）。"""
    global _classes_ready
    if _classes_ready:
        return
    for name, proc in ((OVERLAY_CLASS, _overlay_proc), (MSG_CLASS, _msg_proc)):
        cb = WNDPROC(proc)
        _keep_alive.append(cb)
        wc = WNDCLASSW()
        wc.lpfnWndProc = cb
        wc.hInstance = k32.GetModuleHandleW(None)
        wc.lpszClassName = name
        _keep_alive.append(wc)
        user32.RegisterClassW(ctypes.byref(wc))
    _classes_ready = True

# ------------------------------------------------------------------ 日志

_HAS_CONSOLE = sys.stdout is not None


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    if _HAS_CONSOLE:
        try:
            print(line, flush=True)
        except Exception:
            pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ------------------------------------------------------------------ 配置

def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        log("保存配置失败: %s" % exc)


# ------------------------------------------------------------------ 开机自启

def autostart_command():
    if getattr(sys, "frozen", False):
        return '"%s"' % os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(sys.executable)
    pyw = os.path.join(exe_dir, "pythonw.exe")
    if not os.path.exists(pyw):
        pyw = sys.executable
    script = os.path.abspath(sys.argv[0])
    return '"%s" "%s"' % (pyw, script)


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    try:
        if on:
            # Run 键在某些干净环境（新装系统 / 服务账户 / CI runner）里可能不存在，需要自己建
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as k:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
                ) as k:
                    try:
                        winreg.DeleteValue(k, APP_NAME)
                    except FileNotFoundError:
                        pass
            except FileNotFoundError:
                pass  # 键都没有，等于本来就没开
        log("开机自启已%s" % ("开启" if on else "关闭"))
        return True
    except OSError as exc:
        log("设置开机自启失败: %s" % exc)
        return False


# ------------------------------------------------------------------ 托盘图标

def create_tray_icon():
    """画一个 32x32 的圆形底 + 三根白色竖条（说话指示），返回 HICON。"""
    size = 32
    px = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            dx = x - 15.5
            dy = y - 15.5
            if dx * dx + dy * dy <= 14.6 * 14.6:
                i = (y * size + x) * 4
                px[i + 0] = 0x56  # B
                px[i + 1] = 0x6E  # G
                px[i + 2] = 0x0F  # R
                px[i + 3] = 255
    for x0, y0, y1 in ((9, 12, 20), (15, 8, 24), (21, 12, 20)):
        for y in range(y0, y1):
            for x in range(x0, x0 + 3):
                i = (y * size + x) * 4
                px[i + 0] = px[i + 1] = px[i + 2] = 255
                px[i + 3] = 255

    row = size * 4
    bottom_up = b"".join(bytes(px[y * row:(y + 1) * row]) for y in range(size - 1, -1, -1))

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = size
    bmi.bmiHeader.biHeight = size
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB

    screen = user32.GetDC(None)
    bits = ctypes.c_void_p()
    hbm_color = gdi32.CreateDIBSection(
        screen, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
    )
    user32.ReleaseDC(None, screen)
    if not hbm_color or not bits:
        return None
    ctypes.memmove(bits, bottom_up, len(bottom_up))

    mask_zeros = ctypes.create_string_buffer(4 * size)
    hbm_mask = gdi32.CreateBitmap(size, size, 1, 1, mask_zeros)

    info = ICONINFO()
    info.fIcon = True
    info.hbmMask = hbm_mask
    info.hbmColor = hbm_color
    hicon = user32.CreateIconIndirect(ctypes.byref(info))
    gdi32.DeleteObject(hbm_mask)
    gdi32.DeleteObject(hbm_color)
    return hicon


# ------------------------------------------------------------------ 进程 / 窗口

_name_cache = {}


def list_process_pids(exe_name):
    """按可执行文件名枚举进程 PID（启动时探测 EVE 客户端用）。"""
    pids = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return pids
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            return pids
        while True:
            if entry.szExeFile.lower() == exe_name:
                pids.append(int(entry.th32ProcessID))
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(snap)
    return pids


def process_image_name(pid):
    if pid in _name_cache:
        return _name_cache[pid]
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value).lower()
            _name_cache[pid] = name
            return name
        return ""
    finally:
        k32.CloseHandle(h)


def find_eve_windows(pid_filter=None):
    """列出可见、非最小化、客户区不小于 320x240 的 EVE 客户端窗口。"""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid_filter is not None:
            if pid.value != pid_filter:
                return True
        elif process_image_name(pid.value) != EVE_EXE:
            return True
        rc = wt.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return True
        w, h = rc.right - rc.left, rc.bottom - rc.top
        if w < 320 or h < 240:
            return True
        found.append((hwnd, pid.value, w, h))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def client_origin(hwnd):
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def window_usable(hwnd):
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return False
    rc = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return False
    return (rc.right - rc.left) >= 64 and (rc.bottom - rc.top) >= 64


# ------------------------------------------------------------------ Mumble 管道

class OverlayPipe:
    def __init__(self):
        self.h = None
        self.shm_map = None
        self.shm_ptr = None
        self.buf = b""
        self.active = (0, 0, 0, 0)

    def connect(self, width, height):
        self.close()
        self.h = k32.CreateFileW(
            PIPE_NAME, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if not self.h or self.h == INVALID_HANDLE_VALUE:
            self.h = None
            return False
        self._send(MSG_INIT, struct.pack("<II", width, height))
        self._send(MSG_PID, struct.pack("<I", os.getpid()))
        return True

    def close(self):
        if self.shm_ptr:
            k32.UnmapViewOfFile(self.shm_ptr)
            self.shm_ptr = None
        if self.shm_map:
            k32.CloseHandle(self.shm_map)
            self.shm_map = None
        if self.h:
            self._send(MSG_INTERACTIVE, b"\x00")
            k32.CloseHandle(self.h)
            self.h = None

    def _send(self, mtype, payload=b""):
        if not self.h:
            return
        raw = struct.pack("<IiI", MAGIC, len(payload), mtype) + payload
        buf = ctypes.create_string_buffer(raw, len(raw))
        written = wt.DWORD(0)
        k32.WriteFile(self.h, buf, len(raw), ctypes.byref(written), None)

    def pump(self):
        if not self.h:
            return False
        updated = False
        while True:
            avail = wt.DWORD(0)
            if not k32.PeekNamedPipe(self.h, None, 0, None, ctypes.byref(avail), None):
                log("Mumble 管道断开")
                self.close()
                return True
            if avail.value == 0:
                break
            chunk = ctypes.create_string_buffer(avail.value)
            got = wt.DWORD(0)
            k32.ReadFile(self.h, chunk, avail.value, ctypes.byref(got), None)
            self.buf += chunk.raw[: got.value]

        while len(self.buf) >= 12:
            magic, length, mtype = struct.unpack("<IiI", self.buf[:12])
            if magic != MAGIC or length < 0 or length > 2048:
                log("覆盖层协议不匹配，重连")
                self.close()
                return True
            if len(self.buf) < 12 + length:
                break
            payload = self.buf[12: 12 + length]
            self.buf = self.buf[12 + length:]
            if mtype == MSG_SHMEM:
                self._open_shm(payload.split(b"\x00")[0].decode("utf-8", "replace"))
                updated = True
            elif mtype == MSG_BLIT:
                updated = True
            elif mtype == MSG_ACTIVE:
                self.active = struct.unpack("<IIII", payload)
                updated = True
        return updated

    def _open_shm(self, name):
        if self.shm_ptr:
            k32.UnmapViewOfFile(self.shm_ptr)
            self.shm_ptr = None
        if self.shm_map:
            k32.CloseHandle(self.shm_map)
            self.shm_map = None
        hmap = k32.OpenFileMappingW(FILE_MAP_READ, False, name)
        if not hmap:
            log("打开共享内存失败: %r" % name)
            return
        ptr = k32.MapViewOfFile(hmap, FILE_MAP_READ, 0, 0, 0)
        if not ptr:
            k32.CloseHandle(hmap)
            log("映射共享内存失败: %r" % name)
            return
        self.shm_map, self.shm_ptr = hmap, ptr


# ------------------------------------------------------------------ 画布 / 窗口

class Canvas:
    """一块 ARGB 预乘位图，尺寸与 EVE 客户区一致；多个窗口可以共享同一块。"""

    def __init__(self, width, height):
        self.w, self.h = width, height
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        screen = user32.GetDC(None)
        self.dib = gdi32.CreateDIBSection(
            screen, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        user32.ReleaseDC(None, screen)
        if not self.dib:
            raise ctypes.WinError(ctypes.get_last_error())
        self.bits = bits
        self.mem_dc = gdi32.CreateCompatibleDC(None)
        gdi32.SelectObject(self.mem_dc, self.dib)

    def clear(self):
        if self.bits:
            ctypes.memset(self.bits, 0, self.w * self.h * 4)

    def destroy(self):
        if self.mem_dc:
            gdi32.DeleteDC(self.mem_dc)
            self.mem_dc = None
        if self.dib:
            gdi32.DeleteObject(self.dib)
            self.dib = None
        self.bits = None


_wnd_seq = [0]


class OverlayWindow:
    """一个置顶、鼠标穿透的分层窗口。内容由外部给的 Canvas 提供。"""

    def __init__(self):
        _wnd_seq[0] += 1
        self.tag = _wnd_seq[0]
        self.hwnd = None
        self.visible = False

    def create(self):
        register_window_classes()
        ex = (
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )
        self.hwnd = user32.CreateWindowExW(
            ex, OVERLAY_CLASS, APP_TITLE, WS_POPUP,
            -32000, -32000, 8, 8, None, None, k32.GetModuleHandleW(None), None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        return self.hwnd

    def present(self, canvas, x, y):
        if not self.hwnd or not canvas.bits:
            return
        screen = user32.GetDC(None)
        pt_dst = wt.POINT(x, y)
        size = wt.SIZE(canvas.w, canvas.h)
        pt_src = wt.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            self.hwnd, screen, ctypes.byref(pt_dst), ctypes.byref(size),
            canvas.mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA,
        )
        user32.ReleaseDC(None, screen)

    def show(self, visible):
        if visible == self.visible or not self.hwnd:
            return
        self.visible = visible
        if visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            user32.SetWindowPos(
                self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
        else:
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def raise_topmost(self):
        if self.hwnd and self.visible:
            user32.SetWindowPos(
                self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self.visible = False


# ------------------------------------------------------------------ 应用主体

class App:
    def __init__(self, args):
        self.args = args
        self.running = True
        self.enabled = True
        cfg = load_config()
        self.multi = bool(args.multi) or (bool(cfg.get("multi")) and not args.single)

        self.msg_hwnd = None
        self.hicon = None
        self.wins = {}       # EVE 窗口 hwnd -> {pid, size, pos, presented, win, topmost_at}
        self.rend = {}       # (w,h) -> {canvas, pipe, active, dirty}
        self.pid_filter = args.pid
        self.pending_pick = None
        self.pending_since = 0.0
        self.scan_at = 0.0
        self.reconnect_at = 0.0
        self.status = ""
        self.start = time.time()
        self.blit_seen = 0
        self.first_content = False
        self.hint_shown = False

    # -- 消息窗口（托盘 / 热键用，不随客户端窗口创建销毁而消失） --

    def create_message_window(self):
        global _current_app
        register_window_classes()
        _current_app = self
        self.msg_hwnd = user32.CreateWindowExW(
            0, MSG_CLASS, APP_TITLE, WS_POPUP, -32000, -32000, 8, 8,
            None, None, k32.GetModuleHandleW(None), None,
        )
        if not self.msg_hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    # -- 托盘 --

    def add_tray(self):
        self.hicon = create_tray_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.msg_hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon or 0
        nid.szTip = "%s：启动中" % APP_TITLE
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            log("托盘图标添加失败: %d" % ctypes.get_last_error())

    def balloon(self, title, text):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.msg_hwnd
        nid.uID = 1
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_INFO
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def set_tip(self, text):
        if text == self.status:
            return
        self.status = text
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.msg_hwnd
        nid.uID = 1
        nid.uFlags = NIF_TIP
        nid.szTip = text[:127]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove_tray(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.msg_hwnd
        nid.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def show_menu(self):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(
            menu, MF_STRING, IDM_TOGGLE,
            "屏蔽浮层（暂停显示）" if self.enabled else "启用浮层",
        )
        user32.AppendMenuW(
            menu, MF_STRING | (MF_CHECKED if self.multi else 0), IDM_MULTI,
            "全部客户端同时显示",
        )
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(
            menu,
            MF_STRING | (MF_CHECKED if autostart_enabled() else 0),
            IDM_AUTOSTART,
            "开机自动启动",
        )
        user32.AppendMenuW(menu, MF_STRING, IDM_LOG, "打开日志文件")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出")

        pt = wt.POINT(0, 0)
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.msg_hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.msg_hwnd, None
        )
        user32.PostMessageW(self.msg_hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

        if cmd == IDM_TOGGLE:
            self.toggle_enabled()
        elif cmd == IDM_MULTI:
            self.toggle_multi()
        elif cmd == IDM_AUTOSTART:
            set_autostart(not autostart_enabled())
        elif cmd == IDM_LOG:
            try:
                os.startfile(LOG_PATH)
            except Exception as exc:
                log("打开日志失败: %s" % exc)
        elif cmd == IDM_EXIT:
            self.running = False

    def toggle_enabled(self):
        self.enabled = not self.enabled
        if not self.enabled:
            for t in self.wins.values():
                t["win"].show(False)
        log(
            "浮层已%s（%s）"
            % ("启用" if self.enabled else "屏蔽", "有人说话时显示" if self.enabled else "暂停显示")
        )

    def toggle_multi(self):
        self.multi = not self.multi
        cfg = load_config()
        cfg["multi"] = self.multi
        save_config(cfg)
        log(
            "多客户端模式已%s"
            % ("开启（所有可见客户端同时显示）" if self.multi else "关闭（只跟随前台客户端）")
        )
        self.apply({})

    def handle_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self.show_menu()
                return 0
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.toggle_enabled()
                return 0
            return 0
        if msg == WM_HOTKEY and wparam == 1:
            self.toggle_enabled()
            return 0
        if msg == WM_CLOSE:
            self.running = False
            return 0
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def pump_messages(self):
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # -- 目标发现 --

    def scan_targets(self):
        candidates = find_eve_windows(self.pid_filter)
        if not candidates:
            self.apply({})
            self.set_tip("%s：等待 EVE 窗口" % APP_TITLE)
            return

        if self.multi:
            wanted = {c[0]: c for c in candidates}
        else:
            fg = user32.GetForegroundWindow()
            pick = next((c for c in candidates if c[0] == fg), None)
            if pick is None:
                pick = next((c for c in candidates if c[0] in self.wins), candidates[0])
            wanted = {pick[0]: pick}

            # 稳定 300ms 才真正切换，避免焦点抖动导致目标乱跳
            now = time.time()
            if pick[0] != self.pending_pick:
                self.pending_pick = pick[0]
                self.pending_since = now
            elif pick[0] not in self.wins and now - self.pending_since < SWITCH_DEBOUNCE:
                keep = next((c for c in candidates if c[0] in self.wins), None)
                if keep is not None:
                    wanted = {keep[0]: keep}
        self.apply(wanted)

    def apply(self, wanted):
        # 新增 / 更新
        for chwnd, (hwnd, pid, w, h) in wanted.items():
            t = self.wins.get(chwnd)
            if t is None:
                win = OverlayWindow()
                win.create()
                self.wins[chwnd] = {
                    "pid": pid,
                    "size": (w, h),
                    "pos": client_origin(chwnd),
                    "presented": None,
                    "win": win,
                    "topmost_at": 0.0,
                }
                log(
                    "%s EVE 客户端 0x%X pid=%d 客户区 %dx%d"
                    % ("跟踪" if self.multi else "锁定", chwnd, pid, w, h)
                )
            else:
                if t["size"] != (w, h):
                    log(
                        "客户端 0x%X 客户区 %dx%d -> %dx%d"
                        % (chwnd, t["size"][0], t["size"][1], w, h)
                    )
                    t["size"] = (w, h)
                    t["presented"] = None
                pos = client_origin(chwnd)
                if pos != t["pos"]:
                    t["pos"] = pos

        # 移除
        for chwnd in list(self.wins):
            if chwnd not in wanted:
                t = self.wins.pop(chwnd)
                t["win"].destroy()
                log("移除客户端 0x%X（最小化 / 关闭 / 切走）" % chwnd)

        # 画布与管道在程序运行期间保持（不因客户端消失而释放），
        # 只有画布数量超过上限时才淘汰最久未用的，避免反复拖拽窗口尺寸把内存撑爆
        used = {t["size"] for t in self.wins.values()}
        now = time.time()
        for key in used:
            r = self.rend.get(key)
            if r is not None:
                r["last_used"] = now
                continue
            if key[0] < 64 or key[1] < 64:
                continue
            try:
                self.rend[key] = {
                    "canvas": Canvas(*key),
                    "pipe": OverlayPipe(),
                    "active": (0, 0, 0, 0),
                    "dirty": True,
                    "last_used": now,
                }
                log("新建 %dx%d 画布（程序运行期间保持）" % key)
            except OSError as exc:
                log("创建 %dx%d 画布失败: %s" % (key[0], key[1], exc))

        if len(self.rend) > MAX_CANVASES:
            idle = sorted(
                (k for k in self.rend if k not in used),
                key=lambda k: self.rend[k]["last_used"],
            )
            for key in idle[: len(self.rend) - MAX_CANVASES]:
                r = self.rend.pop(key)
                r["pipe"].close()
                r["canvas"].destroy()
                log("画布超过上限 %d，释放最久未用的 %dx%d" % (MAX_CANVASES, key[0], key[1]))

    # -- 主循环 --

    def step(self, now):
        if now >= self.scan_at:
            self.scan_at = now + SCAN_INTERVAL
            self.scan_targets()

        # 收位图
        for key, r in self.rend.items():
            pipe = r["pipe"]
            if pipe.h is None and now >= self.reconnect_at:
                if pipe.connect(key[0], key[1]):
                    log("已连接覆盖层管道 %dx%d" % key)
                    r["wait_logged"] = False
                else:
                    self.reconnect_at = now + RECONNECT_INTERVAL
                    if not r.get("wait_logged"):
                        r["wait_logged"] = True
                        log(
                            "等待 Mumble 覆盖层管道（Mumble 未运行，或「游戏内显示」未启用），"
                            "每 %d 秒重试" % RECONNECT_INTERVAL
                        )
            if pipe.pump() and pipe.shm_ptr:
                ctypes.memmove(
                    r["canvas"].bits, ctypes.c_void_p(pipe.shm_ptr), key[0] * key[1] * 4
                )
                r["dirty"] = True
                self.blit_seen += 1
            if pipe.active != r["active"]:
                r["active"] = pipe.active
                if pipe.active[2] and pipe.active[3] and not self.first_content:
                    self.first_content = True
                    log("检测到说话者")

        # 显示
        visible_count = 0
        for chwnd, t in list(self.wins.items()):
            r = self.rend.get(t["size"])
            have = bool(r and r["active"][2] and r["active"][3])
            ok = window_usable(chwnd)
            want = bool(self.enabled and have and ok and r and r["canvas"].bits)
            if want:
                if (not t["win"].visible) or r["dirty"] or t["presented"] != t["pos"]:
                    t["win"].present(r["canvas"], t["pos"][0], t["pos"][1])
                    t["presented"] = t["pos"]
                t["win"].show(True)
                if now - t["topmost_at"] >= TOPMOST_INTERVAL:
                    t["topmost_at"] = now
                    t["win"].raise_topmost()
                visible_count += 1
            else:
                t["win"].show(False)

        for r in self.rend.values():
            r["dirty"] = False

        if visible_count:
            self.set_tip(
                "%s：显示中（%d 个客户端）" % (APP_TITLE, visible_count)
                if self.multi
                else "%s：显示中" % APP_TITLE
            )
        elif self.wins:
            self.set_tip("%s：待机（无人说话）" % APP_TITLE)
        if (
            self.enabled
            and not self.first_content
            and not self.hint_shown
            and now - self.start >= 15
        ):
            self.hint_shown = True
            log("15 秒内没检测到说话者：确认 Mumble 已连服务器且频道里有人说话")

    def run(self):
        self.create_message_window()
        self.add_tray()

        pids = list_process_pids(EVE_EXE)
        if pids:
            log(
                "启动时检测到 %d 个 EVE 客户端进程：%s"
                % (len(pids), ", ".join(str(p) for p in pids))
            )
        else:
            log("启动时没检测到 EVE 客户端进程，稍后出现会自动接管")
        if self.args.pid and pids and self.args.pid not in pids:
            log("注意：--pid %d 不在当前 EVE 进程列表里，仍会持续等待该进程的窗口" % self.args.pid)
        log("模式：%s" % ("全部客户端同时显示" if self.multi else "只跟随前台客户端"))

        hotkey = None
        for mods, vk, name in (
            (MOD_CONTROL | MOD_ALT, ord("O"), "Ctrl+Alt+O"),
            (MOD_CONTROL | MOD_ALT, ord("M"), "Ctrl+Alt+M"),
            (MOD_ALT, 0x77, "Alt+F8"),
        ):
            if user32.RegisterHotKey(self.msg_hwnd, 1, mods, vk):
                hotkey = name
                break
        log("托盘已启动，热键：%s" % (hotkey or "无（都被占用）"))
        log("日志：%s" % LOG_PATH)
        if not self.args.duration:
            self.balloon(
                APP_TITLE,
                "已启动并在托盘常驻。右键托盘图标可切换「全部客户端同时显示」与开机自启。",
            )

        try:
            while self.running:
                self.pump_messages()
                now = time.time()
                if self.args.duration and now - self.start >= self.args.duration:
                    log("自测结束，共 %d 批位图更新" % self.blit_seen)
                    break
                self.step(now)
                time.sleep(0.008)
        finally:
            self.shutdown()

    def shutdown(self):
        global _current_app
        for t in self.wins.values():
            t["win"].destroy()
        self.wins.clear()
        for r in self.rend.values():
            r["pipe"].close()
            r["canvas"].destroy()
        self.rend.clear()
        self.remove_tray()
        if self.hicon:
            user32.DestroyIcon(self.hicon)
            self.hicon = None
        if self.msg_hwnd:
            user32.UnregisterHotKey(self.msg_hwnd, 1)
            user32.DestroyWindow(self.msg_hwnd)
            self.msg_hwnd = None
        if _current_app is self:
            _current_app = None
        log("已退出")


def enable_dpi():
    try:
        k32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--pid", type=int, default=None)
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--autostart", choices=("on", "off"), default=None)
    ap.add_argument("--multi", action="store_true", help="所有可见客户端同时显示")
    ap.add_argument("--single", action="store_true", help="只在前台客户端显示（默认）")
    args = ap.parse_args()

    enable_dpi()

    if args.autostart:
        ok = set_autostart(args.autostart == "on")
        return 0 if ok else 1

    mutex = k32.CreateMutexW(None, False, "Local\\EveMumbleOverlaySingleInstance")
    if not mutex or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        log("已有实例在运行，本次退出")
        user32.MessageBoxW(
            None, "EVE 语音浮层已经在运行（看右下角托盘图标）。", APP_TITLE, 0x40
        )
        return 0

    app = App(args)
    try:
        app.run()
    except Exception:
        import traceback
        detail = traceback.format_exc()
        log("崩溃:\n" + detail)
        user32.MessageBoxW(None, "程序出错，详情见日志：\n" + LOG_PATH, APP_TITLE, 0x10)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
