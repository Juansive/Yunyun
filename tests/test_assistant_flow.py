"""端到端测试：右键"✦ 助手模式" → 输入框展开 → 发问 → 芸芸思考、气泡逐字说 → 回答完恢复 → 收起 / 闲置退出 / 菜单退出。

大脑用 tests/fake_assistant_process.py（不开子进程）；最后一个用真的 AssistantProcess + 假 worker 测"芸芸退出时等子进程退出"。
"""
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.assistant import session as session_module
from yunyun.assistant import settings as assistant_settings
from yunyun.main import connect_assistant
from yunyun.pet.assistant_input import AssistantInput
from yunyun.pet.assistant_mode import AssistantMode
from yunyun.pet.menu import FancyAction
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState, StateMachine
from yunyun.pet.window import PetWindow

FAKE_WORKER = Path(__file__).with_name("fake_assistant_worker.py")


def wait_until(qapp, condition, timeout=10.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.fixture
def world(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(AssistantInput, "OPEN_MS", 20)
    monkeypatch.setattr(AssistantInput, "CLOSE_MS", 20)
    monkeypatch.setattr(AssistantMode, "BUBBLE_MS", 10)
    processes = []

    def make(language, parent):
        process = FakeProcess(language, parent)
        processes.append(process)
        return process

    monkeypatch.setattr(session_module, "default_process", make)
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.move(600, 300)
    pet.show()
    machine = StateMachine()
    machine.state_changed.connect(pet.set_state)
    connect_assistant(pet, machine)
    yield pet, machine, processes
    pet.close_assistant()
    if pet.settings_window is not None:
        pet.settings_window.close()
    pet.close()


def has_key(monkeypatch, ready=True):
    monkeypatch.setattr(assistant_settings, "is_ready", lambda settings=None: ready)


def bubbles(pet):
    return [b.text for b in pet._bubbles._bubbles]


def ask(pet, text):
    box = pet.assistant.input
    box.set_text(text)
    QApplication.sendEvent(box._edit, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))


def menu_texts(pet):
    return [a.text() for a in pet._build_menu().actions() if not a.isSeparator()]


def test_menu_puts_assistant_first_and_fancy(world, monkeypatch):
    pet, _, _ = world
    actions = pet._build_menu().actions()
    assert isinstance(actions[0], FancyAction) and actions[0].text() == "✦ 助手模式"
    assert "退出助手模式" not in menu_texts(pet)
    has_key(monkeypatch)
    pet.open_assistant()
    texts = menu_texts(pet)
    assert texts[1] == "退出助手模式"               # 开着时才有


def test_without_key_opens_settings_and_says_so(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch, False)
    pet.open_assistant()
    assert pet.assistant is None and processes == []
    assert pet.settings_window is not None and pet.settings_window.current_page == "assistant"
    assert i18n.tr("assistant_need_key") in bubbles(pet)


def test_full_flow(qapp, world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    assert mode is not None and mode.input.is_open
    assert mode.input.parent() is pet               # 从属窗口
    process = processes[0]
    assert process.started                          # 一唤出就启动大脑

    process.say(protocol.Ready("anthropic", "claude-haiku-4-5"))
    ask(pet, "你好")
    assert process.asked == ["你好"] and mode.input.text == ""   # 发出去了才清空
    assert machine.state is PetState.THINKING       # 发出问题 → 芸芸思考
    assert mode.input._thinking and mode.input._button.stop_mode
    assert bubbles(pet) == [i18n.tr("assistant_thinking")]       # 还没出字：气泡说进度

    process.say(protocol.TextDelta("你好"), protocol.TextDelta("**呀**"))
    assert wait_until(qapp, lambda: bubbles(pet) == ["你好呀"])  # 气泡里不显示 Markdown 符号
    process.say(protocol.TurnFinished(0.001, 0.001))
    assert machine.state is PetState.IDLE           # 回答完 → 回到原来的样子
    assert not mode.input._thinking and not mode.input._button.stop_mode
    assert pet._bubbles._bubbles[-1]._hide_timer.isActive()      # 说完了，过一会儿消失

    pet.open_assistant()                            # 再唤出：还是同一个助手
    assert pet.assistant is mode and len(processes) == 1


def test_long_answer_only_shows_the_beginning(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "讲个长故事")
    processes[0].say(protocol.TextDelta("很久很久以前，" * 80), protocol.TurnFinished(0.01, 0.01))
    text = bubbles(pet)[-1]
    assert text.endswith("……") and len(text) < 200


def test_stopped_answer_says_so(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    ask(pet, "slow")
    process.say(protocol.TextDelta("写到一半"))
    pet.assistant.input._button.click()             # 回答时按钮是"停止"
    assert process.interrupts == 1
    process.say(protocol.TurnFinished(0.001, 0.001, stopped=True))
    assert bubbles(pet)[-1] == "写到一半\n" + i18n.tr("assistant_stopped")


def test_error_makes_yunyun_say_why(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    ask(pet, "hi")
    process.say(protocol.Error("auth", "invalid x-api-key"), protocol.TurnFinished(None, 0.0))
    said = bubbles(pet)
    error = [t for t in said if i18n.tr("assistant_error_auth") in t]
    assert error and i18n.tr("assistant_fix_in_settings") in error[0]


def test_mail_and_assistant_thinking_do_not_cancel_each_other(world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "写首诗")
    machine.start_thinking("mail")                  # 同时点了小红点
    machine.stop_thinking("mail")                   # 邮件先处理完
    assert machine.state is PetState.THINKING       # 助手还在回答，继续想
    processes[0].say(protocol.TurnFinished(0.001, 0.001))
    assert machine.state is PetState.IDLE


def test_collapse_keeps_assistant_then_idle_exit(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    assert pet.assistant is mode and not processes[0].stopped   # 收起 ≠ 退出
    assert mode._idle_timer.isActive()

    pet.open_assistant()                            # 唤出：停止计时
    assert not mode._idle_timer.isActive() and mode.input.is_open
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    mode._idle_timer.timeout.emit()                 # 闲置太久
    assert processes[0].stopped
    assert wait_until(qapp, lambda: pet.assistant is None)


def test_idle_exit_waits_for_the_answer(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "slow")
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    mode._on_idle()                                 # 时间到了但还在回答：不退出
    assert pet.assistant is mode and not processes[0].stopped


def test_exit_from_menu_while_answering(qapp, world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "slow")
    assert machine.state is PetState.THINKING
    pet.close_assistant()                           # 菜单"退出助手模式"
    assert machine.state is PetState.IDLE
    assert processes[0].stopped
    assert wait_until(qapp, lambda: pet.assistant is None)
    pet.open_assistant()                            # 下次唤出：重新启动
    assert len(processes) == 2 and processes[1].started


def test_input_follows_yunyun(qapp, world, monkeypatch):
    pet, _, _ = world
    has_key(monkeypatch)
    pet.move(250, 100)                              # 测试用的屏幕不大：放中间，免得输入框贴着屏幕边
    pet.open_assistant()
    box = pet.assistant.input
    before = box.pos()
    pet.move(pet.x() - 40, pet.y())
    assert wait_until(qapp, lambda: box.pos().x() == before.x() - 40)


def test_quit_waits_for_the_real_worker(qapp, world, monkeypatch):
    """芸芸退出时（aboutToQuit）close_assistant(wait=True)：真的子进程退出了才返回。"""
    pet, _, _ = world
    has_key(monkeypatch)
    from yunyun.assistant.process import AssistantProcess
    real = []

    def make(language, parent):
        process = AssistantProcess(language, parent, command=(sys.executable, [str(FAKE_WORKER)], None))
        real.append(process)
        return process

    monkeypatch.setattr(session_module, "default_process", make)
    pet.open_assistant()
    process = real[0]
    assert wait_until(qapp, lambda: process.ready)
    pet.close_assistant(wait=True)
    assert not isValid(process) or not process.is_running()   # 返回时已经退出了（不靠事件循环）
