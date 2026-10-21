"""助手模式的样子（阶段 4.1 / 4.2）：在干活、等你确认，以及和邮件、打字、免打扰同时发生时显示哪个。

优先级（2026-10-08 用户定）：等你确认 > 在干活 > 思考 > 激动 > 工作（打字）> 待机 / 睡觉。
"""
import shutil

import pytest

from yunyun.pet.sprites import ASSET_DIR, load_sprites
from yunyun.pet.state import PetState, StateMachine

W, B, T = "waiting", "busy", "thinking"


@pytest.mark.parametrize("assistant, mail_thinking, expected", [
    ("", False, PetState.IDLE),
    (T, False, PetState.THINKING),
    (B, False, PetState.BUSY),
    (W, False, PetState.WAITING),
    ("", True, PetState.THINKING),         # 只在处理邮件
    (T, True, PetState.THINKING),
    (B, True, PetState.BUSY),              # 在干活 + 处理邮件 → 在干活
    (W, True, PetState.WAITING),           # 等你确认 + 任何事 → 等你确认
])
def test_priority_table(qapp, assistant, mail_thinking, expected):
    m = StateMachine()
    if mail_thinking:
        m.start_thinking("mail")
    m.set_assistant(assistant)
    assert m.state is expected


@pytest.mark.parametrize("activity, state", [(T, PetState.THINKING), (B, PetState.BUSY), (W, PetState.WAITING)])
def test_new_mail_while_assistant_is_busy_does_not_excite(qapp, activity, state):
    m = StateMachine()
    m.set_assistant(activity)
    m.on_mail()                              # 只是小红点 +1（小红点不归状态机管）
    assert m.state is state
    m.on_key()                               # 打字也不打断
    assert m.state is state


def test_assistant_idle_then_mail_excites(qapp):
    m = StateMachine()
    m.set_assistant(B)
    m.set_assistant("")
    m.on_mail()
    assert m.state is PetState.EXCITED


def test_assistant_takes_over_from_excited_and_mail_timer_does_not_interrupt(qapp):
    m = StateMachine()
    m.on_mail()
    m.set_assistant(W)
    assert m.state is PetState.WAITING
    m._on_excited_done()                     # 激动的计时到了也不打断
    assert m.state is PetState.WAITING


def test_one_turn_goes_through_the_states_and_back(qapp):
    """一问：想 → 用工具 → 等你确认 → 接着干 → 想 → 答完；最近打过字就回到工作。"""
    m = StateMachine()
    m.on_key()
    seen = []
    m.state_changed.connect(seen.append)
    for activity in (T, B, W, B, T, ""):
        m.set_assistant(activity)
    assert seen == [PetState.THINKING, PetState.BUSY, PetState.WAITING, PetState.BUSY, PetState.THINKING,
                    PetState.WORKING]


def test_mail_thinking_keeps_going_after_the_assistant_finishes(qapp):
    m = StateMachine()
    m.start_thinking("mail")
    m.set_assistant(W)
    m.set_assistant("")
    assert m.state is PetState.THINKING      # 邮件还没处理完
    m.stop_thinking("mail")
    assert m.state is PetState.IDLE


def test_quiet_hours_still_show_the_assistant(qapp):
    m = StateMachine()
    m.set_quiet(True)
    assert m.state is PetState.SLEEPING
    m.set_assistant(B)                       # 是你自己在用：照常显示
    assert m.state is PetState.BUSY
    m.set_assistant("")
    assert m.state is PetState.SLEEPING


def test_unknown_activity_counts_as_nothing(qapp):
    m = StateMachine()
    m.set_assistant(B)
    m.set_assistant("dancing")
    assert m.state is PetState.IDLE


# ---------- 图 ----------

def test_new_state_sprites_exist(qapp):
    sprites = load_sprites()
    assert sprites.busy and sprites.waiting and sprites.busy != sprites.thinking


def test_missing_new_sprites_fall_back_to_thinking(qapp, tmp_path):
    """画师的图到之前可能没有这两组：没有就用思考的图，不会启动失败。"""
    folder = tmp_path / "pet"
    shutil.copytree(ASSET_DIR, folder, ignore=shutil.ignore_patterns("busy_*", "waiting_*"))
    sprites = load_sprites(folder)
    assert sprites.busy == sprites.thinking and sprites.waiting == sprites.thinking


def test_window_plays_the_new_states(qapp):
    import time

    from yunyun.pet.window import PetWindow
    sprites = load_sprites()
    pet = PetWindow(sprites)
    for state, frames in ((PetState.BUSY, sprites.busy), (PetState.WAITING, sprites.waiting)):
        pet.set_state(state)
        seen = set()
        end = time.time() + 1.6
        while time.time() < end and len(seen) < len(frames):
            qapp.processEvents()
            if pet._current in frames:
                seen.add(frames.index(pet._current))
            time.sleep(0.02)
        assert seen == set(range(len(frames)))   # 几帧轮流播放
