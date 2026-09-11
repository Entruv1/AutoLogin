# -*- coding: utf-8 -*-
"""Win32 底层能力：DPI 感知 / 屏幕截取 / 鼠标键盘注入 / 剪贴板。

全部通过 ctypes 直调系统 API，不依赖 pywin32、pyautogui、mss 等第三方库，
便于 PyInstaller 打包成体积可控的单文件 exe。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# ---------------------------------------------------------------- DPI / 屏幕

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def set_dpi_aware() -> None:
    """让进程以物理像素工作，避免系统缩放导致坐标被二次换算。"""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def virtual_screen() -> tuple[int, int, int, int]:
    """返回整个虚拟桌面的 (left, top, width, height)，多屏时为并集。"""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# ------------------------------------------------------------------- 屏幕截取


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetDC.restype = wintypes.HANDLE
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HANDLE]
gdi32.CreateCompatibleDC.restype = wintypes.HANDLE
gdi32.CreateCompatibleDC.argtypes = [wintypes.HANDLE]
gdi32.DeleteDC.argtypes = [wintypes.HANDLE]
gdi32.CreateCompatibleBitmap.restype = wintypes.HANDLE
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HANDLE
gdi32.SelectObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
gdi32.BitBlt.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.GetDIBits.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT,
]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0


def grab_screen() -> tuple[np.ndarray, int, int]:
    """截取整个虚拟桌面。

    返回 (BGR 图像, origin_x, origin_y)。图像像素坐标 + origin 即为屏幕绝对坐标。
    """
    left, top, width, height = virtual_screen()
    src_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bitmap = gdi32.CreateCompatibleBitmap(src_dc, width, height)
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        gdi32.BitBlt(mem_dc, 0, 0, width, height, src_dc, left, top, SRCCOPY | CAPTUREBLT)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 负值 => 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB

        buf_size = width * height * 4
        handle = kernel32.GlobalAlloc(0x0040, buf_size)  # GHND
        ptr = kernel32.GlobalLock(handle)
        try:
            gdi32.GetDIBits(mem_dc, bitmap, 0, height, ctypes.c_void_p(ptr), ctypes.byref(info), DIB_RGB_COLORS)
            raw = ctypes.string_at(ptr, buf_size)
        finally:
            kernel32.GlobalUnlock(handle)
            kernel32.GlobalFree(handle)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
        return np.ascontiguousarray(arr[:, :, :3]), left, top
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, src_dc)


def grab() -> tuple[np.ndarray, tuple[int, int]]:
    """截屏并返回 (BGR 图像, 屏幕原点)，原点用于把图像坐标换算成屏幕绝对坐标。"""
    img, ox, oy = grab_screen()
    return img, (ox, oy)


# ---------------------------------------------------------------- 窗口激活

# 只用来"把目标浏览器窗口拉到前台"，不读取页面内容。
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]

SW_RESTORE = 9


def list_windows() -> list[tuple[int, str]]:
    """所有可见顶层窗口的 (hwnd, 标题)。"""
    out: list[tuple[int, str]] = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title:
            out.append((int(hwnd), title))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def find_window(substr: str) -> int | None:
    """按标题子串找窗口（不区分大小写），返回第一个命中的 hwnd。"""
    key = (substr or "").strip().lower()
    if not key:
        return None
    for hwnd, title in list_windows():
        if key in title.lower():
            return hwnd
    return None


def activate_window(hwnd: int) -> bool:
    """把窗口恢复并置于前台。

    SetForegroundWindow 受前台锁定限制（非当前前台进程调用时会被系统忽略），
    这里用 AttachThreadInput 临时挂到目标线程上绕过限制。
    """
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    cur_tid = kernel32.GetCurrentThreadId()

    attached = []
    for tid in {fg_tid, cur_tid}:
        if tid and tid != target_tid and user32.AttachThreadInput(tid, target_tid, True):
            attached.append(tid)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for tid in attached:
            user32.AttachThreadInput(tid, target_tid, False)

    time.sleep(0.12)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def activate_window_by_title(substr: str) -> bool:
    """按标题子串激活窗口；找不到返回 False。"""
    hwnd = find_window(substr)
    return activate_window(hwnd) if hwnd else False



# ------------------------------------------------------------------ 鼠标注入

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
KEYEVENTF_KEYUP = 0x0002


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]


def _send(*inputs: _INPUT) -> None:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    vx, vy, vw, vh = virtual_screen()
    nx = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((y - vy) * 65535 / max(vh - 1, 1)))
    return max(0, min(65535, nx)), max(0, min(65535, ny))


def mouse_move(x: int, y: int) -> None:
    nx, ny = _to_absolute(x, y)
    _send(
        _INPUT(
            type=INPUT_MOUSE,
            u=_INPUTUNION(
                mi=_MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, 0)
            ),
        )
    )


def mouse_down() -> None:
    _send(_INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0))))


def mouse_up() -> None:
    _send(_INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=_MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0))))


def move_human(x: int, y: int, duration: float = 0.25) -> None:
    """把光标平滑移到目标点（用于点击前的自然移动，避免瞬移）。"""
    x0, y0 = cursor_pos()
    steps = max(int(duration * 90), 8)
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)
        mouse_move(int(x0 + (x - x0) * e), int(y0 + (y - y0) * e))
        time.sleep(duration / steps)


def click(x: int, y: int, settle: float = 0.05, smooth: bool = True) -> None:
    if smooth:
        move_human(x, y, duration=0.18)
    else:
        mouse_move(x, y)
    time.sleep(settle)
    mouse_down()
    time.sleep(0.05)
    mouse_up()


# ------------------------------------------------------------------ 键盘注入

VK_CONTROL, VK_V, VK_A, VK_RETURN, VK_ESCAPE, VK_F5 = 0x11, 0x56, 0x41, 0x0D, 0x1B, 0x74
VK_TAB, VK_BACK = 0x09, 0x08
VK_T, VK_L = 0x54, 0x4C
VK_SHIFT = 0x10


def key_vk(vk: int, up: bool = False) -> None:
    _send(_INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=_KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0))))


def hotkey(*vks: int, gap: float = 0.03) -> None:
    for vk in vks:
        key_vk(vk)
        time.sleep(gap)
    for vk in reversed(vks):
        key_vk(vk, up=True)
        time.sleep(gap)


def paste() -> None:
    hotkey(VK_CONTROL, VK_V)


def select_all() -> None:
    hotkey(VK_CONTROL, VK_A)


def press(vk: int) -> None:
    hotkey(vk)


# ------------------------------------------------------------------- 剪贴板

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]


def set_clipboard_text(text: str) -> bool:
    """写入剪贴板文本（中文账号只能靠粘贴输入，模拟按键会被输入法吞掉）。"""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        buf = (text + "\0").encode("utf-16-le")
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(buf))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, buf, len(buf))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()
