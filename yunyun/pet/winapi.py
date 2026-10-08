"""Windows 系统接口：通过 ctypes 直接调用 user32.dll / dwmapi.dll。

Qt 不提供"窗口有没有被别的窗口挡住"这种信息，所以这里直接问 Windows。

注意：
- Windows API 里的坐标都是"物理像素"，和 Qt 的逻辑像素不一样（高分屏上差一个缩放倍数）。
- 非 Windows 系统上，这些函数什么都不做（当作"没被挡住"）。
"""
import ctypes
import os
import sys
from contextlib import contextmanager
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# ---- Windows API 常量（数值来自微软官方文档）----
GW_HWNDPREV = 3                     # GetWindow：取 Z 序中"上面一个"窗口
GWL_EXSTYLE = -20                   # GetWindowLong：读扩展样式
WS_EX_TOPMOST = 0x00000008          # 扩展样式：置顶窗口
WS_EX_TRANSPARENT = 0x00000020      # 扩展样式：鼠标穿透（通常是透明的悬浮层）
DWMWA_EXTENDED_FRAME_BOUNDS = 9     # DWM：窗口实际看得见的边框范围
DWMWA_CLOAKED = 14                  # DWM：窗口是否被"隐身"
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
# SHQueryUserNotificationState 的返回值：这三种表示用户正全屏忙着，不该打扰
QUNS_BUSY = 2                       # 有程序在全屏（比如全屏看视频）
QUNS_RUNNING_D3D_FULL_SCREEN = 3    # 全屏游戏
QUNS_PRESENTATION_MODE = 4          # 演示模式（比如 PPT 放映）

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    shell32 = ctypes.WinDLL("shell32")

    # 声明每个函数的参数和返回值类型。
    # 尤其是 HWND（窗口句柄）是"指针大小"，64 位系统上占 8 字节；
    # 不声明的话 ctypes 默认当作 32 位 int，句柄会被截断，导致莫名其妙的错误。
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]          # 是否最小化
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long  # HRESULT，0 表示成功
    shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
    shell32.SHQueryUserNotificationState.restype = ctypes.c_long
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int

SW_RESTORE = 9
WS_EX_TOOLWINDOW = 0x00000080


# ---------- 顶层窗口 ----------

def top_level_windows():
    """所有顶层窗口，按 Z 序从上到下（EnumWindows 就是这个顺序）。"""
    if not IS_WINDOWS:
        return []
    found = []
    callback = WNDENUMPROC(lambda hwnd, _: found.append(hwnd) or True)
    user32.EnumWindows(callback, 0)
    return found


def foreground_window():
    """现在最前面（你在用）的窗口；没有是 0。"""
    return int(user32.GetForegroundWindow() or 0) if IS_WINDOWS else 0


def process_id(hwnd):
    return _process_id(hwnd) if IS_WINDOWS and hwnd else 0


def main_window_of(pids):
    """这几个进程里，第一个（按 pids 的顺序）有"正常窗口"（看得见、有标题、不是工具小窗）的，返回那个窗口；没有是 None。
    旁观模式用：从 hook 脚本的祖先进程里找 Claude Code 所在的终端 / 桌面版窗口。"""
    if not IS_WINDOWS or not pids:
        return None
    by_pid = {}
    for hwnd in top_level_windows():
        if (user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0
                and not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW):
            by_pid.setdefault(_process_id(hwnd), hwnd)
    return next((by_pid[pid] for pid in pids if pid in by_pid), None)


def activate(hwnd):
    """切到这个窗口（最小化了就先还原）。用户刚点了芸芸的提醒，Windows 允许这样抢到前台。"""
    if not IS_WINDOWS or not hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)

VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
SM_SWAPBUTTON = 23   # 左右键对调了（左撇子设置）：物理的"左键"在系统里是 VK_RBUTTON


def left_button_down():
    """鼠标左键现在是不是按着（不管在哪个程序里）。只看鼠标按钮，不碰键盘。
    用处：输入框失去焦点时，你可能正按着左键从资源管理器里拖文件过来——松开之前别收起。"""
    if not IS_WINDOWS:
        return False
    button = VK_RBUTTON if user32.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON
    return bool(user32.GetAsyncKeyState(button) & 0x8000)


# ---------- 置顶 ----------

def set_topmost(hwnd, on):
    """设置 / 取消窗口置顶（不移动、不改大小、不抢焦点）。"""
    if not IS_WINDOWS:
        return
    after = HWND_TOPMOST if on else HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def is_topmost(hwnd):
    if not IS_WINDOWS:
        return False
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def bring_to_front(hwnd):
    """把窗口提到所有普通窗口的最上面，但不抢键盘焦点。

    小技巧：先设成置顶（跳到最上层），再马上取消置顶，
    窗口就会留在"普通窗口里的最上面"。直接调 SetForegroundWindow 常被 Windows 拦截。
    """
    set_topmost(hwnd, True)
    set_topmost(hwnd, False)


@contextmanager
def keep_z_order(owner_hwnd):
    """在 with 块里显示从属窗口（气泡、小红点）时，保持主人窗口（芸芸）在叠放顺序中的位置不变。

    为什么需要：Windows 规定从属窗口必须在主人上面，所以显示一个从属窗口时，
    会把主人也一起拉到最前面——芸芸被别的窗口挡住时，一弹气泡就会自己"跳"出来。
    做法：先记下压在芸芸正上方的那个窗口，显示完以后，把芸芸放回那个窗口的下面；
    从属窗口会自动跟着主人一起回去。

    用法：
        with winapi.keep_z_order(int(pet.winId())):
            bubble.show()
    """
    above = _first_foreign_window_above(owner_hwnd) if IS_WINDOWS else None
    try:
        yield
    finally:
        if above:
            # hWndInsertAfter = above：把芸芸放到 above 的正下方
            user32.SetWindowPos(owner_hwnd, above, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def _first_foreign_window_above(hwnd):
    """压在 hwnd 正上方的第一个"别的程序的、看得见的、非置顶"窗口；没有就返回 None。

    跳过置顶窗口（比如任务栏）：如果把芸芸放到置顶窗口的正下方，芸芸会意外地变成置顶。
    """
    for h in _windows_above(hwnd):
        if (user32.IsWindowVisible(h) and not _is_cloaked(h) and not is_topmost(h)
                and _process_id(h) != os.getpid()):
            return h
    return None


# ---------- 用户是否在忙 ----------

def is_user_busy():
    """用户是不是正在全屏看视频、玩游戏或放 PPT。这时不应该弹出提醒。

    用的是 Windows 自己判断"现在能不能弹通知"的接口，系统通知也是根据它决定要不要打扰你。
    """
    if not IS_WINDOWS:
        return False
    state = ctypes.c_int()
    if shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return False  # 查询失败：当作不忙
    return state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)


# ---------- 遮挡检测 ----------

def covered_ratio(hwnd, inset=0, samples=10):
    """窗口（四周向内缩 inset 个物理像素）被压在它上面的其他窗口挡住了多少，返回 0.0 ~ 1.0。

    做法：在窗口区域里均匀取 samples × samples 个点，看有多少个点落在上面的窗口里。
    比精确计算多个矩形的并集面积简单得多，精度也够用。
    """
    if not IS_WINDOWS:
        return 0.0

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    left, top = rect.left + inset, rect.top + inset
    right, bottom = rect.right - inset, rect.bottom - inset
    if right <= left or bottom <= top:
        return 0.0

    covers = []
    for h in _windows_above(hwnd):
        r = _covering_rect(h)
        if r is not None and r[0] < right and r[2] > left and r[1] < bottom and r[3] > top:
            covers.append(r)  # 只保留和我们有交集的窗口
    if not covers:
        return 0.0

    hit = 0
    for i in range(samples):
        x = left + (i + 0.5) * (right - left) / samples
        for j in range(samples):
            y = top + (j + 0.5) * (bottom - top) / samples
            if any(l <= x < r and t <= y < b for l, t, r, b in covers):
                hit += 1
    return hit / (samples * samples)


def _windows_above(hwnd):
    """按 Z 序（叠放顺序）从近到远，依次产出压在 hwnd 上面的所有顶层窗口。"""
    h = user32.GetWindow(hwnd, GW_HWNDPREV)
    while h:
        yield h  # 生成器：用到一个才算一个，不用先把所有窗口都存进列表
        h = user32.GetWindow(h, GW_HWNDPREV)


def _covering_rect(hwnd):
    """如果这个窗口真的会挡住东西，返回它的矩形 (left, top, right, bottom)；否则返回 None。"""
    if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
        return None
    if _is_cloaked(hwnd):
        return None  # "隐身"窗口：在别的虚拟桌面上，或者是后台挂起的系统应用
    if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT:
        return None  # 鼠标穿透的悬浮层（比如显卡、游戏的帧数显示），通常看不见
    if _process_id(hwnd) == os.getpid():
        return None  # 自己程序的窗口（气泡、菜单）不算

    r = _frame_rect(hwnd)
    if r.right <= r.left or r.bottom <= r.top:
        return None
    return (r.left, r.top, r.right, r.bottom)


def _is_cloaked(hwnd):
    value = wintypes.DWORD()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return hr == 0 and value.value != 0


def _frame_rect(hwnd):
    """窗口实际看得见的范围。

    Win10/11 上 GetWindowRect 会把四周约 7 像素的"隐形拖拽边框"也算进去，
    DWM 的 EXTENDED_FRAME_BOUNDS 才是肉眼看到的边界；取不到时再退回 GetWindowRect。
    """
    r = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r))
    if hr != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _process_id(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value
