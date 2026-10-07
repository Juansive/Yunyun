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


# ---------- 卡片堆、新对话（U2） ----------

def finish_turn(pet, process, question, answer="答案", cost=0.0012):
    ask(pet, question)
    process.say(protocol.TextDelta(answer), protocol.TurnFinished(cost, cost))


def test_each_turn_becomes_a_card(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    assert not mode.deck.isVisible()                 # 还没有卡片
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, process, "倒序怎么写", "用 `nums[::-1]`")
    card = mode.cards[-1]
    assert (card.kind, card.question, card.answer, card.summary, card.cost) == (
        "turn", "倒序怎么写", "用 `nums[::-1]`", "用 nums[::-1]", "$0.0012")
    assert mode.deck.isVisible() and mode.deck.card(0) is card
    assert pet._bubbles._bubbles[-1]._hide_timer.remainingTime() <= AssistantMode.DONE_SAY_WITH_DECK_MS


def test_stopped_and_failed_turns_are_marked(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    ask(pet, "slow")
    process.say(protocol.TextDelta("一半"), protocol.TurnFinished(0.001, 0.001, stopped=True))
    ask(pet, "hi")
    process.say(protocol.Error("network"), protocol.TurnFinished(None, 0.001))
    stopped, failed = pet.assistant.cards[-2:]
    assert stopped.stopped and stopped.summary == "一半"
    assert failed.error.startswith(i18n.tr("assistant_error_network")) and not failed.summary


def test_cards_survive_exit_and_a_divider_marks_the_restart(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "第一段")
    pet.close_assistant()
    assert wait_until(qapp, lambda: pet.assistant is None)

    pet.open_assistant()                             # 大脑重新启动了：插一张分隔卡
    cards = pet.assistant.cards
    assert [c.kind for c in cards] == ["turn", "divider"]
    assert pet.assistant.deck.isVisible()            # 以前的卡片还能看
    pet.close_assistant()
    assert wait_until(qapp, lambda: pet.assistant is None)
    pet.open_assistant()                             # 中间没问过：不再多插一张
    assert [c.kind for c in pet.assistant.cards] == ["turn", "divider"]


def test_ctrl_n_starts_a_new_conversation(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "旧问题")
    QApplication.sendEvent(mode.input._edit, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier))
    assert processes[0].stopped and len(processes) == 2   # 换了一个新的大脑会话
    assert [c.kind for c in mode.cards] == ["turn", "divider"]
    assert i18n.tr("assistant_new_chat_said") in bubbles(pet)
    mode.new_conversation()                          # 连按两次：不重复插分隔卡
    assert [c.kind for c in mode.cards] == ["turn", "divider"]


def test_new_conversation_while_answering_keeps_the_half_answer(world, monkeypatch):
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "slow")
    processes[0].say(protocol.TextDelta("说到一半"))
    mode.deck.new_conversation_requested.emit()      # 卡片堆右上角的"＋ 新对话"
    assert not mode.busy and machine.state is PetState.IDLE
    assert [(c.kind, c.stopped) for c in mode.cards] == [("turn", True), ("divider", False)]


# ---------- 工作文件夹 ----------

def folder_actions(mode):
    return {a.text(): a for a in mode.input.folder_menu().actions() if not a.isSeparator()}


def test_choose_a_work_folder_starts_a_new_conversation(qapp, world, monkeypatch, tmp_path):
    pet, _, processes = world
    has_key(monkeypatch)
    project = tmp_path / "my-project"
    project.mkdir()
    picked = []

    def fake_dialog(parent, start):
        picked.append(start)
        return str(project)

    monkeypatch.setattr("yunyun.pet.assistant_input.ask_folder", fake_dialog)
    pet.open_assistant()
    mode = pet.assistant
    assert mode.input.workspace == ""                # 默认没选：只聊天
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "旧问题")

    folder_actions(mode)[i18n.tr("assistant_folder_choose")].trigger()
    assert assistant_settings.load_settings().workspace == str(project)   # 存进设置：新的子进程启动时读
    assert mode.input.workspace == str(project)
    assert processes[0].stopped and len(processes) == 2                   # 换了文件夹 = 新的大脑会话
    assert (mode.cards[-1].kind, mode.cards[-1].question) == ("divider", "my-project")
    assert i18n.tr("assistant_folder_said", "my-project") in bubbles(pet)

    actions = folder_actions(mode)                   # 菜单里出现了最近用过的，打着勾
    recent = next(a for a in actions.values() if a.toolTip() == str(project))
    assert recent.isChecked() and not actions[i18n.tr("assistant_folder_none")].isChecked()
    recent.trigger()                                 # 选同一个：什么都不变
    assert len(processes) == 2

    actions[i18n.tr("assistant_folder_none")].trigger()
    assert assistant_settings.load_settings().workspace == "" and len(processes) == 3
    assert mode.cards[-1].question == "" and len([c for c in mode.cards if c.kind == "divider"]) == 1
    assert assistant_settings.recent_workspaces() == [str(project)]       # 最近用过的还留着


def test_cancel_the_folder_dialog_changes_nothing(world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    monkeypatch.setattr("yunyun.pet.assistant_input.ask_folder", lambda parent, start: "")
    pet.open_assistant()
    folder_actions(pet.assistant)[i18n.tr("assistant_folder_choose")].trigger()
    assert len(processes) == 1 and pet.assistant.input.workspace == ""


def test_folder_dialog_does_not_collapse_the_input(qapp, world, monkeypatch):
    """选择框是系统的窗口，开着时芸芸没有一个窗口是"活动"的：不能当成"点到别的程序"而收起。"""
    pet, _, _ = world
    has_key(monkeypatch)
    pet.open_assistant()
    box = pet.assistant.input
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: None))

    def fake_dialog(parent, start):
        box._check_focus_lost()
        return ""

    monkeypatch.setattr("yunyun.pet.assistant_input.ask_folder", fake_dialog)
    box._pick_folder()
    assert box.is_open
    box._check_focus_lost()                          # 选择框关了以后照常
    assert not box.is_open


def test_saved_folder_is_shown_and_used_next_time(world, monkeypatch, tmp_path):
    pet, _, _ = world
    has_key(monkeypatch)
    assistant_settings.set_workspace(str(tmp_path))
    pet.open_assistant()
    assert pet.assistant.input.workspace == str(tmp_path)
    assert str(tmp_path) in pet.assistant.input._folder.toolTip()


def test_deck_follows_the_input(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.move(100, 100)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    assert mode.deck.isVisible()
    mode.input.collapse()                            # 收起：卡片堆一起淡出
    assert wait_until(qapp, lambda: not mode.deck.isVisible())
    pet.open_assistant()
    assert wait_until(qapp, lambda: mode.deck.isVisible())


# ---------- 阅读框（U3） ----------

@pytest.fixture
def two_cards(qapp, world, monkeypatch):
    from yunyun.pet.assistant_reader import AssistantReader
    monkeypatch.setattr(AssistantReader, "OPEN_MS", 20)
    monkeypatch.setattr(AssistantReader, "SWAP_MS", 10)
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.move(100, 100)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, process, "第一问", "第一答")
    finish_turn(pet, process, "第二问", "第二答")
    return pet, pet.assistant


def test_click_card_opens_the_reader_and_again_closes(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    assert wait_until(qapp, lambda: mode.reader.is_open and mode.reader._shown == 1.0)
    assert mode.reader.card.question == "第二问"
    mode.deck.card_clicked.emit(mode.deck.card(0))   # 再点同一张：关掉
    assert wait_until(qapp, lambda: not mode.reader.isVisible())


def test_reader_follows_the_deck(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    mode.deck.go_to(1)                                # 开着阅读框翻卡片堆：阅读框跟着换
    assert wait_until(qapp, lambda: mode.reader.card.question == "第一问")


def test_divider_cards_do_not_open_the_reader(qapp, two_cards):
    pet, mode = two_cards
    mode.new_conversation()
    mode.deck.card_clicked.emit(mode.deck.card(0))   # 分隔卡
    assert not mode.reader.isVisible()


def test_collapse_and_exit_close_the_reader(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    assert wait_until(qapp, lambda: mode.reader.isVisible())
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.reader.isVisible() and not mode.deck.isVisible())
    pet.open_assistant()
    mode.deck.card_clicked.emit(mode.deck.card(0))
    reader = mode.reader
    pet.close_assistant()
    assert not reader.isVisible()


def test_reader_losing_focus_collapses_everything(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    mode.reader.dismissed.emit()                     # 焦点去了别的程序
    assert wait_until(qapp, lambda: not mode.input.isVisible() and not mode.reader.isVisible())
    assert pet.assistant is mode                     # 只是收起，助手还开着


def test_no_assistant_entry_when_this_build_has_no_sdk(world, monkeypatch):
    """打包版到阶段 9 才带上 Claude Code：在那之前菜单和设置窗口里都不显示助手。"""
    from yunyun.pet import settings_window, window
    pet, _, _ = world
    monkeypatch.setattr(window, "assistant_available", lambda: False)
    monkeypatch.setattr(settings_window, "assistant_available", lambda: False)
    assert "✦ 助手模式" not in menu_texts(pet)
    assert not any(isinstance(a, FancyAction) for a in pet._build_menu().actions())
    pet.open_settings()
    sidebar = pet.settings_window._sidebar
    hidden = [sidebar.item(i).text() for i in range(sidebar.count()) if sidebar.item(i).isHidden()]
    assert hidden == ["助手"]
    pet.open_settings("history")                     # 别的页序号不变
    assert pet.settings_window.current_page == "history"


def test_sdk_check_does_not_import_it():
    import subprocess
    code = ("import sys; from yunyun.features import assistant_available; "
            "print(assistant_available(), 'claude_agent_sdk' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert out.stdout.split() == ["True", "False"]   # 开发版找得到 SDK，但没有载入


# ---------- 确认卡片 ----------

def permission(request_id=1, tool="Bash", data=None, kind="command", can_remember=True, scope="pytest"):
    return protocol.PermissionRequest(request_id, tool, data or {"command": "pytest -q"}, kind, can_remember, scope)


@pytest.fixture
def working(qapp, world, monkeypatch):
    """助手开着、正在回答"跑一下测试"。"""
    from yunyun.pet.assistant_confirm import AssistantConfirm
    monkeypatch.setattr(AssistantConfirm, "OPEN_MS", 20)
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "跑一下测试")
    return pet, machine, processes[0]


def test_confirm_card_asks_and_the_answer_goes_to_the_brain(qapp, working):
    pet, machine, process = working
    mode = pet.assistant
    process.say(protocol.ToolStarted("t1", "Bash", "pytest -q"), permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open and mode.confirm._shown == 1.0)
    assert mode.confirm.parent() is pet                          # 从属窗口
    assert machine.state is PetState.THINKING                    # 等你确认时芸芸一直在"想"
    assert bubbles(pet)[-1] == i18n.tr("assistant_waiting_confirm")
    mode.confirm._remember_button.click()
    assert process.sent == [protocol.PermissionReply(1, True, "", True)]
    assert wait_until(qapp, lambda: not mode.confirm.isVisible())


def test_confirms_come_one_at_a_time(qapp, working):
    pet, _, process = working
    confirm = pet.assistant.confirm
    process.say(permission(1), permission(2, "Edit", {"file_path": "a.py"}, "edit"))
    assert wait_until(qapp, lambda: confirm.is_open)
    assert confirm.request.request_id == 1 and confirm._more.text() == "还有 1 个"
    confirm._deny_button.click()
    assert confirm.is_open and confirm.request.request_id == 2 and confirm._more.text() == ""
    confirm._allow_button.click()
    assert [r.request_id for r in process.sent] == [1, 2]
    assert wait_until(qapp, lambda: not confirm.isVisible())


def test_reader_makes_way_for_the_confirm_card(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(protocol.TurnFinished(0.001, 0.001))
    mode._open_card(mode.cards[-1])
    assert wait_until(qapp, lambda: mode.reader.is_open)
    ask(pet, "再跑一次")
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open and not mode.reader.isVisible())
    mode._open_card(mode.cards[-1])                              # 确认卡片开着：点卡片不打开阅读框
    assert not mode.reader.isVisible()


def test_confirm_while_collapsed_reminds_then_shows_on_summon(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    process.say(permission(1), permission(2))
    assert not mode.confirm.isVisible()
    assert bubbles(pet).count(i18n.tr("assistant_confirm_waiting_said")) == 1   # 来了两个，只提醒一次
    pet.open_assistant()
    assert wait_until(qapp, lambda: mode.confirm.is_open and mode.confirm.request.request_id == 1)


def test_collapse_hides_the_confirm_but_keeps_it_waiting(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open)
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.confirm.isVisible())
    assert process.sent == [] and len(mode.session.pending_permissions) == 1


def test_stop_and_exit_drop_the_confirm(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open)
    mode.input._button.click()                                   # 停止
    assert process.interrupts == 1 and process.sent == []      # 大脑停下时把等着的都当成拒绝
    assert wait_until(qapp, lambda: not mode.confirm.isVisible())
    process.say(protocol.TurnFinished(stopped=True))
    ask(pet, "再来")
    process.say(permission(2))
    assert wait_until(qapp, lambda: mode.confirm.is_open)
    pet.close_assistant()                                        # 退出助手模式
    assert process.stopped and not mode.confirm.isVisible()
