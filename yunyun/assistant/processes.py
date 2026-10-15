"""电源和程序（阶段 11.6e）：锁屏 / 睡眠 / 关机 / 重启 / 取消关机，列出正在运行的程序、结束一个程序。
只在助手子进程里用（tools/system.py；权限关卡要看"结束的是什么"时也用 facts）。

- 列出：按程序名合并（chrome.exe ×12），带占的内存，按内存从大到小，最多 MAX_LISTED 个——回答"谁在吃内存"；
- 结束：按程序名（或进程号）结束，直接结束（没保存的会丢，确认卡片上说了）。一律不结束：芸芸自己（本体、助手子进程
  和它们开的 Claude Code、浏览器、语音……整棵树）、系统进程（会话 0 的服务、SYSTEM_NAMES 里的 Windows 核心进程）；
- 关机 / 重启：shutdown.exe 留 GRACE_S 秒（Windows 会提示），这期间 "cancel" 能撤回（shutdown /a）；
  睡眠等 SLEEP_DELAY_S 秒再睡（芸芸先把话说完）。

真的去碰 Windows 的只有 _snapshot / _memory / _session / _terminate / _apply_power：测试里换成假的（conftest），
绝不真的结束程序、关机。日志只记程序名和个数。
"""
import ctypes
import logging
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass

from yunyun.assistant.permissions import ElementFacts

log = logging.getLogger(__name__)

MAX_LISTED = 30
GRACE_S = 60          # 关机 / 重启留多少秒（这期间能取消）
SLEEP_DELAY_S = 5     # 睡眠前等几秒（芸芸先把话说完）
# Windows 的核心进程：结束了可能死机、注销，或者桌面没了。会话 0 的（服务）另外按会话认
SYSTEM_NAMES = {
    "system", "system idle process", "registry", "secure system", "memory compression", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "explorer.exe", "audiodg.exe", "spoolsv.exe", "conhost.exe",
    "runtimebroker.exe", "taskhostw.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe", "searchhost.exe",
    "textinputhost.exe", "applicationframehost.exe", "dllhost.exe", "msmpeng.exe", "nissrv.exe",
    "securityhealthservice.exe", "securityhealthsystray.exe", "searchindexer.exe", "wudfhost.exe", "lsm.exe",
}
# 关机 / 重启时 Windows 提示框里的那句话
_COMMENTS = {
    "zh": "芸芸：{seconds} 秒后{what}。要取消就跟芸芸说\u201c取消关机\u201d。",
    "en": "Yunyun: {what} in {seconds} seconds. Tell Yunyun \u201ccancel\u201d to stop it.",
    "ja": "芸芸：{seconds} 秒後に{what}。取り消すなら芸芸に\u300c取り消して\u300dと言ってください。",
}
_WHAT = {"zh": {"shutdown": "关机", "restart": "重启"}, "en": {"shutdown": "shutting down", "restart": "restarting"},
         "ja": {"shutdown": "シャットダウンします", "restart": "再起動します"}}


@dataclass
class Proc:
    pid: int
    ppid: int
    name: str


def running():
    return [Proc(*p) for p in _snapshot()]


def own_tree(procs, own):
    """芸芸自己的进程（own）和它们开的所有子孙进程。"""
    children = {}
    for p in procs:
        children.setdefault(p.ppid, []).append(p.pid)
    found, todo = set(), [pid for pid in own]
    while todo:
        pid = todo.pop()
        if pid in found:
            continue
        found.add(pid)
        todo += [child for child in children.get(pid, ()) if child not in found and child != pid]
    return found


def _default_own():
    return {os.getpid(), os.getppid()}


def _size(memory):
    return f"{memory / 2**30:.1f} GB" if memory >= 2**30 else f"{memory / 2**20:.0f} MB"


def list_text(own=None):
    """给模型看的：一行一个程序（同名的合并），占内存多的在前。芸芸自己的不列。"""
    procs = running()
    mine = own_tree(procs, own or _default_own())
    groups = {}
    for p in procs:
        if p.pid in mine or p.pid == 0:
            continue
        group = groups.setdefault(p.name.casefold(), [p.name, 0, 0])
        group[1] += 1
        group[2] += _memory(p.pid)
    ranked = sorted(groups.values(), key=lambda g: g[2], reverse=True)
    lines = [f"{name}{f' ×{count}' if count > 1 else ''} · {_size(memory)}"
             f"{' (part of Windows)' if name.casefold() in SYSTEM_NAMES else ''}"
             for name, count, memory in ranked[:MAX_LISTED]]
    head = (f"{len(groups)} programs are running (Yunyun's own not counted); the {len(lines)} using the most "
            f"memory:" if len(groups) > len(lines) else f"{len(groups)} programs are running (Yunyun's own not counted):")
    return head + "\n" + "\n".join(lines)


def _targets(tool_input, own):
    """要结束的那些进程（芸芸自己的去掉）、程序名、有没有芸芸自己的。"""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    name = str(tool_input.get("name") or "").strip()
    pid = tool_input.get("pid") if isinstance(tool_input.get("pid"), int) else None
    procs = running()
    mine = own_tree(procs, own or _default_own())
    matched = [p for p in procs if p.pid == pid] if pid is not None else \
        [p for p in procs if name and p.name.casefold() == name.casefold()]
    return [p for p in matched if p.pid not in mine], (matched[0].name if matched else name), bool(matched)


def facts(tool_input, own=None):
    """权限关卡要看的：结束的是哪个程序、几个进程，是不是芸芸自己 / 系统的（ElementFacts）。"""
    targets, name, matched = _targets(tool_input, own)
    if not matched:
        return ElementFacts(problem=f"No running program is called {name!r}. Use list_processes to see the exact "
                                    "names.")
    if not targets:
        return ElementFacts(site=name, forbidden="own")
    if name.casefold() in SYSTEM_NAMES or any(_session(p.pid) == 0 for p in targets):
        return ElementFacts(site=name, forbidden="system")
    return ElementFacts(site=name, element=f"{name} ×{len(targets)}" if len(targets) > 1 else name)


def end(tool_input, own=None):
    """结束（你已经同意了）：同名的都结束，芸芸自己的、系统的不碰。返回给模型的说明。"""
    found = facts(tool_input, own)
    if found.problem or found.forbidden:   # 权限规则已经挡过，这里兜底
        return False, found.problem or "Yunyun doesn't end that program."
    targets, name, _ = _targets(tool_input, own)
    failed = sum(not _terminate(p.pid) for p in targets)
    log.info("结束程序：%s，%d 个进程，%d 个没结束", name, len(targets), failed)
    if failed == len(targets):
        return False, f"Windows didn't let Yunyun end {name} (it may run as administrator)."
    done = f"Ended {name} ({len(targets) - failed} process{'es' if len(targets) - failed > 1 else ''})."
    return True, done + (f" {failed} of them couldn't be ended." if failed else "")


def power(action, language="zh"):
    """锁屏 / 睡眠 / 关机 / 重启 / 取消关机 → (成没成, 给模型的说明)。"""
    language = language if language in _COMMENTS else "zh"
    comment = ""
    if action in ("shutdown", "restart"):
        comment = _COMMENTS[language].format(seconds=GRACE_S, what=_WHAT[language][action])
    ok, detail = _apply_power(action, comment)
    log.info("电源：%s，%s", action, "成了" if ok else "没成")
    if not ok:
        return False, detail or f"Windows didn't {action}."
    return True, {
        "lock": "Locked the screen.",
        "sleep": f"The computer will sleep in {SLEEP_DELAY_S} seconds.",
        "shutdown": f"Windows will shut down in {GRACE_S} seconds (it shows a notice); the user can still cancel: "
                    "use action=cancel if they ask.",
        "restart": f"Windows will restart in {GRACE_S} seconds (it shows a notice); the user can still cancel: "
                   "use action=cancel if they ask.",
        "cancel": "Cancelled the shutdown / restart.",
    }[action]


# ---------- 真的去碰 Windows 的地方（测试里换成假的） ----------

if sys.platform == "win32":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *((n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                                     "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                                     "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage"))]

    for _func, _restype, _argtypes in (
        (kernel32.CreateToolhelp32Snapshot, wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD]),
        (kernel32.Process32FirstW, wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]),
        (kernel32.Process32NextW, wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]),
        (kernel32.OpenProcess, wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
        (kernel32.CloseHandle, wintypes.BOOL, [wintypes.HANDLE]),
        (kernel32.TerminateProcess, wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
        (kernel32.K32GetProcessMemoryInfo, wintypes.BOOL,
         [wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS), wintypes.DWORD]),
        (kernel32.ProcessIdToSessionId, wintypes.BOOL, [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]),
    ):
        _func.restype, _func.argtypes = _restype, _argtypes

TH32CS_SNAPPROCESS, INVALID_HANDLE = 0x2, wintypes.HANDLE(-1).value
PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE = 0x1000, 0x0001


def _snapshot():
    """[(进程号, 父进程号, 程序名)]。"""
    handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not handle or handle == INVALID_HANDLE:
        return []
    found = []
    try:
        entry = _PROCESSENTRY32W(dwSize=ctypes.sizeof(_PROCESSENTRY32W))
        more = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            found.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            more = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return found


def _memory(pid):
    """占的内存（工作集，字节）；看不到（权限不够）是 0。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0
    try:
        counters = _PROCESS_MEMORY_COUNTERS(cb=ctypes.sizeof(_PROCESS_MEMORY_COUNTERS))
        ok = kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize if ok else 0
    finally:
        kernel32.CloseHandle(handle)


def _session(pid):
    """进程在哪个会话（0 = 系统服务）；看不到当成 0（不碰）。"""
    session = wintypes.DWORD()
    return session.value if kernel32.ProcessIdToSessionId(pid, ctypes.byref(session)) else 0


def _terminate(pid):
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def _apply_power(action, comment=""):
    """真的锁屏 / 睡眠 / 关机……→ (成没成, 没成的原因)。"""
    if action == "lock":
        return bool(ctypes.WinDLL("user32").LockWorkStation()), ""
    if action == "sleep":   # 等几秒再睡：芸芸先把话说完（SetSuspendState 睡醒才返回）
        timer = threading.Timer(SLEEP_DELAY_S, lambda: ctypes.WinDLL("powrprof").SetSuspendState(False, False, False))
        timer.daemon = True
        timer.start()
        return True, ""
    command = {"shutdown": ["/s", "/t", str(GRACE_S), "/c", comment], "restart": ["/r", "/t", str(GRACE_S), "/c", comment],
               "cancel": ["/a"]}[action]
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "shutdown.exe")
    result = subprocess.run([exe, *command], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
                            timeout=15, check=False)
    if result.returncode == 0:
        return True, ""
    if action == "cancel" and result.returncode == 1116:   # ERROR_NO_SHUTDOWN_IN_PROGRESS
        return False, "No shutdown or restart was scheduled."
    if result.returncode == 1190:   # ERROR_SHUTDOWN_IS_SCHEDULED
        return False, "A shutdown / restart is already scheduled (use action=cancel first)."
    return False, f"shutdown.exe failed (code {result.returncode})."
