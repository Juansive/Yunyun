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

    def make(language, parent, resume=""):
        process = FakeProcess(language, parent, resume)
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
    process.say(protocol.ToolStarted("t2", "Read", "src/a.py"))
    assert session.progress == "正在读…… src/a.py"
    process.say(protocol.ToolStarted("t3", "SomethingNew", ""))
    assert session.progress == "正在使用 SomethingNew……"
    process.say(protocol.ToolFinished("t1"), protocol.ToolFinished("t2"), protocol.ToolFinished("t3"))
    assert session.progress == i18n.tr("assistant_thinking")
    process.say(protocol.TurnFinished(0.1, 0.1))
    assert session.progress == ""


# ---------- 确认请求 ----------

def edit_request(request_id=7):
    return protocol.PermissionRequest(request_id, "Edit", {"file_path": "a.py"}, "edit", True, "F:\\proj")


def command_request(request_id=8):
    return protocol.PermissionRequest(request_id, "Bash", {"command": "pytest"}, "command", True, "pytest")


def asking(session):
    changes = []
    session.permissions_changed.connect(lambda: changes.append([r.request_id for r in session.pending_permissions]))
    session.ask("改一下")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.ToolStarted("t1", "Edit", "a.py"))
    return process, changes


def test_permission_requests_queue_up_and_progress_says_waiting(session):
    process, changes = asking(session)
    process.say(edit_request(7), command_request(8))
    assert changes == [[7], [7, 8]]
    assert session.progress == i18n.tr("assistant_waiting_confirm")
    assert session.answer_permission(7, True)
    assert process.sent == [protocol.PermissionReply(7, True, "", False)]
    assert session.progress == i18n.tr("assistant_waiting_confirm")      # 还有一个在等
    assert session.answer_permission(8, False, "  先别跑  ", remember=True)
    assert process.sent[-1] == protocol.PermissionReply(8, False, "先别跑", False)   # 拒绝时"都允许"不算数
    assert changes[-1] == [] and session.progress == "正在改…… a.py"   # 答完了：换回等确认之前的进度
    assert not session.answer_permission(8, True)                       # 已经答过了


def test_allow_for_the_whole_chat_is_passed_on(session):
    process, _ = asking(session)
    process.say(command_request(3))
    session.answer_permission(3, True, remember=True)
    assert process.sent == [protocol.PermissionReply(3, True, "", True)]


def test_tool_events_while_waiting_do_not_hide_the_waiting_text(session):
    process, _ = asking(session)
    process.say(edit_request(1), protocol.ToolFinished("t1"))
    assert session.progress == i18n.tr("assistant_waiting_confirm")
    session.answer_permission(1, True)
    assert session.progress == i18n.tr("assistant_thinking")             # 等的时候工具结束了：答完显示最新的


def test_activity_follows_tools_and_confirmations(session):
    """芸芸的样子跟着它变：在想 → 用工具（在干活）→ 等你确认 → 接着干 → 在想 → 答完。"""
    activities = []
    session.activity_changed.connect(activities.append)
    process, _ = asking(session)                          # 问了，接着开始用 Edit
    process.say(edit_request(1))
    assert session.activity == "waiting"
    session.answer_permission(1, True)
    assert session.activity == "busy"                     # 允许了：工具还在用
    process.say(protocol.ToolFinished("t1"), protocol.TextDelta("改好了"))
    assert session.activity == "thinking"
    process.say(protocol.TurnFinished(0.001, 0.001))
    assert activities == ["thinking", "busy", "waiting", "busy", "thinking", ""]


def test_stop_while_waiting_clears_the_activity(session):
    activities = []
    session.activity_changed.connect(activities.append)
    process, _ = asking(session)
    process.say(edit_request(1))
    session.stop()                                        # 等确认作废：大脑停下之前芸芸接着"干活"
    assert session.activity == "busy"
    process.say(protocol.TurnFinished(0.0, 0.0, stopped=True))
    assert session.activity == "" and activities[-1] == ""


def test_stop_drops_waiting_requests(session):
    process, changes = asking(session)
    process.say(edit_request(1))
    session.stop()
    assert session.pending_permissions == [] and changes[-1] == []
    assert process.interrupts == 1 and process.sent == []                 # 不用一个个回：大脑停下时都当成拒绝


def test_turn_end_and_new_conversation_drop_waiting_requests(session):
    process, _ = asking(session)
    process.say(edit_request(1), protocol.TurnFinished(0.001, 0.001))
    assert session.pending_permissions == []
    session.ask("再来")
    process.say(edit_request(2))
    session.new_conversation()
    assert session.pending_permissions == []


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

    def make(language, parent, resume=""):
        process = AssistantProcess(language, parent, command=(sys.executable, [str(FAKE_WORKER)], None), resume=resume)
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


# ---------- 确认、撤销：走真的通道（QProcess + 一行一条 JSON），对面是假的子进程 ----------

@pytest.fixture
def real_session(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    from yunyun.assistant.process import AssistantProcess

    def make(language, parent, resume=""):
        return AssistantProcess(language, parent, command=(sys.executable, [str(FAKE_WORKER)], None), resume=resume)

    s = AssistantSession(make_process=make)
    yield s
    s.close(wait=True)


def waiting_for_confirm(qapp, session, question="edit"):
    session.ask(question)
    assert wait_until(qapp, lambda: session.pending_permissions)
    return session.pending_permissions[0]


def test_real_channel_confirm_allow_then_undo(qapp, real_session):
    request = waiting_for_confirm(qapp, real_session)
    assert (request.tool, request.kind, request.tool_id, request.input["file_path"]) == ("Edit", "edit", "t1", "a.py")
    real_session.answer_permission(request.request_id, True, remember=True)
    assert wait_until(qapp, lambda: not real_session.busy)
    turn = real_session.turns[0]
    assert turn.answer == "改好了" and turn.files == ("a.py",) and turn.checkpoint == "cp-1"
    assert [(a.tool, a.decision, a.ok) for a in turn.actions] == [("Edit", "remembered", True)]
    results = []
    real_session.rewind_finished.connect(lambda cp, ok, turns: results.append((cp, ok)))
    assert real_session.rewind(turn.generation, "cp-1")
    assert wait_until(qapp, lambda: results) and results == [("cp-1", True)] and turn.undone


def test_real_channel_confirm_deny_with_reason(qapp, real_session):
    request = waiting_for_confirm(qapp, real_session)
    real_session.answer_permission(request.request_id, False, "先别改，我还没提交")
    assert wait_until(qapp, lambda: not real_session.busy)
    turn = real_session.turns[0]
    assert turn.answer == "没改（先别改，我还没提交）"                 # 理由（中文）原样到了对面
    assert turn.files == () and [a.decision for a in turn.actions] == ["denied"]


def test_real_channel_stop_while_waiting(qapp, real_session):
    waiting_for_confirm(qapp, real_session)
    real_session.stop()
    assert real_session.pending_permissions == []                  # 卡片马上收起
    assert wait_until(qapp, lambda: not real_session.busy)
    turn = real_session.turns[0]
    assert turn.stopped and [a.decision for a in turn.actions] == ["stopped"]


def test_real_channel_worker_crash_while_waiting(qapp, real_session):
    errors, queue_sizes = [], []
    real_session.error_occurred.connect(errors.append)
    real_session.permissions_changed.connect(lambda: queue_sizes.append(len(real_session.pending_permissions)))
    real_session.ask("edit-crash")
    assert wait_until(qapp, lambda: errors and not real_session.busy)   # 不会一直等下去
    assert queue_sizes == [1, 0]                                     # 来过一个确认，子进程没了就作废
    assert [e.kind for e in errors] == ["worker"]


def test_each_turn_is_recorded_in_the_ledger(session):
    from yunyun.assistant import ledger
    session.ask("一")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TurnFinished(0.012, 0.012))
    session.ask("二")
    process.say(protocol.TurnFinished(None, None, 100, 20))   # 按 token 计费的服务商：不记美元
    assert round(ledger.summary().today, 6) == 0.012


# ---------- 撤销 ----------

def answered(session, question, files=(), checkpoint=""):
    """问一句、马上答完；返回这一轮。"""
    session.ask(question)
    process = session.processes[-1]
    if not process.ready:
        process.say(protocol.Ready("anthropic", "m"))
    process.say(protocol.TurnFinished(0.001, 0.001, checkpoint=checkpoint, files=files))
    return session.turns[-1]


def rewinds(session):
    results = []
    session.rewind_finished.connect(lambda cp, ok, turns: results.append((cp, ok, [t.question for t in turns])))
    return results


def test_turn_remembers_its_changes_and_generation(session):
    turn = answered(session, "改 a", ("F:\\p\\a.py",), "cp-1")
    assert turn.files == ("F:\\p\\a.py",) and turn.checkpoint == "cp-1"
    assert turn.generation == session.generation > 0


def test_rewind_marks_this_and_later_turns_and_tells_claude_next_time(session):
    first = answered(session, "改 a", ("F:\\p\\a.py",), "cp-1")
    answered(session, "随便聊聊")
    third = answered(session, "改 b", ("F:\\p\\b.py",), "cp-3")
    results = rewinds(session)
    process = session.processes[0]
    assert session.rewind(first.generation, "cp-1")
    assert process.sent == [protocol.Rewind("cp-1")] and session.rewinding == "cp-1"
    assert not session.ask("撤销还没做完")                               # 等撤销做完再问
    process.say(protocol.Rewound("cp-1", True))
    assert results == [("cp-1", True, ["改 a", "改 b"])]               # 这一轮和之后改过文件的都撤销了
    assert first.undone and third.undone and session.rewinding == ""
    session.ask("接着来")
    assert process.asked[-1].startswith("[Note from Yunyun") and "F:\\p\\a.py, F:\\p\\b.py" in process.asked[-1]
    assert process.asked[-1].endswith("接着来") and session.turns[-1].question == "接着来"   # 界面上不显示这句说明
    session.processes[0].say(protocol.TurnFinished(0.001, 0.001))
    session.ask("再来")
    assert process.asked[-1] == "再来"                                  # 只说一次


def test_failed_rewind_changes_nothing(session):
    turn = answered(session, "改 a", ("a.py",), "cp-1")
    results = rewinds(session)
    session.rewind(turn.generation, "cp-1")
    session.processes[0].say(protocol.Rewound("cp-1", False, "No file checkpoint"))
    assert results == [("cp-1", False, [])] and not turn.undone
    session.ask("接着来")
    assert session.processes[0].asked[-1] == "接着来"


def test_cannot_rewind_while_answering_or_from_another_conversation(session):
    turn = answered(session, "改 a", ("a.py",), "cp-1")
    session.ask("slow")
    assert not session.can_rewind(turn.generation, "cp-1") and not session.rewind(turn.generation, "cp-1")
    session.stop()
    session.processes[0].say(protocol.TurnFinished(stopped=True))
    assert session.can_rewind(turn.generation, "cp-1")
    assert not session.can_rewind(turn.generation, "")                 # 没改文件的轮次
    session.new_conversation()                                         # 换了子进程：检查点在旧的那个里
    assert session.generation != turn.generation and not session.can_rewind(turn.generation, "cp-1")


def test_process_gone_while_rewinding_counts_as_failed(session):
    turn = answered(session, "改 a", ("a.py",), "cp-1")
    results = rewinds(session)
    session.rewind(turn.generation, "cp-1")
    session.processes[0].die()
    assert results == [("cp-1", False, [])] and session.rewinding == ""


# ---------- 操作记录 ----------

def test_actions_are_recorded_per_turn(session):
    session.ask("修 bug")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"),
                protocol.ToolStarted("w1", "WebSearch", "pytest 报错"),         # 网页搜索：没有决定事件，算不用问
                protocol.ToolStarted("t1", "Read", "calc.py"),
                protocol.ToolDecision("t1", "Read", "auto", "calc.py"),
                protocol.ToolFinished("t1", ok=True),
                protocol.ToolStarted("t2", "Bash", "pytest -q 很长的命令开头"),
                protocol.ToolDecision("t2", "Bash", "allowed", "pytest -q 很长的命令开头……全文"),
                protocol.ToolFinished("t2", ok=False),
                protocol.ToolStarted("t3", "Bash", "rm -rf build"),
                protocol.ToolDecision("t3", "Bash", "denied", "rm -rf build"),
                protocol.ToolFinished("t3", ok=False),                       # 拒绝的不算"没成功"
                protocol.ToolDecision("t4", "Write", "stopped", "new.py"))   # 没见过开始的也记上
    process.say(protocol.TurnFinished(0.01, 0.01))
    turn = session.turns[0]
    assert [(a.tool, a.detail, a.decision, a.ok) for a in turn.actions] == [
        ("WebSearch", "pytest 报错", "auto", None),
        ("Read", "calc.py", "auto", True),
        ("Bash", "pytest -q 很长的命令开头……全文", "allowed", False),   # 命令换成全文
        ("Bash", "rm -rf build", "denied", None),
        ("Write", "new.py", "stopped", None),
    ]
    session.ask("下一问")
    process.say(protocol.ToolStarted("t5", "Read", "x.py"))
    assert [a.tool_id for a in session.turns[1].actions] == ["t5"]   # 每一轮各记各的


def test_unknown_decision_is_shown_as_auto(session):
    session.ask("问")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.ToolDecision("t1", "Read", "something-new", "a.py"))
    assert session.current_turn.actions[0].decision == "auto"
