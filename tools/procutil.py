"""开发脚本用的 Windows 小工具（tools/measure_assistant.py）：进程内存、子孙进程、还活着没有。

只用 ctypes 直接调 Windows API（不用另装 psutil）。
"""
import ctypes
import ctypes.wintypes as wt

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi")
PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_VM_READ, SYNCHRONIZE = 0x1000, 0x0010, 0x100000
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
psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(MemCounters), wt.DWORD]


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


def descendants(pid):
    """pid 的所有子孙进程：[(pid, 程序名)]。"""
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if snap == INVALID_HANDLE:
        return []
    children = {}
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append((entry.th32ProcessID, entry.szExeFile))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
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
