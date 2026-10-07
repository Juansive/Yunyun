"""助手模式的对话面板（pet/assistant_panel.py）。

大部分用 FakeProcess（纯 Qt 对象，测试自己决定它什么时候发什么事件）；
最后一个用真的 AssistantProcess + tests/fake_assistant_worker.py 把整条通道走一遍。
"""
import sys
import time
from pathlib import Path

import pytest
from shiboken6 import isValid
from PySide6.QtCore import QEvent, QObject, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QPushButton

from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.pet import assistant_panel
from yunyun.pet.assistant_panel import AssistantPanel, _Reply

FAKE_WORKER = Path(__file__).with_name("fake_assistant_worker.py")


class FakeProcess(QObject):
    """和 AssistantProcess 一样的接口；事件由测试调用 say() 发出。"""

    event = Signal(object)
    finished = Signal()

    def __init__(self, language, parent=None):
        super().__init__(parent)
        self.language = language
        self.ready = False
        self.started = False
        self.stopped = False
        self.asked = []
        self.interrupts = 0

    def start(self):
        self.started = True

    def is_running(self):
        return self.started and not self.stopped

    def ask(self, text):
        self.asked.append(text)

    def interrupt(self):
        self.interrupts += 1

    def stop(self):
        self.stopped = True

    def say(self, *events):
        for event in events:
            if isinstance(event, protocol.Ready):
                self.ready = True
            self.event.emit(event)

    def die(self):
        self.finished.emit()


def wait_until(qapp, condition, timeout=10.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.fixture
def panel(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setattr(i18n, "_current", "zh")
    processes = []

    def make(language, parent):
        process = FakeProcess(language, parent)
        processes.append(process)
        return process

    panel = AssistantPanel(make_process=make)
    panel.processes = processes
    panel.busy_log = []
    panel.busy_changed.connect(panel.busy_log.append)
    yield panel
    if isValid(panel) and panel.isVisible():   # 关掉的面板已经销毁了
        panel.close()


def ask(panel, text):
    panel._input.setPlainText(text)
    panel.send()


def press(widget, key, modifiers=Qt.KeyboardModifier.NoModifier):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifiers))


def kinds(panel):
    return [kind for kind, _ in panel.transcript()]


def test_question_waits_until_the_brain_is_ready(panel):
    ask(panel, "你好")
    process = panel.processes[0]
    assert process.started and process.language == "zh"
    assert panel.transcript() == [("user", "你好")]
    assert panel.busy and panel.busy_log == [True]
    assert panel._progress.text() == i18n.tr("assistant_connecting")
    assert process.asked == []                       # 还没准备好，先存着

    process.say(protocol.Ready("anthropic", "claude-haiku-4-5"))
    assert process.asked == ["你好"]
    assert "claude-haiku-4-5" in panel._model_label.text()
    assert panel._progress.text() == i18n.tr("assistant_thinking")
    assert panel._input.toPlainText() == ""


def test_reply_streams_in_and_turn_finishes(panel, qapp):
    ask(panel, "你好")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("你好"))
    assert kinds(panel) == ["user"]                  # 限频：0.1 秒内不重新排版
    assert wait_until(qapp, lambda: kinds(panel) == ["user", "reply"])
    assert panel.transcript()[1] == ("reply", "你好")
    assert panel._progress.isHidden()                # 出字了，"在想"就不用显示了

    process.say(protocol.TextDelta("**呀**"), protocol.TurnFinished(0.0012, 0.0034, 10, 2))
    assert panel.transcript()[1] == ("reply", "你好**呀**")   # 这一轮结束时立刻排版，不等定时器
    assert not panel.busy and panel.busy_log == [True, False]
    assert panel._cost.text() == "这一轮 $0.0012 · 这次对话共 $0.0034"


def test_second_turn_gets_its_own_reply(panel):
    ask(panel, "一")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("A"), protocol.TurnFinished(0.001, 0.001))
    ask(panel, "二")
    process.say(protocol.TextDelta("B"), protocol.TurnFinished(0.002, 0.003))
    assert panel.transcript() == [("user", "一"), ("reply", "A"), ("user", "二"), ("reply", "B")]
    assert process.asked == ["一", "二"]
    assert len(panel.processes) == 1                 # 同一个子进程接着聊


def test_failed_turn_without_cost_does_not_keep_the_old_number(panel):
    ask(panel, "一")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TurnFinished(0.0012, 0.0012))
    ask(panel, "二")
    process.say(protocol.Error("network"), protocol.TurnFinished(None, 0.0012))
    assert panel._cost.text() == "这一轮 $0.0000 · 这次对话共 $0.0012"
    ask(panel, "三")
    process.say(protocol.TurnFinished())
    assert panel._cost.text() == ""


def test_short_question_is_not_wrapped(panel):
    panel.resize(420, 560)
    ask(panel, "Python 里怎么把列表倒过来？")
    label = [w for k, w in panel._entries if k == "user"][0]
    assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())


def test_cost_in_tokens_when_provider_is_not_billed_in_dollars(panel):
    ask(panel, "hi")
    panel.processes[0].say(protocol.Ready("x", "m"), protocol.TurnFinished(None, None, 120, 30))
    assert panel._cost.text() == "这一轮 120 / 30 token（输入 / 输出）"


def test_enter_sends_shift_enter_makes_new_line(panel):
    panel._input.setPlainText("第一行")
    press(panel._input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert panel.transcript() == [] and "\n" in panel._input.toPlainText()

    press(panel._input, Qt.Key.Key_Return)
    assert panel.transcript() == [("user", "第一行")]


def test_can_type_but_not_send_while_answering(panel):
    ask(panel, "一")
    panel._input.setPlainText("二")
    press(panel._input, Qt.Key.Key_Return)
    assert panel.transcript() == [("user", "一")]
    assert panel._input.toPlainText() == "二"        # 打好的字留着，回答完再发
    assert panel._send_button.text() == i18n.tr("assistant_stop")


def test_empty_input_is_not_sent(panel):
    ask(panel, "   \n ")
    assert panel.transcript() == [] and not panel.busy and panel.processes == []


def test_stop_interrupts_and_marks_the_reply(panel):
    ask(panel, "slow")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("写到一半"))
    panel._send_button.click()                       # 回答时按钮是"停止"
    assert process.interrupts == 1
    assert panel.busy                                # 等大脑说"停好了"

    process.say(protocol.TurnFinished(0.001, 0.001, stopped=True))
    assert panel.transcript() == [("user", "slow"), ("reply", "写到一半"), ("note", "（已停止）")]
    assert not panel.busy and "error" not in kinds(panel)
    assert panel._send_button.text() == i18n.tr("assistant_send")


def test_stop_before_the_brain_is_ready_takes_the_question_back(panel):
    ask(panel, "你好")
    panel.stop()
    assert not panel.busy and kinds(panel) == ["user", "note"]
    panel.processes[0].say(protocol.Ready("anthropic", "m"))
    assert panel.processes[0].asked == []


def test_tool_progress_line(panel):
    ask(panel, "查一下")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("我查查。"),
                protocol.ToolStarted("t1", "WebSearch", "芸芸 天气"))
    assert panel._progress.text() == "正在搜索网页…… 芸芸 天气"
    assert panel.transcript()[1] == ("reply", "我查查。")   # 用工具前说的话马上显示

    process.say(protocol.TextDelta("还在查"))
    assert not panel._progress.isHidden()            # 工具还没用完，进度行不收起
    process.say(protocol.ToolStarted("t2", "Read", ""))
    assert panel._progress.text() == "正在使用 Read……"
    process.say(protocol.ToolFinished("t1"), protocol.ToolFinished("t2"))
    assert panel._progress.text() == i18n.tr("assistant_thinking")
    process.say(protocol.TurnFinished(0.1, 0.1))
    assert panel._progress.isHidden()


def test_error_during_a_turn_offers_settings(panel):
    errors, wanted = [], []
    panel.error_occurred.connect(errors.append)
    panel.settings_requested.connect(lambda: wanted.append(True))
    ask(panel, "hi")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.Error("auth", "invalid x-api-key"))
    assert panel.busy                                # 大脑还会发"这一轮结束"
    process.say(protocol.TurnFinished(None, 0.0))
    assert not panel.busy

    kind, text = panel.transcript()[-1]
    assert kind == "error"
    assert i18n.tr("assistant_error_auth") in text and "invalid x-api-key" in text
    assert errors == [protocol.Error("auth", "invalid x-api-key")]
    buttons = [b for b in panel.findChildren(QPushButton) if b.text() == i18n.tr("assistant_open_settings")]
    assert len(buttons) == 1
    buttons[0].click()
    assert wanted == [True]


def test_network_error_has_no_settings_button(panel):
    ask(panel, "hi")
    panel.processes[0].say(protocol.Ready("anthropic", "m"), protocol.Error("network"), protocol.TurnFinished())
    assert not [b for b in panel.findChildren(QPushButton) if b.text() == i18n.tr("assistant_open_settings")]


def test_no_key_on_start_ends_the_turn_and_next_question_restarts(panel):
    ask(panel, "hi")
    first = panel.processes[0]
    first.say(protocol.Error("no_key"))              # 还没准备好就出错，然后子进程自己退出
    assert not panel.busy
    assert kinds(panel) == ["user", "error"]
    first.die()

    ask(panel, "再试")                               # 用户去设置页填好 Key 再问：重新启动一个
    assert len(panel.processes) == 2 and panel.processes[1].started


def test_error_while_idle_is_still_shown(panel):
    panel._ensure_process()
    panel.processes[0].say(protocol.Error("no_key"))
    assert kinds(panel) == ["error"]


def test_worker_dying_mid_answer_ends_the_turn(panel):
    ask(panel, "crash")
    process = panel.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("半句"),
                protocol.Error("worker", "意外退出（3）"))
    assert panel.busy                                # 准备好之后的出错：等子进程真的没了再结束
    process.die()
    assert not panel.busy and panel.process is None
    assert kinds(panel) == ["user", "reply", "error"]


def test_new_conversation_clears_and_restarts(panel):
    ask(panel, "你好")
    old = panel.processes[0]
    old.say(protocol.Ready("anthropic", "m"), protocol.TextDelta("嗨"), protocol.TurnFinished(0.01, 0.01))
    panel._new_button.click()
    assert panel.transcript() == [] and panel._cost.text() == ""
    assert old.stopped
    assert len(panel.processes) == 2 and panel.processes[1].started

    old.say(protocol.TextDelta("旧的"))              # 旧进程退出前说的话不再显示
    assert panel.transcript() == []
    ask(panel, "新问题")
    assert panel.processes[1].asked == [] and panel.busy
    panel.processes[1].say(protocol.Ready("anthropic", "m"))
    assert panel.processes[1].asked == ["新问题"]


def test_new_conversation_while_answering(panel):
    ask(panel, "slow")
    panel.processes[0].say(protocol.Ready("anthropic", "m"), protocol.TextDelta("。。"))
    panel.new_conversation()
    assert not panel.busy and panel.busy_log == [True, False]
    assert panel.processes[0].stopped


def test_close_stops_the_worker_and_remembers_size(panel, qapp):
    closed = []
    panel.closed.connect(lambda: closed.append(True))
    panel.resize(500, 600)
    panel.show()
    process = panel.processes[0]                     # 一打开就启动大脑
    assert process.started
    panel.close()
    assert process.stopped and closed == [True]
    assert QSettings().value("assistant/panel_size", type=QSize) == QSize(500, 600)


def test_size_is_restored(panel, qapp):
    QSettings().setValue("assistant/panel_size", QSize(480, 640))
    other = AssistantPanel(make_process=lambda language, parent: FakeProcess(language, parent))
    assert other.size() == QSize(480, 640)
    other.deleteLater()


def test_code_blocks_wrap(qapp):
    reply = _Reply()
    reply.resize(200, 50)
    reply.set_markdown("看这个：\n\n```python\nprint('" + "x" * 200 + "')\n```\n")
    block = reply.document().begin()
    while block.isValid():
        assert not block.blockFormat().nonBreakableLines()
        block = block.next()
    assert "x" * 200 in reply.toPlainText()          # 复制出来还是原样


def test_error_text_falls_back_to_other(panel):
    assert assistant_panel.error_text(protocol.Error("weird")) == i18n.tr("assistant_error_other")


def test_real_channel_with_fake_worker(panel, qapp, monkeypatch):
    """真的 AssistantProcess + 假的子进程：问一句、逐字显示、花费、关闭。"""
    from yunyun.assistant.process import AssistantProcess
    processes = []

    def make(language, parent):
        process = AssistantProcess(language, parent, command=(sys.executable, [str(FAKE_WORKER)], None))
        processes.append(process)
        return process

    real = AssistantPanel(make_process=make)
    ask(real, "hello")
    assert wait_until(qapp, lambda: not real.busy)
    assert real.transcript() == [("user", "hello"), ("reply", "你好呀")]
    assert real._cost.text() == "这一轮 $0.0010 · 这次对话共 $0.0010"

    process = processes[0]
    real.show()
    real.close()
    assert wait_until(qapp, lambda: not isValid(process) or not process.is_running())   # 子进程退出后自己销毁
