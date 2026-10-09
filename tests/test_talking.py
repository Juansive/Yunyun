"""说话的样子（2026-10-09 用户定）：头顶有气泡时，待机 / 睡觉 / 打字 / 激动都换成说话；助手在想 / 干活 / 等确认时不变。"""
import shutil

import pytest

from yunyun.pet.bubble import BubbleStack
from yunyun.pet.sprites import ASSET_DIR, load_sprites
from yunyun.pet.state import PetState, StateMachine
from helpers import wait_until


def test_talking_covers_rest_typing_and_excited(qapp):
    m = StateMachine()
    m.set_talking(True)
    assert m.state is PetState.TALKING          # 待机 → 说话
    m.set_talking(False)
    assert m.state is PetState.IDLE

    m.on_key()
    assert m.state is PetState.WORKING
    m.set_talking(True)
    assert m.state is PetState.TALKING          # 打字时说话
    m.on_key()
    assert m.state is PetState.TALKING          # 接着打字也不打断
    m.set_talking(False)
    assert m.state is PetState.WORKING          # 说完：刚才在打字，回到工作

    m = StateMachine()                          # 没在打字的时候
    m.on_mail()
    assert m.state is PetState.EXCITED
    m.set_talking(True)
    assert m.state is PetState.TALKING          # 激动时说话
    m.set_talking(False)
    assert m.state is PetState.IDLE


def test_talking_wakes_her_up_and_she_sleeps_again(qapp):
    m = StateMachine()
    m.set_quiet(True)
    assert m.state is PetState.SLEEPING
    m.set_talking(True)
    assert m.state is PetState.TALKING          # 睡着时说话：醒来说
    m.set_talking(False)
    assert m.state is PetState.SLEEPING         # 说完再睡


def test_mail_while_talking_does_not_excite(qapp):
    m = StateMachine()
    m.set_talking(True)
    m.on_mail()
    assert m.state is PetState.TALKING


@pytest.mark.parametrize("activity, state", [("thinking", PetState.THINKING), ("busy", PetState.BUSY),
                                             ("waiting", PetState.WAITING)])
def test_assistant_states_win_over_talking(qapp, activity, state):
    m = StateMachine()
    m.set_talking(True)
    m.set_assistant(activity)
    assert m.state is state                     # 助手在忙：保持那个样子（气泡里在说进度）
    m.set_assistant("")
    assert m.state is PetState.TALKING          # 忙完了气泡还在：说话
    m.set_talking(False)
    assert m.state is PetState.IDLE


def test_bubbles_tell_when_she_starts_and_stops_talking(qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QWidget
    owner = QWidget()
    stack = BubbleStack(owner)
    events = []
    stack.speaking_changed.connect(events.append)
    anchor = QRect(300, 300, 160, 160)
    stack.add("你好", anchor, 100)
    stack.add("还有一句", anchor, 300)
    assert events == [True]                     # 第二个气泡不再重复发
    assert wait_until(qapp, lambda: events == [True, False], 3)   # 最后一个消失了才算说完

    stack.show_live("芸芸在想……", anchor)
    stack.end_live(100)
    assert wait_until(qapp, lambda: events == [True, False, True, False], 3)


def test_talking_sprites(qapp, tmp_path):
    sprites = load_sprites()
    assert len(sprites.talking) == 2            # 占位图（tools/make_placeholders.py）
    folder = tmp_path / "pet"
    shutil.copytree(ASSET_DIR, folder, ignore=shutil.ignore_patterns("talking_*"))
    fallback = load_sprites(folder)
    assert fallback.talking is fallback.excited   # 画师的图里没有说话的：用激动的图，不会启动失败


def test_window_talks_faster_than_other_loops(qapp):
    from yunyun.pet.window import PetWindow
    sprites = load_sprites()
    pet = PetWindow(sprites)
    pet.set_state(PetState.TALKING)
    assert pet._current in sprites.talking and pet._loop_timer.interval() == pet.TALK_MS
    assert wait_until(qapp, lambda: pet._current is sprites.talking[1], 2)   # 嘴一张一合
    pet.set_state(PetState.EXCITED)
    assert pet._loop_timer.interval() == pet.LOOP_MS
    pet.set_state(PetState.TALKING)
    pet._play_loop(sprites.lifted)               # 说话时被拎起来：拎起来的图照常按 LOOP_MS 播
    assert pet._loop_timer.interval() == pet.LOOP_MS
