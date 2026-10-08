"""端到端测试：右键"✦ 助手模式" → 输入框展开 → 发问 → 芸芸思考、气泡逐字说 → 回答完恢复 → 收起 / 闲置退出 / 菜单退出。

大脑用 tests/fake_assistant_process.py（不开子进程）；最后一个用真的 AssistantProcess + 假 worker 测"芸芸退出时等子进程退出"。
"""
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
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
from yunyun.pet.assistant_reader import AssistantReader
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

    def make(language, parent, resume=""):
        process = FakeProcess(language, parent, resume)
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
    assert menu_texts(pet)[1] == "历史对话…"           # 没开助手模式也有（第二天接着做昨天的事）
    assert "退出助手模式" not in menu_texts(pet)
    has_key(monkeypatch)
    pet.open_assistant()
    texts = menu_texts(pet)
    assert texts[1:4] == ["历史对话…", "隐藏卡片", "退出助手模式"]   # 后两项开着时才有


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


def test_yunyun_looks_busy_and_waiting(world, monkeypatch):
    """芸芸跟着助手变样子：用工具 = 在干活，有确认卡片 = 等你确认；这期间来了邮件不激动。"""
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    ask(pet, "改一下")
    assert machine.state is PetState.THINKING
    process.say(protocol.ToolStarted("t1", "Edit", "a.py"))
    assert machine.state is PetState.BUSY and pet._state is PetState.BUSY
    process.say(protocol.PermissionRequest(1, "Edit", {"file_path": "a.py"}, "edit", True, "F:\\proj"))
    assert machine.state is PetState.WAITING
    machine.on_mail()
    assert machine.state is PetState.WAITING                 # 新邮件只让小红点 +1
    pet.assistant.session.answer_permission(1, True)
    assert machine.state is PetState.BUSY
    process.say(protocol.ToolFinished("t1"), protocol.TurnFinished(0.001, 0.001))
    assert machine.state is PetState.IDLE


def test_collapse_keeps_assistant_then_idle_releases_only_the_brain(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    assert pet.assistant is mode and not processes[0].stopped   # 收起 ≠ 退出
    assert mode.deck.isVisible()                                # 卡片常亮
    assert mode._idle_timer.isActive()

    pet.open_assistant()                            # 唤出：停止计时
    assert not mode._idle_timer.isActive() and mode.input.is_open
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    mode._idle_timer.timeout.emit()                 # 闲置太久：只释放大脑
    assert processes[0].stopped and pet.assistant is mode and mode.deck.isVisible()
    pet.open_assistant()                            # 再唤出：新大脑，但接着同一段对话聊
    assert len(processes) == 2 and processes[1].started and processes[1].resume == "sess-1"
    assert [c.question for c in mode.cards] == ["问题"]


def test_left_click_on_yunyun_summons_the_assistant(qapp, world, monkeypatch):
    pet, _, _ = world
    clicks = []
    pet.clicked.connect(lambda: clicks.append(1))
    QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=pet.body_rect().center() - pet.pos())
    assert pet.assistant is None and clicks == [1]              # 没开助手模式：还是原来的单击
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    clicks.clear()
    QTest.mouseClick(pet, Qt.MouseButton.LeftButton, pos=pet.body_rect().center() - pet.pos())
    assert mode.input.is_open and clicks == []                  # 开着助手模式：左键 = 唤出输入框


def test_hide_cards_from_the_menu_and_summon_brings_the_reader_back(qapp, world, monkeypatch):
    monkeypatch.setattr(AssistantReader, "OPEN_MS", 10)
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    mode._open_card(mode.cards[-1])
    assert wait_until(qapp, lambda: mode.reader.is_open)
    assert "隐藏卡片" in menu_texts(pet)
    next(a for a in pet._build_menu().actions() if a.text() == "隐藏卡片").trigger()
    assert wait_until(qapp, lambda: not mode.deck.isVisible() and not mode.reader.isVisible()
                      and not mode.input.isVisible())
    assert "隐藏卡片" not in menu_texts(pet) and pet.assistant is mode
    pet.open_assistant()
    assert wait_until(qapp, lambda: mode.deck.isVisible() and mode.reader.is_open)   # 刚才开着的阅读框也回来了
    assert mode.reader.card is mode.cards[-1]


def test_cards_can_be_dragged_and_follow_yunyun(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.move(300, 200)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    before = mode.deck.cards_rect().topLeft()
    mode.deck.drag_started.emit()
    mode.deck.dragged.emit(QPoint(-40, 30))
    mode.deck.drag_finished.emit()
    moved = mode.deck.cards_rect().topLeft()
    assert moved == before + QPoint(-40, 30) and mode.has_custom_place
    pet.move(pet.pos() + QPoint(20, -10))            # 芸芸走了：卡片保持相对位置跟着
    assert mode.deck.cards_rect().topLeft() == moved + QPoint(20, -10)
    assert "卡片回到芸芸旁边" in menu_texts(pet)
    mode.reset_place()
    assert not mode.has_custom_place and mode.deck.cards_rect().topLeft() != moved + QPoint(20, -10)


def test_dragging_the_reader_moves_the_whole_group(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    assert wait_until(qapp, lambda: mode.reader.is_open and mode.reader._shown == 1.0)
    deck_before, reader_before = mode.deck.cards_rect().topLeft(), mode.reader.pos()
    mode.reader.drag_started.emit()
    mode.reader.dragged.emit(QPoint(15, 20))
    mode.reader.drag_finished.emit()
    assert mode.deck.cards_rect().topLeft() == deck_before + QPoint(15, 20)
    assert mode.reader.pos() == reader_before + QPoint(15, 20)   # 阅读框跟着卡片堆


def test_dragged_cards_stay_on_screen(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    mode.deck.drag_started.emit()
    mode.deck.dragged.emit(QPoint(-100000, -100000))
    area = QApplication.primaryScreen().availableGeometry()
    assert area.contains(mode.deck.cards_rect().topLeft())


def test_a_real_mouse_drag_on_the_deck_moves_it_but_a_click_still_opens(qapp, world, monkeypatch):
    monkeypatch.setattr(AssistantReader, "OPEN_MS", 10)
    pet, _, processes = world
    has_key(monkeypatch)
    pet.move(300, 200)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    assert wait_until(qapp, lambda: mode.deck._shown == 1.0)
    deck = mode.deck
    on_card = deck._card_rect(0.0).center().toPoint()
    before = deck.cards_rect().topLeft()
    QTest.mousePress(deck, Qt.MouseButton.LeftButton, pos=on_card)
    QTest.mouseMove(deck, on_card + QPoint(30, 25))
    QTest.mouseRelease(deck, Qt.MouseButton.LeftButton, pos=on_card + QPoint(30, 25))
    assert deck.cards_rect().topLeft() != before and not mode.reader.isVisible()   # 拖动：不算单击
    on_card = deck._card_rect(0.0).center().toPoint()
    QTest.mouseClick(deck, Qt.MouseButton.LeftButton, pos=on_card)
    assert wait_until(qapp, lambda: mode.reader.is_open)                            # 单击：照样打开


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

    def make(language, parent, resume=""):
        process = AssistantProcess(language, parent, command=(sys.executable, [str(FAKE_WORKER)], None), resume=resume)
        real.append(process)
        return process

    monkeypatch.setattr(session_module, "default_process", make)
    pet.open_assistant()
    process = real[0]
    assert wait_until(qapp, lambda: process.ready)
    pet.close_assistant(wait=True)
    assert not isValid(process) or not process.is_running()   # 返回时已经退出了（不靠事件循环）


# ---------- 卡片堆、新对话（U2） ----------

def finish_turn(pet, process, question, answer="答案", cost=0.0012, session_id="sess-1"):
    ask(pet, question)
    process.say(protocol.TextDelta(answer), protocol.TurnFinished(cost, cost, session_id=session_id))


def ask_then_collapse(qapp, pet, process, question):
    """问完就收起输入框（去忙别的了）。"""
    ask(pet, question)
    pet.assistant.input.collapse()
    assert wait_until(qapp, lambda: not pet.assistant.input.isVisible())


def test_done_while_collapsed_says_done_with_files_and_cost(qapp, world, monkeypatch):
    """输入框收起着时答完：气泡第一行"做完啦 · 改了 N 个文件 · 花费"，下面是回答开头（2026-10-08 用户选的）。"""
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    ask_then_collapse(qapp, pet, process, "修 bug")
    process.say(protocol.TextDelta("3 个 bug 都修好了"),
                protocol.TurnFinished(0.004, 0.004, files=("F:\\p\\a.py", "F:\\p\\b.py"), checkpoint="cp"))
    assert bubbles(pet)[-1] == "做完啦 · 改了 2 个文件 · $0.0040\n3 个 bug 都修好了"


def test_done_while_open_shows_only_the_answer(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "你好", answer="你好呀")
    assert bubbles(pet)[-1] == "你好呀"                         # 你一直看着：不用报"做完啦"


@pytest.mark.parametrize("ending", ["stopped", "error"])
def test_no_done_when_stopped_or_failed(qapp, world, monkeypatch, ending):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))
    ask_then_collapse(qapp, pet, process, "修 bug")
    if ending == "stopped":
        process.say(protocol.TextDelta("写到一半"), protocol.TurnFinished(0.001, 0.001, stopped=True))
    else:
        process.say(protocol.Error("network", "offline"), protocol.TurnFinished(None, 0.0))
    assert not any(text.startswith("做完啦") for text in bubbles(pet))


# ---------- 芸芸被挡住时的角落小条（4.4） ----------

@pytest.fixture
def covered(world, monkeypatch):
    """助手开着、芸芸被别的窗口挡住（不在全屏）。返回 (pet, process, 设置挡没挡住的函数, 全屏开关)。"""
    from yunyun.pet import assistant_mode
    pet, _, processes = world
    has_key(monkeypatch)
    fullscreen = {"on": False}
    monkeypatch.setattr(assistant_mode.winapi, "is_user_busy", lambda: fullscreen["on"])
    pet.open_assistant()
    process = processes[0]
    process.say(protocol.Ready("anthropic", "m"))

    def set_covered(value):
        pet._occluded = value
        pet.cover_checked.emit()               # 平时约每 0.5 秒检查一次
    set_covered(True)
    return pet, process, set_covered, fullscreen


def test_notice_asks_for_confirmation_while_covered(covered):
    pet, process, set_covered, _ = covered
    ask(pet, "跑一下测试")
    process.say(protocol.ToolStarted("t1", "Bash", "pytest -q"), permission())
    notice = pet.assistant.notice
    assert notice.isVisible() and notice.kind == "confirm"
    assert notice.text == "芸芸想执行一条命令：pytest -q"
    assert notice.parent() is None             # 独立窗口：显示不会把芸芸带出来
    pet.assistant.session.answer_permission(1, True)
    assert not notice.isVisible()              # 答了就收起


def test_notice_says_done_until_you_see_yunyun(covered):
    pet, process, set_covered, _ = covered
    finish_turn(pet, process, "修 bug", answer="修好了", cost=0.004)
    notice = pet.assistant.notice
    assert notice.isVisible() and notice.kind == "done" and notice.text == "芸芸：做完啦 · $0.0040"
    set_covered(True)
    assert notice.isVisible()                  # 一直留着
    set_covered(False)                         # 芸芸露出来了：算你看到了
    assert not notice.isVisible()
    set_covered(True)
    assert not notice.isVisible()              # 不会再冒出来


def test_notice_done_goes_away_when_you_ask_again(covered):
    pet, process, _, _ = covered
    finish_turn(pet, process, "一")
    assert pet.assistant.notice.isVisible()
    ask(pet, "二")
    assert not pet.assistant.notice.isVisible()


def test_no_notice_in_fullscreen_or_when_visible(covered):
    pet, process, set_covered, fullscreen = covered
    fullscreen["on"] = True
    finish_turn(pet, process, "一")
    assert not pet.assistant.notice.isVisible()      # 全屏（看视频、演示）时不弹
    fullscreen["on"] = False
    set_covered(True)
    assert pet.assistant.notice.isVisible()          # 退出全屏后出来
    set_covered(False)
    finish_turn(pet, process, "二")                  # 芸芸看得见：气泡就够了
    assert not pet.assistant.notice.isVisible()


def test_clicking_the_notice_brings_yunyun_and_the_cards(covered, monkeypatch):
    pet, process, _, _ = covered
    raised = []
    monkeypatch.setattr(pet, "bring_to_front", lambda: raised.append(True))
    mode = pet.assistant
    finish_turn(pet, process, "一")
    mode.input.close_now()
    assert mode.notice.isVisible()
    mode.notice.clicked.emit()
    assert raised and not mode.notice.isVisible() and mode.input.is_open


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


def test_exit_and_reopen_continues_the_same_chat(qapp, world, monkeypatch):
    """芸芸没重启时，退出助手模式再打开：卡片还在，大脑接着同一段对话（resume）。"""
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "第一段", session_id="sess-a")
    pet.close_assistant()
    assert wait_until(qapp, lambda: pet.assistant is None)

    pet.open_assistant()
    assert [c.question for c in pet.assistant.cards] == ["第一段"]   # 不再插分隔卡
    assert pet.assistant.deck.isVisible() and processes[1].resume == "sess-a"


def test_ctrl_n_starts_a_new_conversation(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "旧问题")
    QApplication.sendEvent(mode.input._edit, QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_N, Qt.KeyboardModifier.ControlModifier))
    assert processes[0].stopped and len(processes) == 2 and processes[1].resume == ""   # 新的大脑会话
    assert mode.cards == []                                    # 卡片堆只放当前这段：清空
    assert i18n.tr("assistant_new_chat_said") in bubbles(pet)
    from yunyun.pet import assistant_history
    assert [c.question for c in assistant_history.load()] == ["旧问题"]   # 上一段在历史里


def test_new_conversation_while_answering_keeps_the_half_answer(world, monkeypatch):
    from yunyun.pet import assistant_history
    pet, machine, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    ask(pet, "slow")
    processes[0].say(protocol.TextDelta("说到一半"))
    mode.deck.new_conversation_requested.emit()      # 卡片堆右上角的"＋ 新对话"
    assert not mode.busy and machine.state is PetState.IDLE
    assert mode.cards == []
    assert [(c.question, c.answer, c.stopped) for c in assistant_history.load()] == [("slow", "说到一半", True)]


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
    assert mode.cards == []                                                # 卡片堆清空（上一段在历史里）
    assert i18n.tr("assistant_folder_said", "my-project") in bubbles(pet)

    actions = folder_actions(mode)                   # 菜单里出现了最近用过的，打着勾
    recent = next(a for a in actions.values() if a.toolTip() == str(project))
    assert recent.isChecked() and not actions[i18n.tr("assistant_folder_none")].isChecked()
    recent.trigger()                                 # 选同一个：什么都不变
    assert len(processes) == 2

    actions[i18n.tr("assistant_folder_none")].trigger()
    assert assistant_settings.load_settings().workspace == "" and len(processes) == 3
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


def test_deck_stays_when_the_input_collapses(qapp, world, monkeypatch):
    """2026-10-08 用户要求：点到别的程序（比如看文档）时卡片常亮，只有输入框收起。"""
    pet, _, processes = world
    has_key(monkeypatch)
    pet.move(100, 100)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    assert mode.deck.isVisible()
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    assert mode.deck.isVisible() and mode.cards_shown


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


def test_new_conversation_closes_the_reader_and_empties_the_deck(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    assert wait_until(qapp, lambda: mode.reader.is_open)
    mode.new_conversation()
    assert not mode.reader.isVisible() and mode.cards == []
    assert wait_until(qapp, lambda: not mode.deck.isVisible())


def test_collapse_keeps_the_reader_and_exit_closes_it(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    assert wait_until(qapp, lambda: mode.reader.isVisible())
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    assert mode.reader.is_open and mode.deck.isVisible()      # 输入框收起，阅读框和卡片堆留着
    reader = mode.reader
    pet.close_assistant()
    assert not reader.isVisible()


def test_reader_losing_focus_stays_open(qapp, two_cards):
    pet, mode = two_cards
    mode.deck.card_clicked.emit(mode.deck.card(0))
    assert wait_until(qapp, lambda: mode.reader.is_open)
    mode.reader.dismissed.emit()                     # 焦点去了别的程序（比如点了文档）
    qapp.processEvents()
    assert mode.reader.is_open and mode.deck.isVisible()


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
    assert machine.state is PetState.WAITING                     # 等你确认时芸芸是"等你确认"的样子（阶段 4）
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


def test_confirm_while_cards_hidden_reminds_then_shows_on_summon(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    mode.hide_cards()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    process.say(permission(1), permission(2))
    assert not mode.confirm.isVisible()
    assert bubbles(pet).count(i18n.tr("assistant_confirm_waiting_said")) == 1   # 来了两个，只提醒一次
    pet.open_assistant()
    assert wait_until(qapp, lambda: mode.confirm.is_open and mode.confirm.request.request_id == 1)


def test_confirm_shows_even_when_only_the_input_is_collapsed(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    mode.input.collapse()                                          # 只是在看别的东西：卡片组还显示着
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open)
    assert i18n.tr("assistant_confirm_waiting_said") not in bubbles(pet)


def test_collapse_keeps_the_confirm_waiting_and_visible(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open)
    mode.input.collapse()
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    assert mode.confirm.is_open and process.sent == [] and len(mode.session.pending_permissions) == 1
    mode.confirm._allow_button.click()                             # 收起着也能直接点
    assert process.sent == [protocol.PermissionReply(1, True, "", False)]


def test_reader_steps_aside_for_a_confirm_and_comes_back(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(protocol.TurnFinished(0.001, 0.001))
    mode._open_card(mode.cards[-1])
    assert wait_until(qapp, lambda: mode.reader.is_open)
    ask(pet, "再来")
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open and not mode.reader.isVisible())
    mode.confirm._allow_button.click()
    assert wait_until(qapp, lambda: mode.reader.is_open and mode.reader.card is mode.cards[-1])


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


def test_emergency_stop_from_the_menu_even_when_collapsed(qapp, working):
    pet, machine, process = working
    mode = pet.assistant
    process.say(permission())
    assert wait_until(qapp, lambda: mode.confirm.is_open)
    mode.input.collapse()                                          # 收起着也能停
    assert wait_until(qapp, lambda: not mode.input.isVisible())
    texts = menu_texts(pet)
    assert texts[1:5] == ["历史对话…", "■ 停止助手", "隐藏卡片", "退出助手模式"]   # 停止：干活时才有
    stop = next(a for a in pet._build_menu().actions() if a.text() == "■ 停止助手")
    stop.trigger()
    assert process.interrupts == 1 and mode.session.pending_permissions == []
    process.say(protocol.TurnFinished(stopped=True))
    assert machine.state is PetState.IDLE and "■ 停止助手" not in menu_texts(pet)
    assert pet.assistant is mode and not process.stopped           # 停止 ≠ 退出：还能接着聊


# ---------- 历史对话 ----------

def test_next_day_continue_yesterdays_chat_from_history(qapp, world, monkeypatch, tmp_path):
    """用户的场景：前一天任务没做完，第二天（重启了芸芸）从历史对话里接着做。"""
    from yunyun.assistant import settings as settings_module
    monkeypatch.setattr(AssistantReader, "OPEN_MS", 10)
    project = tmp_path / "proj"
    project.mkdir()
    settings_module.set_workspace(str(project))
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "修昨天的 bug", "改了一半", session_id="sess-yesterday")
    pet.close_assistant()
    settings_module.set_workspace("")                 # 第二天在别的文件夹（这里：只聊天）

    another = PetWindow(load_sprites())               # 重新启动芸芸：卡片堆是空的
    try:
        connect_assistant(another, StateMachine())
        menu = {a.text(): a for a in another._build_menu().actions()}
        menu["历史对话…"].trigger()                   # 右键 → 历史对话…（助手模式还没开）
        mode = another.assistant
        assert mode.cards == [] and wait_until(qapp, lambda: mode.history.is_open)
        items = [mode.history._list.item(i) for i in range(mode.history._list.count())]
        conversation = next(i.data(Qt.ItemDataRole.UserRole) for i in items if i.data(Qt.ItemDataRole.UserRole))
        assert conversation.title == "修昨天的 bug" and conversation.workspace == str(project)
        mode.history.open_conversation(conversation)
        assert "改了一半" in mode.history._detail.toPlainText()
        mode.history._continue.click()                  # 接着聊
        assert processes[-1].resume == "sess-yesterday"  # 用昨天那段的会话编号恢复
        assert settings_module.load_settings().workspace == str(project)   # 切回了那段的工作文件夹
        assert [c.question for c in mode.cards] == ["修昨天的 bug"]          # 那段的卡片放回卡片堆
        assert mode.cards[0].generation == 0             # 以前的改动撤销不了了
        assert wait_until(qapp, lambda: not mode.history.isVisible())
    finally:
        another.close_assistant()
        another.close()


def test_history_hides_the_current_chat_and_waits_for_confirms(qapp, world, monkeypatch):
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    mode = pet.assistant
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "现在这段", session_id="sess-now")
    mode.show_history()
    assert wait_until(qapp, lambda: mode.history.is_open)
    assert mode.history._list.count() == 0 and mode.history._empty.isVisibleTo(mode.history)   # 正在聊的不列
    mode.history.hide_animated()
    ask(pet, "改文件")
    processes[0].say(protocol.PermissionRequest(1, "Edit", {"file_path": "a.py"}, "edit", True, "F:\\p"))
    mode.show_history()                               # 有确认在等：先回答确认
    assert i18n.tr("assistant_answer_confirm_first") in bubbles(pet)


def test_clearing_history_in_settings_empties_the_deck(qapp, world, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from yunyun.pet import assistant_history
    pet, _, processes = world
    has_key(monkeypatch)
    pet.open_assistant()
    processes[0].say(protocol.Ready("anthropic", "m"))
    finish_turn(pet, processes[0], "问题")
    assert assistant_history.history_path().exists()
    pet.open_settings("assistant")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a: QMessageBox.StandardButton.Yes))
    pet.settings_window._assistant_page._clear_history.click()
    assert not assistant_history.history_path().exists()
    assert pet.assistant.cards == []
    assert wait_until(qapp, lambda: not pet.assistant.deck.isVisible())


# ---------- 撤销 ----------

def test_undo_from_the_reader(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(protocol.TurnFinished(0.001, 0.001, checkpoint="cp-1", files=("F:\\p\\a.py", "F:\\p\\b.py")))
    card = mode.cards[-1]
    assert card.files and card.checkpoint == "cp-1" and card.generation == mode.session.generation
    mode._open_card(card)
    assert wait_until(qapp, lambda: mode.reader.is_open)
    assert mode.reader._undo.isEnabled()
    mode.reader._undo.click()
    mode.reader._undo.click()                                     # 点两次才算
    assert process.sent == [protocol.Rewind("cp-1")]
    assert mode.reader._undo.text() == i18n.tr("assistant_undo_working")
    process.say(protocol.Rewound("cp-1", True))
    assert card.undone and mode.reader._undo.text() == i18n.tr("assistant_undo_done")
    assert i18n.tr("assistant_undo_said", 2) in bubbles(pet)


def test_undo_is_disabled_while_answering_and_after_a_new_chat(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(protocol.TurnFinished(0.001, 0.001, checkpoint="cp-1", files=("a.py",)))
    card = mode.cards[-1]
    mode._open_card(card)
    assert wait_until(qapp, lambda: mode.reader.is_open)
    ask(pet, "再改一下")
    assert not mode.reader._undo.isEnabled()                      # 回答时不能撤销
    process.say(protocol.TurnFinished(0.001, 0.001))
    assert mode.reader._undo.isEnabled()
    mode.new_conversation()
    assert mode._undo_state_for(card) == "expired" and not mode.reader._undo.isEnabled()


def test_failed_undo_says_so(qapp, working):
    pet, _, process = working
    mode = pet.assistant
    process.say(protocol.TurnFinished(0.001, 0.001, checkpoint="cp-1", files=("a.py",)))
    card = mode.cards[-1]
    mode._undo(card)
    process.say(protocol.Rewound("cp-1", False, "No file checkpoint"))
    assert not card.undone and i18n.tr("assistant_undo_failed") in bubbles(pet)
    assert mode._undo_state_for(card) == "ready"                  # 还可以再试
