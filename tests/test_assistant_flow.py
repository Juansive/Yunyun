"""端到端测试：右键"助手模式…" → 面板 → 发送 → 假大脑回答 → 芸芸思考 / 恢复 → 出错说一句 → 关闭。

大脑用 tests/fake_assistant_process.py（不开子进程）；最后一个用真的 AssistantProcess + 假 worker 测"芸芸退出时等子进程退出"。
"""
import sys
import time
from pathlib import Path

import pytest
from shiboken6 import isValid

from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.assistant import settings as assistant_settings
from yunyun.main import connect_assistant
from yunyun.pet import assistant_panel
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
    processes = []

    def make(language, parent):
        process = FakeProcess(language, parent)
        processes.append(process)
        return process

    monkeypatch.setattr(assistant_panel, "default_process", make)
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.move(600, 500)
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
    return [b._text for b in pet._bubbles._bubbles]


def test_menu_has_assistant_entry(world):
    pet, _, _ = world
    texts = [a.text() for a in pet._build_menu().actions()]
    assert texts.index("助手模式…") == texts.index("设置…") + 1   # 就在"设置…"下面


def test_without_key_opens_settings_and_says_so(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch, False)
    pet.open_assistant()
    assert pet.assistant_panel is None and processes == []
    assert pet.settings_window is not None and pet.settings_window.current_page == "assistant"
    assert i18n.tr("assistant_need_key") in bubbles(pet)


def test_full_flow(qapp, world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    panel = pet.assistant_panel
    assert panel is not None and panel.isVisible()
    assert panel.parent() is pet                  # 从属窗口
    process = processes[0]
    assert process.started                        # 一打开就启动大脑

    pet.open_assistant()                          # 再点一次：还是同一个面板、同一个大脑
    assert pet.assistant_panel is panel and len(processes) == 1

    process.say(protocol.Ready("anthropic", "claude-haiku-4-5"))
    panel._input.setPlainText("你好")
    panel.send()
    assert machine.state is PetState.THINKING     # 发出问题 → 芸芸思考
    process.say(protocol.TextDelta("你好呀"), protocol.TurnFinished(0.001, 0.001))
    assert machine.state is PetState.IDLE         # 回答完 → 回到原来的样子
    assert panel.transcript() == [("user", "你好"), ("reply", "你好呀")]

    panel.close()
    assert wait_until(qapp, lambda: pet.assistant_panel is None)
    assert process.stopped                        # 关掉面板 = 退出助手模式

    pet.open_assistant()                          # 下次打开重新启动
    assert len(processes) == 2 and processes[1].started


def test_mail_and_assistant_thinking_do_not_cancel_each_other(world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    panel = pet.assistant_panel
    panel._input.setPlainText("写首诗")
    panel.send()
    machine.start_thinking("mail")                # 同时点了小红点
    machine.stop_thinking("mail")                 # 邮件先处理完
    assert machine.state is PetState.THINKING     # 助手还在回答，继续想
    process.say(protocol.TurnFinished(0.001, 0.001))
    assert machine.state is PetState.IDLE


def test_closing_while_answering_stops_thinking(qapp, world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    pet.assistant_panel._input.setPlainText("slow")
    pet.assistant_panel.send()
    assert machine.state is PetState.THINKING
    pet.close_assistant()
    assert machine.state is PetState.IDLE
    assert pet.assistant_panel is None and processes[0].stopped


def test_error_makes_yunyun_say_something(world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Error("no_key"))
    assert i18n.tr("assistant_error_bubble") in bubbles(pet)


def test_open_settings_from_error(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Error("auth"))
    pet.assistant_panel.settings_requested.emit()
    assert pet.settings_window is not None and pet.settings_window.current_page == "assistant"


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

    monkeypatch.setattr(assistant_panel, "default_process", make)
    pet.open_assistant()
    process = real[0]
    assert wait_until(qapp, lambda: process.ready)
    pet.close_assistant(wait=True)
    assert not isValid(process) or not process.is_running()   # 返回时已经退出了（不靠事件循环）
