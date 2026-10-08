"""旁观模式（阶段 8）的 hook 脚本：你自己用的 Claude Code 做完一轮 / 等你确认时运行它，把这件事告诉芸芸。

    Claude Code ──(hook 输入 JSON，标准输入)──▶ relay.py ──(一行 JSON，命名管道)──▶ 芸芸（yunyun/watch/server.py）

- **只用标准库**：每次 hook 都要启动一次，要快；开发版直接当脚本跑（pythonw relay.py），打包版是 芸芸.exe --watch-relay；
- 芸芸没开（管道不在）：悄悄退出。**标准输出什么都不写**、退出码一律 0——Stop hook 的输出会被 Claude Code 当成指令；
- 发给芸芸的：事件（stop / permission / choice）、工作目录、一句话（最后回答的第一句 / 通知的提示，最多 MAX_TEXT 字）、
  祖先进程编号和控制台窗口（芸芸据此判断你是不是正看着 Claude Code 的窗口，点提醒时切过去）。不写日志内容。
"""
import ctypes
import getpass
import json
import os
import re
import sys
import time
from ctypes import wintypes

PIPE_NAME = f"yunyun-watch-{getpass.getuser()}"   # 芸芸那边 QLocalServer 的名字（只有当前用户能连）
MAX_TEXT = 200
MAX_ANCESTORS = 12
NOTIFICATIONS = {"permission_prompt": "permission", "elicitation_dialog": "choice"}   # 要提醒的通知种类 → 事件


def pipe_path(name):
    return "\\\\.\\pipe\\" + name


def first_line(text):
    """第一句有内容的话（去掉 Markdown 的 # * ` > 和多余的空白），最多 MAX_TEXT 字。"""
    for line in str(text or "").splitlines():
        line = re.sub(r"[*`#>]+", "", line)
        line = " ".join(line.split())
        if line:
            return line[:MAX_TEXT]
    return ""


def message_from(hook):
    """hook 的输入 → 要发给芸芸的事（不用提醒的返回 None）。"""
    name = hook.get("hook_event_name")
    if name == "Stop":
        event, text = "stop", first_line(hook.get("last_assistant_message"))
    elif name == "Notification" and hook.get("notification_type") in NOTIFICATIONS:
        event, text = NOTIFICATIONS[hook["notification_type"]], first_line(hook.get("message"))
    else:
        return None
    return {"event": event, "cwd": str(hook.get("cwd") or ""), "text": text}


# ---------- 祖先进程（芸芸据此找 Claude Code 所在的窗口） ----------

class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def ancestors(pid):
    """pid 的父进程、祖父进程……（最多 MAX_ANCESTORS 层）。非 Windows / 查不了就是空的。"""
    if sys.platform != "win32":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    chain, seen = [], {pid}
    current = parents.get(pid)
    while current and current not in seen and len(chain) < MAX_ANCESTORS:
        chain.append(current)
        seen.add(current)
        current = parents.get(current)
    return chain


def console_window():
    """这个进程挂着的控制台窗口（老式的 cmd / PowerShell 窗口里跑 Claude Code 时有）；没有是 0。"""
    if sys.platform != "win32":
        return 0
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    return int(kernel32.GetConsoleWindow() or 0)


# ---------- 读、发 ----------

def read_stdin():
    """读完标准输入（os.read 读底层描述符：pythonw 下 sys.stdin 可能是 None）。"""
    chunks = []
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def send(message, name):
    with open(pipe_path(name), "wb", buffering=0) as pipe:
        pipe.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))


def _note_problem(problem):
    """出了意外（不是"芸芸没开"）：在芸芸的日志文件夹里记一行（只记种类，不记内容）。记不了就算了。"""
    folder = os.path.join(os.environ.get("APPDATA", ""), "Yunyun", "Yunyun", "logs")
    try:
        with open(os.path.join(folder, "watch-relay.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {problem}\n")
    except OSError:
        return


def main():
    try:
        hook = json.loads(read_stdin() or b"{}")
        message = message_from(hook) if isinstance(hook, dict) else None
        if message:
            message["ancestors"] = ancestors(os.getpid())
            message["console"] = console_window()
            send(message, os.environ.get("YUNYUN_WATCH_PIPE") or PIPE_NAME)
    except FileNotFoundError:   # 芸芸没开：正常，悄悄退出
        pass
    except (OSError, ValueError) as e:
        _note_problem(type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
