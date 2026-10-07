"""助手子进程的主循环（assistant/worker.py 的 serve），用假的大脑在本进程里测，不启动 Claude。"""
import asyncio
import queue

import pytest

from yunyun.assistant import protocol, worker
from yunyun.assistant import settings as assistant_settings
from yunyun.assistant.settings import AssistantSettings


class Feed:
    """代替标准输入：测试往里放命令，serve 的读线程一行行读出来；close() 等于"管道断了"。"""

    def __init__(self):
        self._lines = queue.Queue()

    def send(self, command):
        self._lines.put(protocol.encode(command))

    def send_raw(self, line):
        self._lines.put(line)

    def close(self):
        self._lines.put(None)

    def __iter__(self):
        while (line := self._lines.get()) is not None:
            yield line


class FakeBrain:
    """假的大脑：记下被怎么调用；ask 时说两句话再结束这一轮（slow 的话一直等到被打断）。"""

    def __init__(self, settings, api_key, language, emit, fail_kind=None):
        self.args = (settings, api_key, language)
        self.emit = emit
        self.fail_kind = fail_kind
        self.asked, self.interrupts, self.closed = [], 0, False
        self._interrupted = None

    async def start(self):
        if self.fail_kind:
            error = RuntimeError("启动失败")
            error.kind, error.detail = self.fail_kind, "detail"
            raise error
        self.emit(protocol.Ready("anthropic", "fake"))

    async def ask(self, text):
        self.asked.append(text)
        if text == "slow":
            self._interrupted = asyncio.Event()
            await self._interrupted.wait()
            self.emit(protocol.TurnFinished(stopped=True))
            return
        self.emit(protocol.TextDelta("好的"))
        self.emit(protocol.TurnFinished(0.001, 0.001, 5, 1))

    async def interrupt(self):
        self.interrupts += 1
        if self._interrupted:
            self._interrupted.set()

    async def close(self):
        self.closed = True


@pytest.fixture
def key(monkeypatch):
    """假装凭据管理器里有 Key（值是多少无所谓，假大脑不会用它）。"""
    monkeypatch.setattr(assistant_settings, "load_settings", lambda: AssistantSettings())
    monkeypatch.setattr(assistant_settings, "get_api_key", lambda provider: "sk-ant-test")


def run(scenario, fail_kind=None):
    """跑一遍 serve：scenario(feed, events, brains) 是一个协程，负责发命令、检查结果。返回 (事件, 造出来的大脑)。"""
    feed, events, brains = Feed(), [], []

    def make_brain(*args):
        brains.append(FakeBrain(*args, fail_kind=fail_kind))
        return brains[0]

    async def main():
        served = asyncio.create_task(worker.serve(feed, lambda data: events.append(protocol.decode(data)), "ja", make_brain))
        await scenario(feed, events, brains)
        feed.close()                                   # 万一场景里没说 Quit：管道断了也会退出
        await asyncio.wait_for(served, 5)

    asyncio.run(main())
    return events, brains


async def until(condition, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if condition():
            return True
        await asyncio.sleep(0.01)
    return condition()


def test_no_key_means_no_brain(monkeypatch):
    monkeypatch.setattr(assistant_settings, "get_api_key", lambda provider: None)

    async def scenario(feed, events, brains):
        assert await until(lambda: events)

    events, brains = run(scenario)
    assert events == [protocol.Error("no_key")]
    assert brains == []                                # 没有 Key 就不启动 Claude Code


def test_brain_gets_settings_key_and_language(key):
    async def scenario(feed, events, brains):
        assert await until(lambda: events)
        feed.send(protocol.Quit())

    events, brains = run(scenario)
    assert brains[0].args == (AssistantSettings(), "sk-ant-test", "ja")
    assert events == [protocol.Ready("anthropic", "fake")]
    assert brains[0].closed


def test_start_failure_reports_its_kind(key):
    async def scenario(feed, events, brains):
        assert await until(lambda: events)

    events, brains = run(scenario, fail_kind="missing_cli")
    assert events == [protocol.Error("missing_cli", "detail")]
    assert brains[0].closed


def test_ask_then_quit(key):
    async def scenario(feed, events, brains):
        assert await until(lambda: events)
        feed.send(protocol.Ask("你好"))
        assert await until(lambda: any(isinstance(e, protocol.TurnFinished) for e in events))
        feed.send(protocol.Quit())

    events, brains = run(scenario)
    assert brains[0].asked == ["你好"]
    assert protocol.TextDelta("好的") in events
    assert brains[0].closed


def test_pipe_closed_means_exit(key):
    """芸芸退出或崩溃 → 标准输入读到头 → 子进程自己收拾好退出。"""
    async def scenario(feed, events, brains):
        assert await until(lambda: events)
        feed.close()

    events, brains = run(scenario)
    assert brains[0].closed


def test_interrupt_only_while_answering(key):
    async def scenario(feed, events, brains):
        assert await until(lambda: events)
        feed.send(protocol.Interrupt())                # 没在回答：不用打断
        feed.send(protocol.Ask("slow"))
        assert await until(lambda: brains[0].asked)
        feed.send(protocol.Ask("插队"))                 # 上一轮还没完：不接
        feed.send(protocol.Interrupt())
        assert await until(lambda: any(isinstance(e, protocol.TurnFinished) for e in events))
        feed.send(protocol.Quit())

    events, brains = run(scenario)
    assert brains[0].interrupts == 1
    assert brains[0].asked == ["slow"]
    assert [e for e in events if isinstance(e, protocol.TurnFinished)] == [protocol.TurnFinished(stopped=True)]


def test_quit_while_answering_cancels_the_turn(key):
    async def scenario(feed, events, brains):
        assert await until(lambda: events)
        feed.send(protocol.Ask("slow"))
        assert await until(lambda: brains[0].asked)
        feed.send(protocol.Quit())

    events, brains = run(scenario)
    assert brains[0].closed
    assert not [e for e in events if isinstance(e, protocol.TurnFinished)]   # 这一轮被取消了


def test_bad_command_lines_are_skipped(key):
    async def scenario(feed, events, brains):
        assert await until(lambda: events)
        feed.send_raw(b"not json\n")
        feed.send_raw(b"\n")
        feed.send(protocol.Ask("你好"))
        assert await until(lambda: any(isinstance(e, protocol.TurnFinished) for e in events))
        feed.send(protocol.Quit())

    events, brains = run(scenario)
    assert brains[0].asked == ["你好"]
