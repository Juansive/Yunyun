"""停止时把 Claude Code 执行命令开出来的进程结束掉（只在助手子进程里用，只用 ctypes，不 import Qt / SDK）。

为什么要自己做（2.8，tools/try_stop.py 实测）：点"停止"后 Claude Code 结束了这一轮，也结束了它开的 bash，
但 bash 再开的进程（sleep、pytest、python 脚本……）还在后台接着跑。而且 Git Bash（MSYS）开进程时中间隔着一个临时进程，
sleep.exe 的"父进程"是个早就退出的进程号——顺着进程树根本找不到它。

做法：**作业对象（Job Object）**。助手子进程一启动大脑就把自己放进一个作业（guard_this_process），之后它开的所有子孙进程
（claude.exe、bash、sleep……）都自动在这个作业里，不管中间的父进程还在不在。停止时（stop_commands）把作业里
除了"助手子进程自己、claude.exe、它们的控制台宿主 conhost"以外的都结束掉。
顺带的兜底（计划 1.8）：作业设了 KILL_ON_JOB_CLOSE，助手子进程退出或被强行结束时，作业里剩下的进程全部跟着结束。

放不进作业时（很老的 Windows、所在的作业不许嵌套）退回到"顺着进程树找 shell 的子孙"（找不到 MSYS 的孤儿，聊胜于无）。
"""
import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

KEEP = {"claude.exe", "conhost.exe"}   # 作业里不结束的（只认助手子进程的直接子进程和它们的 conhost）
SHELLS = {"bash.exe", "sh.exe", "zsh.exe", "powershell.exe", "pwsh.exe", "cmd.exe"}

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SET_QUOTA = 0x0100
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectBasicProcessIdList = 3
_JobObjectExtendedLimitInformation = 9
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# 一定要写 argtypes / restype：不写的话 64 位的句柄会被当成 32 位整数（CLAUDE.md 里记过这个坑）
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32FirstW.restype = wintypes.BOOL
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
_kernel32.Process32NextW.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
_kernel32.GetProcessTimes.restype = wintypes.BOOL
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryInformationJobObject.restype = wintypes.BOOL
_kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
_kernel32.IsProcessInJob.restype = wintypes.BOOL
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.restype = wintypes.BOOL
_STILL_ACTIVE = 259

_job = None   # 助手子进程的作业对象（guard_this_process 之后才有）


@dataclass(frozen=True)
class Proc:
    pid: int
    name: str
    created: int    # 创建时间（FILETIME，100 纳秒为单位）：确认还是同一个进程


# ---------- 作业对象 ----------

def guard_this_process():
    """把当前进程放进一个新的作业对象（只做一次）。成功返回 True。"""
    global _job
    if _job is not None:
        return True
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        log.warning("建不了作业对象（%d），停止时只能尽量结束命令", ctypes.get_last_error())
        return False
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not (_kernel32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info),
                                              ctypes.sizeof(info))
            and _kernel32.AssignProcessToJobObject(job, _kernel32.GetCurrentProcess())):
        log.warning("放不进作业对象（%d），停止时只能尽量结束命令", ctypes.get_last_error())
        _kernel32.CloseHandle(job)
        return False
    _job = job   # 不关：进程退出时系统关掉它，作业里剩下的进程跟着结束
    return True


def _job_members():
    count = 4096
    buffer = (ctypes.c_size_t * (count + 2))()   # 前两项是两个 DWORD（在 64 位上正好占一个 size_t）
    if not _kernel32.QueryInformationJobObject(_job, _JobObjectBasicProcessIdList, buffer, ctypes.sizeof(buffer),
                                               None):
        return []
    header = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))
    listed = header[1]
    ids = ctypes.cast(ctypes.addressof(buffer) + 8, ctypes.POINTER(ctypes.c_size_t))
    return [int(ids[i]) for i in range(listed)]


def _keepers(root_pid):
    """不结束的：root 自己、root 直接开的 claude.exe / conhost.exe、claude.exe 的 conhost.exe。"""
    processes = _all_processes()
    keep = {root_pid}
    for pid, ppid, name in processes:
        if ppid == root_pid and name.lower() in KEEP:
            keep.add(pid)
    claude = {pid for pid in keep if pid != root_pid}
    for pid, ppid, name in processes:
        if ppid in claude and name.lower() == "conhost.exe":
            keep.add(pid)
    return keep


def _kill_job_leftovers(root_pid):
    keep = _keepers(root_pid)
    killed = 0
    for pid in _job_members():
        if pid in keep:
            continue
        handle = _kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            continue
        try:
            inside = wintypes.BOOL()
            # 进程号可能刚被别的程序用了：确认它还在我们的作业里再动手
            if _kernel32.IsProcessInJob(handle, _job, ctypes.byref(inside)) and inside \
                    and _kernel32.TerminateProcess(handle, 1):
                killed += 1
        finally:
            _kernel32.CloseHandle(handle)
    return killed


# ---------- 进程树（作业对象用不了时） ----------

def _all_processes():
    """[(进程号, 父进程号, 程序名)]"""
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        return []
    result = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            result.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)
    return result


def _created(pid):
    """进程的创建时间；打不开（已经没了、没权限）就是 None。"""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def is_running(pid):
    """进程还在运行吗（结束了但别人还拿着句柄的，也算没在运行）。"""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def command_processes(root_pid):
    """root 下面，执行命令开的 shell 和它们（还找得到的）子孙。"""
    children, names = {}, {}
    for pid, ppid, name in _all_processes():
        if pid != ppid:
            children.setdefault(ppid, []).append(pid)
        names[pid] = name
    created = {root_pid: _created(root_pid)}
    if created[root_pid] is None:
        return []
    found, stack = [], [(root_pid, False)]
    while stack:
        pid, in_shell = stack.pop()
        for child in children.get(pid, []):
            child_created = _created(child)
            if child_created is None or child_created < created[pid]:   # 父进程号可能是被重新用过的
                continue
            created[child] = child_created
            child_in_shell = in_shell or names.get(child, "").lower() in SHELLS
            if child_in_shell:
                found.append(Proc(child, names.get(child, ""), child_created))
            stack.append((child, child_in_shell))
    return found


def kill(processes):
    """结束这些进程里还活着、而且还是同一个（创建时间对得上）的；返回结束了几个。"""
    killed = 0
    for proc in processes:
        handle = _kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, proc.pid)
        if not handle:
            continue
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                continue
            if ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) != proc.created:
                continue   # 进程号被别的程序用了
            if _kernel32.TerminateProcess(handle, 1):
                killed += 1
        finally:
            _kernel32.CloseHandle(handle)
    return killed


# ---------- 给大脑用的 ----------

def before_stop():
    """停止之前调用：作业对象用不了时，先记下正在跑的命令（停了以后父进程没了就找不到了）。"""
    return [] if _job is not None else command_processes(os.getpid())


def stop_commands(running):
    """停止之后调用：结束还在跑的命令进程；返回结束了几个。"""
    if _job is not None:
        return _kill_job_leftovers(os.getpid())
    return kill(running)
