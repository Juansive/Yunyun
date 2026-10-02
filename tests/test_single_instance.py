"""单实例的测试（用测试专用的通道名，不会影响正在运行的芸芸）。"""
import subprocess
import sys
import time
import uuid

from yunyun import single_instance


def wait_until(qapp, condition, timeout=3.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def test_nobody_running(qapp):
    assert single_instance.notify_running_instance(f"yunyun-test-{uuid.uuid4()}") is False


def test_second_launch_wakes_first(qapp):
    name = f"yunyun-test-{uuid.uuid4()}"
    guard = single_instance.InstanceGuard(name)
    assert guard.listening
    woken = []
    guard.show_requested.connect(lambda: woken.append(1))

    # 用另一个进程模拟"又双击了一次"：真实情况下第二个芸芸就是另一个进程
    code = ("from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
            "from yunyun import single_instance as s;"
            f"print(s.notify_running_instance({name!r}))")
    second = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert wait_until(qapp, lambda: second.poll() is not None and woken, timeout=10)
    assert second.stdout.read().strip() == "True"   # 第二个发现已经有人在运行
    assert woken == [1]                              # 第一个被叫出来了
