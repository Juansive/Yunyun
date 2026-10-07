"""开发脚本共用的 Windows 小工具：进程内存、进程树、可见窗口、清理继承的环境变量。

只用 ctypes 直接调 Windows API（不用另装 psutil），也不 import Agent SDK——
tools/try_assistant_ipc.py 的芸芸一侧要证明自己"从没 import 过 SDK"，所以不能经由这里间接 import。
"""
import ctypes
import ctypes.wintypes as wt
import os

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
psapi = ctypes.WinDLL("psapi")
PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ, PROCESS_TERMINATE, SYNCHRONIZE = 0x1000, 0x0010, 0x0001, 0x100000
INVALID_HANDLE = ctypes.c_void_p(-1).value


class MemCounters(ctypes.Structure):   # PROCESS_MEMORY_COUNTERS_EX
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


class ProcessEntry(ctypes.Structure):   # PROCESSENTRY32W
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


# 每个函数都声明参数类型：不声明的话，64 位的句柄会被当成 32 位整数，溢出报错
k32.OpenProcess.argtypes, k32.OpenProcess.restype = [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CreateToolhelp32Snapshot.argtypes, k32.CreateToolhelp32Snapshot.restype = [wt.DWORD, wt.DWORD], wt.HANDLE
k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(ProcessEntry)]
k32.WaitForSingleObject.argtypes, k32.WaitForSingleObject.restype = [wt.HANDLE, wt.DWORD], wt.DWORD
k32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
k32.GetConsoleWindow.restype = wt.HWND
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(MemCounters), wt.DWORD]
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]


def memory_mb(pid):
    """(私有内存, 工作集)，单位 MB；进程不在了返回 None。"""
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return None
    try:
        counters = MemCounters()
        counters.cb = ctypes.sizeof(MemCounters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return counters.PrivateUsage / 2**20, counters.WorkingSetSize / 2**20
    finally:
        k32.CloseHandle(handle)


def _snapshot():
    """系统里所有进程：[(pid, 父进程 pid, 程序名)]。"""
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if snap == INVALID_HANDLE:
        return []
    rows = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            rows.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return rows


def process_names():
    return {pid: name for pid, _, name in _snapshot()}


def descendants(pid):
    """pid 的所有子孙进程：[(pid, 程序名)]。"""
    children = {}
    for child, parent, name in _snapshot():
        children.setdefault(parent, []).append((child, name))
    found, todo = [], [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child[0])
    return found


def is_alive(pid):
    handle = k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        return k32.WaitForSingleObject(handle, 0) == 258   # WAIT_TIMEOUT：还没结束
    finally:
        k32.CloseHandle(handle)


def kill(pid):
    handle = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        k32.TerminateProcess(handle, 1)
        k32.CloseHandle(handle)


def visible_windows():
    """屏幕上所有可见的顶层窗口：{窗口句柄: (窗口类名, 标题, 所属进程 pid)}。"""
    found = {}

    @WNDENUMPROC
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            cls, title, pid = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256), wt.DWORD()
            user32.GetClassNameW(hwnd, cls, 256)
            user32.GetWindowTextW(hwnd, title, 256)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found[hwnd] = (cls.value, title.value, pid.value)
        return True

    user32.EnumWindows(callback, 0)
    return found


def own_console():
    """(这个进程有没有控制台, 控制台窗口是否可见)。"""
    hwnd = k32.GetConsoleWindow()
    return bool(hwnd), bool(hwnd) and bool(user32.IsWindowVisible(hwnd))


def clean_inherited_env():
    """去掉从父进程继承来的 Claude 相关变量，返回删掉的变量名（不打印值）。

    在 Claude 桌面版里运行时，环境里有 ANTHROPIC_BASE_URL 等变量（桌面版自己用的）。
    原样传给 Claude Code 子进程的话，它可能走桌面版的通道，而不是用你的 API Key。
    """
    names = [k for k in os.environ if k.startswith(("ANTHROPIC_", "CLAUDE_", "MCP_")) or k == "CLAUDECODE"]
    for name in names:
        del os.environ[name]
    return names
