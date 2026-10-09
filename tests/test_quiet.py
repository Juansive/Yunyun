"""免打扰时段和"睡觉"状态的测试。"""
from datetime import time

import pytest

from yunyun.notify.quiet import QuietHours, in_range
from yunyun.pet.state import PetState, StateMachine


@pytest.mark.parametrize("now, start, end, expected", [
    (time(23, 30), time(23, 0), time(8, 0), True),    # 跨午夜：晚上
    (time(2, 0), time(23, 0), time(8, 0), True),      # 跨午夜：凌晨
    (time(8, 0), time(23, 0), time(8, 0), False),     # 结束那一刻就不算了
    (time(12, 0), time(23, 0), time(8, 0), False),
    (time(12, 30), time(12, 0), time(13, 30), True),  # 白天的时段（午休）
    (time(14, 0), time(12, 0), time(13, 30), False),
    (time(9, 0), time(9, 0), time(9, 0), False),      # 开始 = 结束：没有时段
])
def test_in_range(now, start, end, expected):
    assert in_range(now, start, end) is expected


def test_quiet_hours_follow_clock_and_settings(qapp):
    clock = {"now": time(22, 0)}
    q = QuietHours(clock=lambda: clock["now"])
    assert not q.enabled and (q.start, q.end) == (time(23, 0), time(8, 0))   # 默认关闭、23:00–08:00
    events = []
    q.changed.connect(events.append)

    q.set_enabled(True)
    assert not q.active                           # 22:00 还没到
    clock["now"] = time(23, 1)
    q.check()                                     # 定时器每 30 秒会调用它
    assert q.active and events == [True]
    clock["now"] = time(8, 0)
    q.check()
    assert not q.active and events == [True, False]

    q.set_range(time(7, 0), time(9, 0))           # 改时段：立刻重新判断
    assert q.active and events == [True, False, True]

    q2 = QuietHours(clock=lambda: clock["now"])   # 重启后设置还在
    assert q2.enabled and (q2.start, q2.end) == (time(7, 0), time(9, 0)) and q2.active


def test_machine_sleeps_wakes_and_sleeps_again(qapp):
    m = StateMachine()
    m.set_quiet(True)
    assert m.state is PetState.SLEEPING           # 待机 → 睡觉
    m.on_key()
    assert m.state is PetState.WORKING            # 打字：醒来陪你
    m._on_idle_timeout()
    assert m.state is PetState.SLEEPING           # 停下一会儿：又睡着（不是待机）
    m.on_mail()
    assert m.state is PetState.SLEEPING           # 免打扰时来邮件：不激动
    m.set_quiet(False)
    assert m.state is PetState.IDLE               # 免打扰结束：醒来待机


def test_machine_rest_state_after_thinking(qapp):
    m = StateMachine()
    m.start_thinking()
    m.set_quiet(True)                             # 思考到一半进入免打扰：不打断思考
    assert m.state is PetState.THINKING
    m.stop_thinking()
    assert m.state is PetState.SLEEPING           # 思考完直接睡


def test_general_page_quiet_controls(qapp, monkeypatch):
    from yunyun import i18n
    from yunyun.pet.sprites import load_sprites
    from yunyun.pet.window import PetWindow
    i18n._current = "zh"
    q = QuietHours(clock=lambda: time(12, 0))
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.quiet_hours = q
    pet.open_settings("general")
    page = pet.settings_window.general_page

    assert not page._quiet_check.isChecked() and not page._quiet_start.isEnabled()   # 没开：时间框是灰的
    page._quiet_check.setChecked(True)
    assert q.enabled and page._quiet_start.isEnabled()
    from PySide6.QtCore import QTime
    page._quiet_start.setTime(QTime(11, 30))
    page._quiet_end.setTime(QTime(13, 0))
    assert (q.start, q.end) == (time(11, 30), time(13, 0)) and q.active
