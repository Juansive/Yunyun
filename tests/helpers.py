"""测试共用的小函数（fixture 在 conftest.py）。"""
import time


def wait_until(qapp, condition, timeout=10.0):
    """处理 Qt 事件，直到条件成立或超时（后台线程、子进程的结果要靠事件循环送回来）。返回条件最后是否成立。"""
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def wait(qapp, seconds):
    """处理 Qt 事件 seconds 秒（等定时器、动画走完）。"""
    wait_until(qapp, lambda: False, seconds)
