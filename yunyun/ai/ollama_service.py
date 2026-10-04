"""需要时才启动 Ollama，用完就关（2026-10 用户的要求：降低后台常驻内存）。

Ollama 平时常驻后台约 160 MB（托盘程序 + 服务）；加载模型时还有一个干活的子进程 llama-server.exe，
用显卡时约占 0.9 GB 内存，只用 CPU 时约 2 GB。芸芸的做法：

- 要用本地模型的代码包在 `with session(base_url):` 里（处理邮件、设置页测试 / 下载模型）。
  - Ollama 已经在运行（用户自己打开的）：直接用，用完也不关——不是芸芸开的，不去动它；
  - 没在运行：芸芸在后台启动 `ollama serve`（不弹窗口、低于正常的优先级），用完空闲 IDLE_STOP_S 秒就关掉。
- 关的时候连同子进程一起结束：用 Windows 的"作业对象"（Job Object）把它们绑在一起，关掉作业就全部结束。
  只结束主进程的话，llama-server.exe 会留下来继续占着内存（实测会这样）。
  芸芸自己退出、甚至崩溃时，Windows 也会自动关掉这个作业，不会留下孤儿进程。
- 实测（开发用的笔记本）启动到能用约 2.5 秒（刚开机第一次约 4～8 秒），主要花在检测显卡上。
  所以点小红点时就提前启动、加载模型（见 ai.prepare），和读邮箱同时进行，用户基本感觉不到多等。

这个模块不依赖 Qt（在后台线程里调用），用一把锁保证线程安全。
"""
import contextlib
import ctypes
import functools
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from yunyun.ai.errors import AIError

log = logging.getLogger(__name__)

ENABLED = True          # 测试时关掉（tests/conftest.py），免得真的去启动 Ollama
IDLE_STOP_S = 20.0      # 用完后空闲多久关掉（一批邮件之间不会隔这么久；中途又要用就取消关闭）
START_TIMEOUT_S = 60.0  # 启动最多等多久

_lock = threading.RLock()   # RLock：同一个线程可以重复加锁（_acquire 里会调用 _stop）
_proc = None                # 芸芸启动的 ollama serve（subprocess.Popen）；None = 没有
_job = None                 # 绑着它和它的子进程的作业对象
_users = 0                  # 此刻有几段代码正在用 Ollama
_stop_timer = None          # "空闲一会儿后关掉"的定时器
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 连本机不走系统代理


@contextlib.contextmanager
def session(base_url):
    """在 with 块里保证 Ollama 在运行；没装 / 没能启动时抛 AIError(kind="not_running")。"""
    if not ENABLED:
        yield
        return
    _acquire(base_url)
    try:
        yield
    finally:
        _release()


def is_running(base_url, timeout=0.5):
    """Ollama 有没有在 base_url 上运行（不管是谁启动的）。"""
    try:
        with _opener.open(base_url.rstrip("/") + "/api/version", timeout=timeout) as response:
            response.read()
        return True
    except OSError:   # 连不上、超时（urllib 的错误都是 OSError 的子类）
        return False


def started_by_us():
    """此刻运行的 Ollama 是不是芸芸启动的（测试和设置页用）。"""
    with _lock:
        return _proc is not None and _proc.poll() is None


def find_exe():
    """Ollama 的程序在哪；没装就返回 None。"""
    found = shutil.which("ollama")   # 安装程序会把它加进 PATH
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(default) if default.exists() else None


def shutdown():
    """芸芸退出时调用：马上关掉芸芸启动的 Ollama（不等空闲时间）。"""
    with _lock:
        _cancel_stop_timer()
        _stop()


# ---------- 内部 ----------

def _command(exe):
    return [exe, "serve"]   # 单独写成函数：测试时换成一个假的服务器


def _acquire(base_url):
    global _users
    with _lock:   # 启动要几秒：期间别的线程也想用，就在这里排队，等同一次启动完成
        _users += 1
        _cancel_stop_timer()
        try:
            if started_by_us() or is_running(base_url):
                return
            _start(base_url)
        except BaseException:
            _users -= 1
            raise


def _release():
    global _users, _stop_timer
    with _lock:
        _users -= 1
        if _users == 0 and _proc is not None:
            _stop_timer = threading.Timer(IDLE_STOP_S, _stop_if_idle)
            _stop_timer.daemon = True
            _stop_timer.start()


def _stop_if_idle():
    with _lock:
        if _users == 0:
            _stop()


def _cancel_stop_timer():
    global _stop_timer
    if _stop_timer is not None:
        _stop_timer.cancel()
        _stop_timer = None


def _start(base_url):
    global _proc, _job
    _stop()   # 上次启动的如果自己崩了，先清理干净
    exe = find_exe()
    if exe is None:
        raise AIError("没找到 Ollama", kind="not_running", detail="Ollama 没有安装")
    host = urlparse(base_url).netloc   # "127.0.0.1:11434"
    env = dict(os.environ, OLLAMA_HOST=host, OLLAMA_NO_CLOUD="1")   # 芸芸用不到 Ollama 的云端功能，关掉
    started = time.monotonic()
    proc = subprocess.Popen(
        _command(exe), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,   # 不保存 Ollama 的输出
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),                          # 不弹黑色窗口
    )
    _proc, _job = proc, _bind_to_job(proc)
    while time.monotonic() - started < START_TIMEOUT_S:
        if is_running(base_url, timeout=1):
            log.info("按需启动了 Ollama，用时 %.1f 秒", time.monotonic() - started)
            return
        if proc.poll() is not None:   # 进程自己退出了（比如端口被别的程序占着）
            break
        time.sleep(0.05)
    _stop()
    raise AIError("Ollama 没能启动", kind="not_running", detail=f"ollama serve 没有在 {host} 上响应")


def _stop():
    """关掉芸芸启动的 Ollama（连同子进程）。"""
    global _proc, _job
    if _proc is None:
        return
    if _job is not None:
        _kernel32().CloseHandle(_job)   # 关掉作业 = 结束里面的所有进程
        _job = None
    else:
        _proc.kill()
    try:
        _proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log.warning("Ollama 没有按时结束")
    _proc = None
    log.info("关掉了芸芸启动的 Ollama")


# ---------- Windows 作业对象 ----------

class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in ("reads", "writes", "others", "read_b", "write_b", "other_b")]


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_KILL_ON_JOB_CLOSE = 0x2000           # 作业的最后一个句柄关掉时，结束里面所有进程（芸芸崩溃时系统也会关掉句柄）
_LIMIT_PRIORITY_CLASS = 0x20          # 作业里的进程都用指定的优先级……
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000  # ……低于正常：用户正在做的事优先


@functools.lru_cache(maxsize=None)   # 只准备一次
def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _bind_to_job(proc):
    """把进程放进一个作业对象，返回作业的句柄；做不到（非 Windows / 出错）就返回 None，关的时候只结束主进程。

    Ollama 启动后要等有请求才会开子进程，所以启动后马上放进作业，子进程会自动跟着进去。
    """
    if sys.platform != "win32":
        return None
    k32 = _kernel32()
    job = k32.CreateJobObjectW(None, None)
    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _LIMIT_PRIORITY_CLASS
    info.BasicLimitInformation.PriorityClass = _BELOW_NORMAL_PRIORITY_CLASS
    ok = job and k32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                             ctypes.byref(info), ctypes.sizeof(info))
    if ok and k32.AssignProcessToJobObject(job, int(proc._handle)):
        return job
    log.warning("没能把 Ollama 放进作业对象（错误码 %s），关的时候只能结束主进程", ctypes.get_last_error())
    if job:
        k32.CloseHandle(job)
    return None
