"""芸芸这一头的助手子进程管理（assistant/process.py），用假的子进程 tests/fake_assistant_worker.py 测。"""
import sys
import time
from pathlib import Path

import pytest

from yunyun.assistant import process as assistant_process
from yunyun.assistant import protocol
from yunyun.assistant.process import AssistantProcess
from yunyun.assistant.process import worker_command as real_worker_command   # import 时拿到的是真的（conftest 之后才拦截）

FAKE_WORKER = Path(__file__).with_name("fake_assistant_worker.py")


def wait_until(qapp, condition, timeout=10.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def start_fake(qapp, *flags):
    """启动假的子进程，返回 (进程, 收到的事件列表, 结束了没有)。"""
    worker = AssistantProcess("zh", command=(sys.executable, [str(FAKE_WORKER), *flags], None))
    events, finished = [], []
    worker.event.connect(events.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.start()
    return worker, events, finished


def of_type(events, cls):
    return [e for e in events if isinstance(e, cls)]


def test_ask_and_stop(qapp):
    worker, events, finished = start_fake(qapp)
    assert wait_until(qapp, lambda: worker.ready)
    assert events == [protocol.Ready("anthropic", "fake-model")]

    worker.ask("hello")
    assert wait_until(qapp, lambda: of_type(events, protocol.TurnFinished))
    assert "".join(e.text for e in of_type(events, protocol.TextDelta)) == "你好呀"

    worker.stop()
    assert wait_until(qapp, lambda: finished)
    assert not worker.is_running()
    assert not of_type(events, protocol.Error)            # 正常关闭不算出错
    assert finished == [True]                              # finished 只发一次


def test_interrupt_stops_the_turn(qapp):
    worker, events, finished = start_fake(qapp)
    assert wait_until(qapp, lambda: worker.ready)
    worker.ask("slow")
    assert wait_until(qapp, lambda: len(of_type(events, protocol.TextDelta)) >= 3)
    worker.interrupt()
    assert wait_until(qapp, lambda: of_type(events, protocol.TurnFinished))
    assert of_type(events, protocol.TurnFinished)[0].stopped
    worker.stop_and_wait()


def test_tool_events_come_through(qapp):
    worker, events, finished = start_fake(qapp)
    assert wait_until(qapp, lambda: worker.ready)
    worker.ask("tool")
    assert wait_until(qapp, lambda: of_type(events, protocol.TurnFinished))
    assert of_type(events, protocol.ToolStarted) == [protocol.ToolStarted("t1", "WebSearch", "芸芸")]
    assert of_type(events, protocol.ToolFinished) == [protocol.ToolFinished("t1")]
    worker.stop_and_wait()


def test_garbage_line_is_skipped(qapp):
    worker, events, finished = start_fake(qapp)
    assert wait_until(qapp, lambda: worker.ready)
    worker.ask("garbage")
    assert wait_until(qapp, lambda: of_type(events, protocol.TurnFinished))   # 乱码那行跳过了，后面照常
    worker.stop_and_wait()


def test_crash_becomes_worker_error(qapp):
    worker, events, finished = start_fake(qapp)
    assert wait_until(qapp, lambda: worker.ready)
    worker.ask("crash")
    assert wait_until(qapp, lambda: finished)
    assert [e.kind for e in of_type(events, protocol.Error)] == ["worker"]


def test_error_before_ready_is_not_reported_twice(qapp):
    """比如没有 Key：子进程说 no_key 后自己退出——这不是"意外退出"，不要再多报一个 worker 错误。"""
    worker, events, finished = start_fake(qapp, "--no-key")
    assert wait_until(qapp, lambda: finished)
    assert [e.kind for e in of_type(events, protocol.Error)] == ["no_key"]


def test_ready_timeout(qapp, monkeypatch):
    monkeypatch.setattr(assistant_process, "READY_TIMEOUT_MS", 300)
    worker, events, finished = start_fake(qapp, "--no-ready")
    assert wait_until(qapp, lambda: finished)
    assert [(e.kind, e.detail) for e in of_type(events, protocol.Error)] == [("worker", "启动超时")]


def test_stuck_worker_is_killed(qapp, monkeypatch):
    monkeypatch.setattr(assistant_process, "QUIT_TIMEOUT_MS", 300)
    worker, events, finished = start_fake(qapp, "--ignore-quit")
    assert wait_until(qapp, lambda: worker.ready)
    started = time.time()
    worker.stop()
    assert wait_until(qapp, lambda: finished)
    assert time.time() - started < 5                      # 300 毫秒后就强制结束了
    assert not of_type(events, protocol.Error)            # 是我们要关它，不算出错


def test_program_that_cannot_start(qapp):
    worker = AssistantProcess("zh", command=("no_such_program_for_yunyun.exe", [], None))
    events, finished = [], []
    worker.event.connect(events.append)
    worker.finished.connect(lambda: finished.append(True))
    worker.start()
    assert wait_until(qapp, lambda: finished)
    assert [e.kind for e in of_type(events, protocol.Error)] == ["worker"]


def test_commands_after_stop_are_ignored(qapp):
    worker, events, finished = start_fake(qapp)
    assert wait_until(qapp, lambda: worker.ready)
    worker.stop_and_wait()
    worker.ask("hello")                                    # 已经关了：不报错，什么也不做
    worker.stop()                                          # 再关一次也没关系
    assert finished == [True] or wait_until(qapp, lambda: finished)


def test_tests_never_start_the_real_worker():
    """conftest 里的规矩：测试里不传 command 就会被拦下（真的子进程会启动 Claude Code、花钱）。"""
    with pytest.raises(RuntimeError):
        AssistantProcess("zh")


def test_real_worker_command_shapes(monkeypatch):
    """开发版：用当前的 Python 跑 yunyun.assistant.worker；打包后：用芸芸.exe --assistant-worker。只看命令长什么样，不启动。"""
    program, args, cwd = real_worker_command("ja")
    assert program == sys.executable
    assert args == ["-m", "yunyun.assistant.worker", "--language", "ja"] and cwd
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    program, args, cwd = real_worker_command("en")
    assert args == ["--assistant-worker", "--language", "en"] and cwd is None
