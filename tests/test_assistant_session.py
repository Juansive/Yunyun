"""一次助手会话（assistant/session.py）：每一轮问答、进度、停止、出错、新对话。

大部分用 tests/fake_assistant_process.py（纯 Qt 对象，测试决定事件顺序）；最后一个用真的 AssistantProcess + 假 worker。
"""
import sys
import time
from pathlib import Path

import pytest
from shiboken6 import isValid

from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.assistant.session import AssistantSession, Turn, error_text

FAKE_WORKER = Path(__file__).with_name("fake_assistant_worker.py")


def wait_until(qapp, condition, timeout=10.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.fixture
def session(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    processes = []

    def make(language, parent):
        process = FakeProcess(language, parent)
        processes.append(process)
        return process

    s = AssistantSession(make_process=make)
    s.processes = processes
    s.log = []
    for name in ("busy_changed", "turn_started", "turn_finished", "error_occurred", "progress_changed", "model_changed"):
        getattr(s, name).connect(lambda value, name=name: s.log.append((name, value)))
    yield s
    s.close()


def logged(session, name):
    return [value for n, value in session.log if n == name]


def test_question_waits_until_the_brain_is_ready(session):
    assert session.ask("你好")
    process = session.processes[0]
    assert process.started and process.language == "zh"
    assert session.busy and logged(session, "busy_changed") == [True]
    assert session.progress == i18n.tr("assistant_connecting")
    assert process.asked == []                       # 还没准备好，先存着

    process.say(protocol.Ready("anthropic", "claude-haiku-4-5"))
    assert process.asked == ["你好"]
    assert session.model == "claude-haiku-4-5" and logged(session, "model_changed") == ["claude-haiku-4-5"]
    assert session.progress == i18n.tr("assistant_thinking")


def test_answer_grows_and_turn_finishes(session):
    changes = []
    session.answer_changed.connect(lambda turn: changes.append(turn.answer))
    session.ask("你好")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("你好"), protocol.TextDelta("**呀**"))
    assert changes == ["你好", "你好**呀**"]
    assert session.progress == ""                    # 出字了，"在想"就不用显示了

    process.say(protocol.TurnFinished(0.0012, 0.0034, 10, 2))
    turn = logged(session, "turn_finished")[0]
    assert turn.question == "你好" and turn.answer == "你好**呀**" and turn.finished and not turn.stopped
    assert turn.cost_text() == "这一轮 $0.0012 · 这次对话共 $0.0034"
    assert not session.busy and logged(session, "busy_changed") == [True, False]
    assert session.turns == [turn]


def test_second_turn_uses_the_same_process(session):
    session.ask("一")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("A"), protocol.TurnFinished(0.001, 0.001))
    session.ask("二")
    process.say(protocol.TextDelta("B"), protocol.TurnFinished(0.002, 0.003))
    assert [(t.question, t.answer) for t in session.turns] == [("一", "A"), ("二", "B")]
    assert process.asked == ["一", "二"] and len(session.processes) == 1


def test_cannot_ask_while_answering_or_empty(session):
    assert not session.ask("   ")
    assert session.processes == []
    session.ask("一")
    assert not session.ask("二")
    assert [t.question for t in session.turns] == ["一"]


def test_cost_text_variants(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    assert Turn("q", cost_usd=None, session_cost_usd=0.0012).cost_text() == "这一轮 $0.0000 · 这次对话共 $0.0012"
    assert Turn("q", input_tokens=120, output_tokens=30).cost_text() == "这一轮 120 / 30 token（输入 / 输出）"
    assert Turn("q").cost_text() == ""


def test_stop_interrupts_and_marks_the_turn(session):
    session.ask("slow")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("写到一半"))
    session.stop()
    assert process.interrupts == 1 and session.busy  # 等大脑说"停好了"
    process.say(protocol.TurnFinished(0.001, 0.001, stopped=True))
    turn = logged(session, "turn_finished")[0]
    assert turn.stopped and turn.answer == "写到一半" and turn.error is None
    assert not session.busy


def test_stop_before_the_brain_is_ready_takes_the_question_back(session):
    session.ask("你好")
    session.stop()
    assert not session.busy and logged(session, "turn_finished")[0].stopped
    session.processes[0].say(protocol.Ready("anthropic", "m"))
    assert session.processes[0].asked == []


def test_tool_progress(session):
    session.ask("查一下")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("我查查。"),
                protocol.ToolStarted("t1", "WebSearch", "芸芸 天气"))
    assert session.progress == "正在搜索网页…… 芸芸 天气"
    process.say(protocol.TextDelta("还在查"))
    assert session.progress == "正在搜索网页…… 芸芸 天气"   # 工具还没用完，进度不收起
    process.say(protocol.ToolStarted("t2", "Read", ""))
    assert session.progress == "正在使用 Read……"
    process.say(protocol.ToolFinished("t1"), protocol.ToolFinished("t2"))
    assert session.progress == i18n.tr("assistant_thinking")
    process.say(protocol.TurnFinished(0.1, 0.1))
    assert session.progress == ""


def test_error_during_a_turn(session):
    session.ask("hi")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.Error("auth", "invalid x-api-key"))
    assert session.busy                              # 大脑还会发"这一轮结束"
    process.say(protocol.TurnFinished(None, 0.0))
    turn = logged(session, "turn_finished")[0]
    assert turn.error == protocol.Error("auth", "invalid x-api-key")
    assert logged(session, "error_occurred") == [protocol.Error("auth", "invalid x-api-key")]
    text = error_text(turn.error)
    assert i18n.tr("assistant_error_auth") in text and "invalid x-api-key" in text


def test_error_text_falls_back_to_other():
    assert error_text(protocol.Error("weird")) == i18n.tr("assistant_error_other")


def test_no_key_on_start_ends_the_turn_and_next_question_restarts(session):
    session.ask("hi")
    first = session.processes[0]
    first.say(protocol.Error("no_key"))              # 还没准备好就出错，然后子进程自己退出
    assert not session.busy
    first.die()
    assert session.process is None
    session.ask("再试")                              # 用户去设置页填好 Key 再问：重新启动一个
    assert len(session.processes) == 2 and session.processes[1].started


def test_error_while_idle_is_still_reported(session):
    session.start()
    session.processes[0].say(protocol.Error("no_key"))
    assert logged(session, "error_occurred") == [protocol.Error("no_key")]


def test_worker_dying_mid_answer_ends_the_turn(session):
    session.ask("crash")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("半句"), protocol.Error("worker", "意外退出（3）"))
    assert session.busy                              # 准备好之后的出错：等子进程真的没了再结束
    process.die()
    assert not session.busy and session.process is None


def test_late_events_after_stop_are_ignored(session):
    session.ask("一")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TurnFinished(stopped=True))
    process.say(protocol.TextDelta("迟到的字"))
    assert all(not t.answer for t in session.turns)


def test_new_conversation_restarts_the_process(session):
    session.ask("你好")
    old = session.processes[0]
    old.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("嗨"), protocol.TurnFinished(0.01, 0.01))
    session.new_conversation()
    assert session.turns == [] and old.stopped
    assert len(session.processes) == 2 and session.processes[1].started
    old.say(protocol.Error("worker"))                # 旧进程退出前说的话不再理
    assert logged(session, "error_occurred") == []


def test_new_conversation_while_answering(session):
    session.ask("slow")
    session.processes[0].say(protocol.Ready("anthropic", "m"), protocol.TextDelta("。。"))
    session.new_conversation()
    assert not session.busy and logged(session, "busy_changed") == [True, False]


def test_close_stops_the_process(session):
    session.start()
    process = session.processes[0]
    session.close()
    assert process.stopped and session.process is None


def test_real_channel_with_fake_worker(qapp):
    """真的 AssistantProcess + 假的子进程：问一句、收到回答、关闭时等它退出。"""
    from yunyun.assistant.process import AssistantProcess
    processes = []

    def make(language, parent):
        process = AssistantProcess(language, parent, command=(sys.executable, [str(FAKE_WORKER)], None))
        processes.append(process)
        return process

    session = AssistantSession(make_process=make)
    session.ask("hello")
    assert wait_until(qapp, lambda: not session.busy)
    assert session.turns[0].answer == "你好呀"
    assert session.turns[0].cost_text() == "这一轮 $0.0010 · 这次对话共 $0.0010"
    process = processes[0]
    session.close(wait=True)
    assert not isValid(process) or not process.is_running()
