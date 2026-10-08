"""看屏幕（阶段 5）：芸芸进程里的截图。助手子进程要截图时发 ScreenshotRequest，由这里截好、缩小、编码成 base64。

- 截哪里（2026-10-08 用户定）："window" = 你切到芸芸之前用的那个窗口（按 Z 序在芸芸下面的第一个正常窗口），
  找不到就截 "screen" = 芸芸所在的那块屏幕；
- 芸芸自己的窗口（芸芸、气泡、卡片、输入框……）不截：截之前给它们设 SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)，
  截完马上取消（实测立刻生效、屏幕上不闪，也不影响你自己截图、共享屏幕）；
- 用 GDI 的 BitBlt 按物理像素截（Qt 6 的进程是"每个屏幕各自 DPI"，Windows 给的坐标都是物理像素，不用换算）；
- 缩小到最长边 ≤ MAX_SIDE（Claude 看图片时更大的也会被缩到这么大），存成 JPEG：一张约 1500 token。

非 Windows 系统上截不了（take 返回 None）。
"""
import base64
import ctypes
import logging
import os
from contextlib import contextmanager
from ctypes import wintypes

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

from yunyun.pet import winapi

log = logging.getLogger(__name__)

MAX_SIDE = 1568          # 缩小后的最长边（像素）
JPEG_QUALITY = 80
MIN_WINDOW = (120, 80)   # 比这还小的窗口不算"你在用的窗口"（小工具条、弹出的提示）
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}   # 桌面、任务栏

WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT = 0x80, 0x20
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
MONITOR_DEFAULTTONEAREST = 2
BI_RGB, DIB_RGB_COLORS = 0, 0


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


if winapi.IS_WINDOWS:
    user32, gdi32 = winapi.user32, ctypes.WinDLL("gdi32")
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL


# ---------- 窗口 ----------

def _top_level_windows():
    """所有顶层窗口，按 Z 序从上到下（EnumWindows 就是这个顺序）。"""
    found = []
    callback = WNDENUMPROC(lambda hwnd, _: found.append(hwnd) or True)
    user32.EnumWindows(callback, 0)
    return found


def own_windows():
    """芸芸自己的、看得见的顶层窗口（芸芸、气泡、卡片、输入框、角落小条……）。"""
    return [h for h in _top_level_windows() if user32.IsWindowVisible(h) and winapi._process_id(h) == os.getpid()]


def _class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def _is_normal_window(hwnd):
    """看起来是"你在用的窗口"：看得见、没最小化、不是别的虚拟桌面上的、有标题、不是桌面 / 任务栏 / 小工具条 / 悬浮层。"""
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or winapi._is_cloaked(hwnd):
        return False
    if winapi._process_id(hwnd) == os.getpid() or user32.GetWindowTextLengthW(hwnd) == 0:
        return False
    if user32.GetWindowLongW(hwnd, winapi.GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT):
        return False
    if _class_name(hwnd) in SHELL_CLASSES:
        return False
    r = winapi._frame_rect(hwnd)
    return r.right - r.left >= MIN_WINDOW[0] and r.bottom - r.top >= MIN_WINDOW[1]


def previous_window():
    """你切到芸芸之前用的那个窗口（芸芸的窗口不算）：Z 序里最上面的正常窗口；没有就是 None。"""
    return next((h for h in _top_level_windows() if _is_normal_window(h)), None)


@contextmanager
def excluded_from_capture(hwnds):
    """with 块里截图时，这些窗口不出现在截图里（出了 with 马上恢复）。"""
    done = [h for h in hwnds if user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE)]
    try:
        yield
    finally:
        for h in done:
            user32.SetWindowDisplayAffinity(h, WDA_NONE)


# ---------- 屏幕 ----------

def monitor_rect(x, y):
    """(x, y)（物理像素）所在那块屏幕的范围 (left, top, right, bottom)。"""
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST),
                           ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right, r.bottom


def window_center(hwnd):
    """窗口中心（物理像素）。"""
    if not winapi.IS_WINDOWS:
        return 0, 0
    r = winapi._frame_rect(hwnd)
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


def window_rect(hwnd):
    """窗口看得见的范围，裁到它所在的那块屏幕里（最大化的窗口会超出屏幕几个像素）。"""
    r = winapi._frame_rect(hwnd)
    left, top, right, bottom = monitor_rect((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    return max(r.left, left), max(r.top, top), min(r.right, right), min(r.bottom, bottom)


def capture(rect):
    """把屏幕上 rect（物理像素）这一块截成 QImage（GDI BitBlt）。截不了是 None。"""
    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    screen_dc = user32.GetDC(None)
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
    try:
        old = gdi32.SelectObject(memory_dc, bitmap)
        copied = gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, left, top, SRCCOPY | CAPTUREBLT)
        gdi32.SelectObject(memory_dc, old)   # GetDIBits 要求位图不在任何 DC 里
        if not copied:
            return None
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, BI_RGB)   # 负的高度 = 从上往下
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), DIB_RGB_COLORS):
            return None
        return QImage(pixels.raw, width, height, width * 4, QImage.Format.Format_RGB32).copy()   # copy：数据归 QImage 自己
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def encode(image, max_side=MAX_SIDE, quality=JPEG_QUALITY):
    """缩小到最长边 ≤ max_side，存成 JPEG → (base64 文字, 宽, 高)。"""
    if max(image.width(), image.height()) > max_side:
        image = image.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", quality)
    buffer.close()
    return base64.b64encode(bytes(data)).decode("ascii"), image.width(), image.height()


def take(target, near):
    """截图：target "window" / "screen"，near = 芸芸的位置（物理像素，截 "screen" 时用它找是哪块屏幕）。
    返回 (实际截的 target, base64, 宽, 高)；截不了是 None。"""
    if not winapi.IS_WINDOWS:
        return None
    with excluded_from_capture(own_windows()):
        hwnd = previous_window() if target == "window" else None
        if hwnd is not None:
            target, rect = "window", window_rect(hwnd)
        else:
            target, rect = "screen", monitor_rect(*near)
        image = capture(rect)
    if image is None:
        log.warning("截图失败（%s）", target)
        return None
    data, width, height = encode(image)
    log.info("截图：%s，%dx%d → %dx%d，%d KB", target, image.width(), image.height(), width, height,
             len(data) * 3 // 4 // 1024)
    return target, data, width, height
