"""窗口工具在芸芸本体这边做的事（阶段 11.5）：列出你开着的窗口、切换到某个窗口、最小化、还原。不提供"关闭"。

为什么在芸芸本体做：Windows 只让"前台程序"把别的窗口提到前面，你刚在芸芸的输入框里打过字，芸芸本体就是前台程序；
助手子进程不是，从它那里切常被 Windows 拦住。助手的工具（assistant/tools/windows.py）经 Bridge 请这里做。

窗口编号是 "w" + 窗口句柄；芸芸自己的窗口不列、也不让动。窗口标题会交给模型（用户知道，2026-10-14 定的）。
_enumerate / _apply 是真的去问 / 动 Windows 的两处：测试里换成假的（conftest），绝不动你真的窗口。日志不写标题。
"""
import logging
import os
from dataclasses import dataclass

from yunyun.pet import winapi

log = logging.getLogger(__name__)

MAX_WINDOWS = 40
ACTIONS = ("switch", "minimize", "restore")


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    app: str            # 程序文件名（"WeChat.exe"），看不到是 ""
    pid: int
    minimized: bool = False


def window_id(hwnd):
    return f"w{hwnd}"


def parse_id(text):
    text = str(text or "").strip()
    return int(text[1:]) if text[:1] == "w" and text[1:].isdigit() else None


def _enumerate():
    """任务栏上会有的那些窗口（按叠放顺序从上到下）。"""
    found = []
    for hwnd in winapi.top_level_windows():
        if winapi.is_normal_window(hwnd):
            pid = winapi.process_id(hwnd)
            found.append(WindowInfo(int(hwnd), winapi.window_title(hwnd), winapi.process_name(pid), pid,
                                    winapi.is_minimized(hwnd)))
    return found


def _apply(action, hwnd):
    """真的去做；返回窗口有没有到前台（只对 switch 有意义）。"""
    if action == "minimize":
        winapi.minimize(hwnd)
        return True
    if action == "restore":
        winapi.restore(hwnd)
        return True
    winapi.activate(hwnd)            # 最小化了先还原，再要前台
    winapi.bring_to_front(hwnd)      # Windows 不给前台时，至少叠到最上面
    return winapi.foreground_window() == hwnd


def list_windows(own_pid=None):
    """你开着的窗口（芸芸自己的不算），最多 MAX_WINDOWS 个。"""
    own_pid = os.getpid() if own_pid is None else own_pid
    return [w for w in _enumerate() if w.pid != own_pid][:MAX_WINDOWS]


def describe(windows):
    """交给模型的样子：一行一个"w1234 · WeChat.exe · 微信（最小化了）"。"""
    if not windows:
        return "No windows are open."
    return "\n".join(f"{window_id(w.hwnd)} · {w.app or '?'} · {w.title}{' (minimized)' if w.minimized else ''}"
                     for w in windows)


def act(action, window, own_pid=None):
    """switch / minimize / restore 一个窗口 → (成没成, 给模型的说明)。只动 list_windows 里有的。"""
    if action not in ACTIONS:
        return False, f"Unknown action {action!r}: use switch, minimize or restore."
    hwnd = parse_id(window)
    target = next((w for w in list_windows(own_pid) if w.hwnd == hwnd), None)
    if target is None:
        return False, f"There is no window {window!r} (it may have been closed). List the windows again."
    in_front = _apply(action, hwnd)
    log.info("窗口：%s %s", action, target.app or "?")
    if action == "switch" and not in_front:
        return True, f"Brought \"{target.title}\" to the top, but Windows did not give it keyboard focus; the user " \
                     f"may need to click it."
    done = {"switch": "Switched to", "minimize": "Minimized", "restore": "Restored"}[action]
    return True, f"{done} \"{target.title}\"."
