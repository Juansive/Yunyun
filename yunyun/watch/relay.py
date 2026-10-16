"""旁观模式（阶段 8）的 hook 脚本：你自己用的 Claude Code 发生了什么（开始干活、等你确认、做完……）就运行它，告诉芸芸。

    Claude Code ──(hook 输入 JSON，标准输入)──▶ relay.py ──(一行 JSON，命名管道)──▶ 芸芸（yunyun/watch/server.py）
                ◀──(hook 输出，标准输出)───── relay.py ◀──(一行 JSON 回话)──────── 芸芸（只有 REPLY_EVENTS 才等）

- **只用标准库**：每次 hook 都要启动一次，要快；开发版直接当脚本跑（pythonw relay.py），打包版是 芸芸.exe --watch-relay；
- 芸芸没开（管道不在）、没回话、回得太慢：悄悄放行（退出码一律 0、标准输出不写）——**绝不卡住 Claude Code**；
- 8.6（2026-10-16）"芸芸当 Claude Code 的脸"：多接了几个事件（开始干活、用完一个工具、要你批准、出错停下、会话结束、闲着）
  让芸芸跟着换样子；"要你批准"和"做完"要等芸芸回话（芸芸能替你批准 / 让它接着干，见 hook_output），
  回话只认那几种样子，别的一律不写——Stop / PermissionRequest hook 的输出 Claude Code 会照着做；
- 发给芸芸的：事件、会话编号、工作目录、一句话（最后回答的第一句 / 通知的提示，最多 MAX_TEXT 字）、要提醒的事再加
  祖先进程编号和控制台窗口（芸芸据此判断你是不是正看着 Claude Code 的窗口，点提醒时切过去）。不写日志内容。
"""
import ctypes
import getpass
import hashlib
import json
import os
import re
import sys
import time
from ctypes import wintypes

PIPE_NAME = f"yunyun-watch-{getpass.getuser()}"   # 芸芸那边 QLocalServer 的名字（只有当前用户能连）
MAX_TEXT = 200
MAX_ANCESTORS = 12
NOTIFICATIONS = {"permission_prompt": "permission", "elicitation_dialog": "choice",   # 通知种类 → 事件
                 "idle_prompt": "idle"}   # 闲着等你（被你按 Esc 打断时不会有 Stop，靠它知道不忙了；不提醒）
HOOK_EVENTS = {"UserPromptSubmit": "busy", "PostToolUse": "busy", "PostToolUseFailure": "busy",   # hook 事件 → 发给芸芸的事件
               "PermissionRequest": "approval", "StopFailure": "failed", "SessionEnd": "end"}   # （Stop、Notification 另算）
TOOL_DONE = ("PostToolUse", "PostToolUseFailure")   # 一个工具做完了（带指纹：芸芸据此知道你在 Claude 那边批准过了，收起卡片）
REPLY_EVENTS = {"approval": 590, "stop": 60}   # 要等芸芸回话的事件 → 最多等几秒（比 hooks.py 里给它的超时短）
REMIND_EVENTS = ("stop", "permission", "choice")   # 要提醒你的
WINDOW_EVENTS = (*REMIND_EVENTS, "approval")   # 要看你是不是正看着 Claude Code 的（才查祖先进程、控制台窗口）
MAX_REPLY = 8192
MAX_INPUT = 48_000   # 要批准的那件事（tool_input）最多带多少字给芸芸：卡片上看个大概，全文在 Claude 那边
POLL_S = 0.02


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
    """hook 的输入 → 要发给芸芸的事（芸芸不关心的返回 None）。"""
    name = hook.get("hook_event_name")
    if name == "Stop":
        event, text = "stop", first_line(hook.get("last_assistant_message"))
    elif name == "Notification" and hook.get("notification_type") in NOTIFICATIONS:
        event, text = NOTIFICATIONS[hook["notification_type"]], first_line(hook.get("message"))
    elif name in HOOK_EVENTS:
        event, text = HOOK_EVENTS[name], ""
    else:
        return None
    message = {"event": event, "session": str(hook.get("session_id") or "")[:100], "cwd": str(hook.get("cwd") or ""),
               "text": text}
    if name == "PermissionRequest" or name in TOOL_DONE:
        message["tool"] = str(hook.get("tool_name") or "")[:100]
        message["fp"] = fingerprint(hook.get("tool_name"), hook.get("tool_input"))
    if event == "approval":   # 8.6b：芸芸的确认卡片上要写清它要做什么（只走本机的管道，不记日志）
        message["input"] = trimmed(hook.get("tool_input"))
    if event in REPLY_EVENTS:
        message["reply"] = True
    return message


def fingerprint(tool, tool_input):
    """一次工具调用的指纹：要批准的那件事 ↔ 后来做完的那件事对得上（PermissionRequest 里没有 tool_use_id）。"""
    raw = json.dumps([tool, tool_input], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def trimmed(tool_input, limit=MAX_INPUT):
    """要批准的那件事 → 发给芸芸的：只留一层（里面的列表 / 字典变成一段文字），太长的字段截断到总共不超过 limit 字。"""
    if not isinstance(tool_input, dict):
        return {}
    fields = {str(key)[:100]: value if isinstance(value, (str, int, float, bool)) or value is None
              else json.dumps(value, ensure_ascii=False, default=str) for key, value in list(tool_input.items())[:30]}
    chars = limit
    while chars > 50:
        result = {key: value[:chars] if isinstance(value, str) else value for key, value in fields.items()}
        if len(json.dumps(result, ensure_ascii=False)) <= limit:
            return result
        chars //= 2
    return {}


def hook_output(event, reply):
    """芸芸的回话 → 给 Claude Code 的 hook 输出（不是认得的样子就是 None：什么都不写，照常进行）。
    approval：{"behavior": "allow" / "deny", "message": 拒绝的理由} → 替你批准 / 拒绝（它自己的确认框随之消失）；
    stop：{"decision": "block", "reason": 你接着说的话} → 它不停下、照着这句接着干（界面上是"Hook 已重新提示 Claude"）。"""
    if not isinstance(reply, dict):
        return None
    if event == "approval" and reply.get("behavior") in ("allow", "deny"):
        decision = {"behavior": reply["behavior"]}
        if reply["behavior"] == "deny" and isinstance(reply.get("message"), str):
            decision["message"] = reply["message"][:MAX_REPLY]
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}
    reason = reply.get("reason")
    if event == "stop" and reply.get("decision") == "block" and isinstance(reason, str) and reason.strip():
        return {"decision": "block", "reason": reason[:MAX_REPLY]}
    return None


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


def send(message, name, wait=0):
    """发一行给芸芸；wait > 0：再等芸芸回一行（最多 wait 秒），返回解开的 JSON（没回 / 断了 / 看不懂是 None）。"""
    with open(pipe_path(name), "r+b", buffering=0) as pipe:
        pipe.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        line = _receive(pipe, wait) if wait else None
    try:
        return json.loads(line) if line else None
    except ValueError:
        return None


def _receive(pipe, timeout):
    """等芸芸回的一行。用 PeekNamedPipe 看有没有字再读（直接读会一直卡着，超时也停不下来）；芸芸关了 / 断开就不等了。"""
    import msvcrt
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.PeekNamedPipe.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(pipe.fileno())
    available, data = wintypes.DWORD(), b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            return None   # 芸芸那头断开了
        if not available.value:
            time.sleep(POLL_S)
            continue
        data += pipe.read(available.value)
        if b"\n" in data:
            return data.split(b"\n", 1)[0]
        if len(data) > MAX_REPLY:
            return None
    return None


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
            if message["event"] in WINDOW_EVENTS:
                message["ancestors"] = ancestors(os.getpid())
                message["console"] = console_window()
            reply = send(message, os.environ.get("YUNYUN_WATCH_PIPE") or PIPE_NAME, REPLY_EVENTS.get(message["event"], 0))
            output = hook_output(message["event"], reply)
            if output:   # 写底层描述符：pythonw 下 sys.stdout 是 None
                os.write(1, json.dumps(output, ensure_ascii=False).encode("utf-8"))
    except FileNotFoundError:   # 芸芸没开：正常，悄悄退出
        pass
    except (OSError, ValueError) as e:
        _note_problem(type(e).__name__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
