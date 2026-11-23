"""EVE Online + Mumble 说话浮层 —— 托盘常驻单文件版。

双击本文件（.pyw 用 pythonw 打开）即：无控制台窗口、托盘出现图标、随 Mumble 自动接管。
原理：Mumble 客户端在 \\\\.\\pipe\\MumbleOverlayPipe 上做服务端，把整个浮层渲染成
ARGB 预乘位图放进共享内存，再用 BLIT 消息告诉客户端贴哪块。本程序扮演"虚拟 overlay
客户端"，把位图贴到置顶、鼠标穿透的分层窗口上。不注入、不 hook DirectX。

命令行（可选）：
    pythonw EveMumbleOverlay.pyw                 正常常驻
    python  EveMumbleOverlay.pyw --duration 10   自测，10 秒后自动退出（有控制台时打印日志）
    python  EveMumbleOverlay.pyw --pid 32256     绑定指定 EVE 客户端
    python  EveMumbleOverlay.pyw --autostart on  写开机自启（off 取消）
日志：%LOCALAPPDATA%\\EveMumbleOverlay\\overlay.log

许可说明：本程序独立实现 Mumble 覆盖层的通信协议（仅使用协议常量与字段宽度这类互操作
必需的事实信息，见 mumble 仓库 overlay/overlay.h，BSD-3-Clause，版权归 The Mumble
Developers），未复制、未链接、未分发 Mumble 的任何源码或二进制。详见 NOTICE.md。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import time

# ------------------------------------------------------------------ 常量

APP_TITLE = "EVE 语音浮层"
APP_NAME = "EveMumbleOverlay"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

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
WM_COMMAND = 0x0111
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

# ------------------------------------------------------------------ Win32

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


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

shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

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


# ------------------------------------------------------------------ EVE 窗口

_name_cache = {}


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
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid_filter is not None:
            if pid.value != pid_filter:
                return True
        elif process_image_name(pid.value) != "exefile.exe":
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
        log("共享内存就绪: %s" % name)


# ------------------------------------------------------------------ 应用主体

class App:
    def __init__(self, args):
        self.args = args
        self.running = True
        self.showing = False
        self.enabled = True
        self.topmost_at = 0.0
        self.blit_seen = 0
        self.width = 0
        self.height = 0
        self.dib = None
        self.dib_bits = None
        self.mem_dc = None
        self.hwnd = None
        self.hicon = None
        self.pipe = OverlayPipe()
        self.target_hwnd = None
        self.pid_filter = args.pid
        self.target_pid = args.pid
        self.pos = (0, 0)
        self.last_active = (0, 0, 0, 0)
        self.pending_resize = None
        self.resize_deadline = 0.0
        self.reconnect_at = 0.0
        self.scan_at = 0.0
        self.fg_seen = None
        self.fg_since = 0.0
        self.status = ""
        self.start = time.time()
        self.hint_shown = False
        self.first_content = False
        self._wndproc = WNDPROC(self._proc)
        self._wc = WNDCLASSW()

    # -- 窗口 --

    def create_window(self):
        cls = "EveMumbleOverlayWnd"
        self._wc.lpfnWndProc = self._wndproc
        self._wc.hInstance = k32.GetModuleHandleW(None)
        self._wc.lpszClassName = cls
        user32.RegisterClassW(ctypes.byref(self._wc))
        ex = (
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )
        self.hwnd = user32.CreateWindowExW(
            ex, cls, APP_TITLE, WS_POPUP,
            -32000, -32000, 8, 8, None, None, self._wc.hInstance, None,
        )
        if not self.hwnd:
            raise ctypes.WinError(ctypes.get_last_error())

    def ensure_canvas(self, width, height):
        if width < 64 or height < 64:
            log("忽略异常的客户区尺寸 %dx%d（窗口可能被最小化/移出屏幕）" % (width, height))
            return False
        if (width, height) == (self.width, self.height) and self.dib:
            return True
        self.destroy_canvas()
        self.width, self.height = width, height
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
        self.dib_bits = bits
        self.mem_dc = gdi32.CreateCompatibleDC(None)
        gdi32.SelectObject(self.mem_dc, self.dib)
        return True

    def destroy_canvas(self):
        if self.mem_dc:
            gdi32.DeleteDC(self.mem_dc)
            self.mem_dc = None
        if self.dib:
            gdi32.DeleteObject(self.dib)
            self.dib = None
        self.dib_bits = None
        self.width = self.height = 0

    def present(self):
        if not self.dib_bits or not self.hwnd:
            return
        screen = user32.GetDC(None)
        pt_dst = wt.POINT(self.pos[0], self.pos[1])
        size = wt.SIZE(self.width, self.height)
        pt_src = wt.POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(
            self.hwnd, screen, ctypes.byref(pt_dst), ctypes.byref(size),
            self.mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(blend), ULW_ALPHA,
        )
        user32.ReleaseDC(None, screen)

    def set_visible(self, visible):
        if visible == self.showing:
            return
        self.showing = visible
        log("浮层%s" % ("显示" if visible else "隐藏"))
        if visible:
            user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
            self.topmost_at = time.time()
            user32.SetWindowPos(
                self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            )
        else:
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def reassert_topmost(self, now):
        """EVE-O-Preview 的预览窗也是 topmost，会被压到我们上面，定期重新置顶。"""
        if not self.hwnd or not self.showing:
            return
        if now - self.topmost_at < 1.0:
            return
        self.topmost_at = now
        user32.SetWindowPos(
            self.hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        )

    def target_ok(self):
        if not self.target_hwnd or not user32.IsWindow(self.target_hwnd):
            return False
        if not user32.IsWindowVisible(self.target_hwnd) or user32.IsIconic(self.target_hwnd):
            return False
        rc = wt.RECT()
        if not user32.GetClientRect(self.target_hwnd, ctypes.byref(rc)):
            return False
        return (rc.right - rc.left) >= 64 and (rc.bottom - rc.top) >= 64

    def update_visibility(self, has_content, now):
        """可见性每帧推导，而不是"藏了就忘"：屏蔽 / 目标不可用 / 没内容，三者任一成立就隐藏。"""
        target_ok = True if self.args.size else self.target_ok()
        want = bool(self.enabled and has_content and self.dib_bits and target_ok)
        self.set_visible(want)
        if want:
            self.reassert_topmost(now)

    def toggle_enabled(self):
        self.enabled = not self.enabled
        if not self.enabled:
            self.set_visible(False)
        log(
            "浮层已%s（%s）"
            % ("启用" if self.enabled else "屏蔽", "有人说话时显示" if self.enabled else "暂停显示")
        )

    # -- 托盘 --

    def add_tray(self):
        self.hicon = create_tray_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
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
        nid.hWnd = self.hwnd
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
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_TIP
        nid.szTip = text[:127]
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def remove_tray(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def show_menu(self):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        user32.AppendMenuW(menu, MF_STRING, IDM_TOGGLE,
                           "屏蔽浮层（暂停显示）" if self.enabled else "启用浮层")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        flags = MF_STRING | (MF_CHECKED if autostart_enabled() else 0)
        user32.AppendMenuW(menu, flags, IDM_AUTOSTART, "开机自动启动")
        user32.AppendMenuW(menu, MF_STRING, IDM_LOG, "打开日志文件")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, IDM_EXIT, "退出")

        pt = wt.POINT(0, 0)
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.hwnd, None
        )
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)

        if cmd == IDM_TOGGLE:
            self.toggle_enabled()
        elif cmd == IDM_AUTOSTART:
            set_autostart(not autostart_enabled())
        elif cmd == IDM_LOG:
            try:
                os.startfile(LOG_PATH)
            except Exception as exc:
                log("打开日志失败: %s" % exc)
        elif cmd == IDM_EXIT:
            self.running = False

    def _proc(self, hwnd, msg, wparam, lparam):
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

    # -- 目标窗口 --

    def refresh_target(self):
        """跟随前台的 EVE 客户端。

        EVE-O-Preview 切窗口的做法是把非当前客户端最小化并挪到屏幕外，所以不能死锁一个窗口；
        只要前台窗口是 EVE 客户端就搬过去，前台不是 EVE（比如 Alt-Tab 到 Mumble）时保持不动。
        候选窗口要稳定 300ms 才真正切换，避免焦点抖动导致目标乱跳。
        """
        wins = find_eve_windows(self.pid_filter)
        if not wins:
            self.fg_seen = None
            return False
        fg = user32.GetForegroundWindow()
        fg_win = next((w for w in wins if w[0] == fg), None)
        if fg_win is not None:
            cand = fg_win
            src = "前台"
        else:
            cur = next((w for w in wins if w[0] == self.target_hwnd), None)
            cand = cur if cur is not None else wins[0]
            src = "保持" if cur is not None else "兜底"

        now = time.time()
        if cand[0] != self.fg_seen:
            self.fg_seen = cand[0]
            self.fg_since = now
        if cand[0] != self.target_hwnd:
            if now - self.fg_since < 0.3:
                return True
            log(
                "候选目标 0x%X pid=%d（%s，前台=0x%X，可见客户端 %d 个）"
                % (cand[0], cand[1], src, fg, len(wins))
            )
        return self.attach(cand)

    def attach(self, cand):
        hwnd, pid, w, h = cand
        first = self.target_hwnd is None
        size_changed = (w, h) != (self.width, self.height)
        self.pos = client_origin(hwnd)
        if hwnd == self.target_hwnd and not size_changed and self.dib_bits:
            return True
        self.target_hwnd, self.target_pid = hwnd, pid
        if size_changed:
            if not self.ensure_canvas(w, h):
                return False
            ctypes.memset(self.dib_bits, 0, w * h * 4)
            log("画布重建为 %dx%d，重连管道（窗口 0x%X pid=%d）" % (w, h, hwnd, pid))
            self.pipe.connect(w, h)
        if first:
            log("锁定 EVE 窗口 0x%X pid=%d 客户区 %dx%d" % (hwnd, pid, w, h))
        elif not size_changed:
            log("切换到 EVE 窗口 0x%X pid=%d（同尺寸，无需重连）" % (hwnd, pid))
        return True

    # -- 主循环 --

    def run(self):
        self.create_window()
        self.add_tray()

        hotkey = None
        for mods, vk, name in (
            (MOD_CONTROL | MOD_ALT, ord("O"), "Ctrl+Alt+O"),
            (MOD_CONTROL | MOD_ALT, ord("M"), "Ctrl+Alt+M"),
            (MOD_ALT, 0x77, "Alt+F8"),
        ):
            if user32.RegisterHotKey(self.hwnd, 1, mods, vk):
                hotkey = name
                break
        log("托盘已启动，热键：%s" % (hotkey or "无（都被占用）"))
        log("日志：%s" % LOG_PATH)
        if not self.args.duration:
            self.balloon(
                APP_TITLE,
                "已启动并在托盘常驻。右键托盘图标可设置开机自启；双击图标可临时隐藏/显示。",
            )

        fixed = None
        if self.args.size:
            fixed = tuple(int(v) for v in self.args.size.lower().split("x"))
            self.pos = (100, 100)
            self.ensure_canvas(*fixed)

        try:
            while self.running:
                self.pump_messages()
                now = time.time()

                if self.args.duration and now - self.start >= self.args.duration:
                    log("自测结束，共 %d 批位图更新" % self.blit_seen)
                    break

                if fixed is None:
                    if now >= self.scan_at:
                        self.scan_at = now + 0.4
                        if not self.refresh_target():
                            self.set_tip("%s：等待 EVE 窗口" % APP_TITLE)
                    if self.target_hwnd:
                        self.follow_window(now)

                if self.dib_bits and not self.pipe.h and now >= self.reconnect_at:
                    if not self.pipe.connect(self.width, self.height):
                        self.reconnect_at = now + 3.0
                        self.set_tip("%s：等待 Mumble" % APP_TITLE)
                    else:
                        log("已连接 Mumble 覆盖层管道")
                        self.set_tip("%s：已连接，待机" % APP_TITLE)

                if self.pipe.pump() and self.pipe.shm_ptr and self.dib_bits:
                    self.blit_seen += 1
                    ctypes.memmove(
                        self.dib_bits, ctypes.c_void_p(self.pipe.shm_ptr),
                        self.width * self.height * 4,
                    )
                    self.present()

                active = self.pipe.active
                if active != self.last_active:
                    self.last_active = active
                    if active[2] and active[3]:
                        if not self.first_content:
                            self.first_content = True
                            log("检测到说话者")
                        self.set_tip("%s：显示中" % APP_TITLE)
                    elif self.first_content:
                        self.set_tip("%s：待机（无人说话）" % APP_TITLE)

                self.update_visibility(bool(active[2] and active[3]), now)

                if (
                    self.pipe.h and not self.first_content and not self.hint_shown
                    and now - self.start >= 15
                ):
                    self.hint_shown = True
                    log("15 秒内收到管道但没有任何说话者：确认 Mumble 已连服务器且频道里有人说话")

                time.sleep(0.008)
        finally:
            self.shutdown()

    def follow_window(self, now):
        """只负责跟随位置与尺寸；显示与否统一由 update_visibility 决定。"""
        if not self.target_hwnd or not user32.IsWindow(self.target_hwnd):
            return
        if user32.IsIconic(self.target_hwnd):
            return
        rc = wt.RECT()
        if not user32.GetClientRect(self.target_hwnd, ctypes.byref(rc)):
            return
        cw, ch = rc.right - rc.left, rc.bottom - rc.top
        if cw < 64 or ch < 64:
            return
        if (cw, ch) != (self.width, self.height):
            if self.pending_resize != (cw, ch):
                self.pending_resize = (cw, ch)
                self.resize_deadline = now + 0.6
            elif now >= self.resize_deadline:
                if self.ensure_canvas(cw, ch):
                    log("EVE 客户区变化 -> %dx%d，重建画布并重连" % (cw, ch))
                    ctypes.memset(self.dib_bits, 0, cw * ch * 4)
                    self.pipe.connect(cw, ch)
                self.pending_resize = None
        else:
            self.pending_resize = None
        new_pos = client_origin(self.target_hwnd)
        if new_pos != self.pos:
            self.pos = new_pos
            if self.showing:
                self.present()

    def shutdown(self):
        self.pipe.close()
        self.remove_tray()
        if self.hicon:
            user32.DestroyIcon(self.hicon)
            self.hicon = None
        self.destroy_canvas()
        if self.hwnd:
            user32.UnregisterHotKey(self.hwnd, 1)
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
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
    ap.add_argument("--size", default=None, help="调试画布 宽x高")
    ap.add_argument("--duration", type=float, default=0)
    ap.add_argument("--autostart", choices=("on", "off"), default=None)
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
