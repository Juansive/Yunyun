"""按需启动 / 关闭 Ollama 的测试：用一个假的 Ollama（tests/fake_ollama.py），不会碰你电脑上真的 Ollama。"""
import ctypes
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from yunyun.ai import ollama_service
from yunyun.ai.errors import AIError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="作业对象只在 Windows 上有")

FAKE = Path(__file__).with_name("fake_ollama.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))   # 端口 0 = 让系统挑一个空闲的
        return s.getsockname()[1]


def pid_alive(pid):
    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = ctypes.c_void_p
    handle = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE：只为了"等它结束"
    if not handle:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102   # WAIT_TIMEOUT = 还在运行
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))


def wait_until(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def fake(monkeypatch, tmp_path):
    port = free_port()
    pid_file = tmp_path / "child.pid"
    monkeypatch.setattr(ollama_service, "ENABLED", True)          # conftest 默认关掉了，这里打开
    monkeypatch.setattr(ollama_service, "IDLE_STOP_S", 0.3)
    monkeypatch.setattr(ollama_service, "find_exe", lambda: sys.executable)
    monkeypatch.setattr(ollama_service, "_command", lambda exe: [exe, str(FAKE)])
    monkeypatch.setenv("FAKE_OLLAMA_CHILD_PID", str(pid_file))
    f = SimpleNamespace(base=f"http://127.0.0.1:{port}", host=f"127.0.0.1:{port}", pid_file=pid_file)
    f.child = lambda: int(pid_file.read_text()) if wait_until(lambda: pid_file.exists() and pid_file.read_text()) else None
    yield f
    ollama_service.shutdown()


def test_starts_on_demand_and_stops_with_its_children(fake):
    assert not ollama_service.is_running(fake.base)
    with ollama_service.session(fake.base):
        assert ollama_service.is_running(fake.base) and ollama_service.started_by_us()
        child = fake.child()
        assert pid_alive(child)
    # 用完：空闲一会儿后关掉，"llama-server"（子进程）也一起结束，不留下占内存的孤儿
    assert wait_until(lambda: not ollama_service.is_running(fake.base))
    assert not ollama_service.started_by_us()
    assert wait_until(lambda: not pid_alive(child))


def test_back_to_back_use_keeps_the_same_server(fake):
    with ollama_service.session(fake.base):
        first = ollama_service._proc.pid
    with ollama_service.session(fake.base):          # 还没到关闭时间又要用：取消关闭，接着用同一个
        assert ollama_service._proc.pid == first
    ollama_service.shutdown()                          # 芸芸退出：马上关
    assert not ollama_service.is_running(fake.base)


def test_ollama_opened_by_the_user_is_left_alone(fake):
    user = subprocess.Popen([sys.executable, str(FAKE)], env=dict(os.environ, OLLAMA_HOST=fake.host))
    try:
        assert wait_until(lambda: ollama_service.is_running(fake.base))
        with ollama_service.session(fake.base):
            assert not ollama_service.started_by_us()   # 用户自己开着：直接用，不再启动一个
        time.sleep(ollama_service.IDLE_STOP_S + 0.5)
        assert ollama_service.is_running(fake.base)     # 也不会被芸芸关掉
    finally:
        user.kill()
        child = fake.child()
        if child:
            subprocess.run(["taskkill", "/PID", str(child), "/F"], capture_output=True)


def test_missing_ollama_gives_a_clear_error(fake, monkeypatch):
    monkeypatch.setattr(ollama_service, "find_exe", lambda: None)
    with pytest.raises(AIError) as info:
        with ollama_service.session(fake.base):
            pass
    assert info.value.kind == "not_running"
    assert ollama_service._users == 0                  # 出错也不会把"使用中"的计数弄乱
