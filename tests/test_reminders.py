"""提醒（notify/reminders.py，阶段 11.5）：一次性 / 每天 / 工作日 / 每周、到点取出、错过的、上限。"""
from datetime import datetime

import pytest

from yunyun.notify import reminders as rm

NOW = datetime(2026, 10, 14, 14, 30)   # 周三


def test_one_off_reminder_rings_once():
    r = rm.add("  给妈妈 打电话 ", datetime(2026, 10, 14, 20, 30), now=NOW)
    assert (r.id, r.text, r.due, r.repeat) == ("t1", "给妈妈 打电话", "2026-10-14T20:30", "")
    assert rm.take_due(datetime(2026, 10, 14, 20, 29)) == []
    fired = rm.take_due(datetime(2026, 10, 14, 20, 30, 20))
    assert [(f.text, late) for f, late in fired] == [("给妈妈 打电话", 20)] and rm.load() == []
    assert rm.next_time() is None


def test_repeating_reminders_move_to_the_next_time():
    rm.add("喝水", datetime(2026, 10, 14, 15, 0), "daily", now=NOW)
    rm.add("开组会", datetime(2026, 10, 16, 9, 0), "weekdays", now=NOW)        # 周五
    rm.add("交周报", datetime(2026, 10, 14, 10, 0), "weekly", now=NOW)         # 已经过了：从下周算起
    assert [(r.text, r.due) for r in rm.load()] == [
        ("喝水", "2026-10-14T15:00"), ("开组会", "2026-10-16T09:00"), ("交周报", "2026-10-21T10:00")]
    rm.take_due(datetime(2026, 10, 16, 9, 1))
    by_text = {r.text: r.due for r in rm.load()}
    assert by_text["开组会"] == "2026-10-19T09:00"                                # 跳过周末到周一
    assert by_text["喝水"] == "2026-10-16T15:00"                                  # 错过的几次只响一次，挪到下一次
    assert rm.next_time() == datetime(2026, 10, 16, 15, 0)


def test_weekday_reminder_never_starts_on_a_weekend():
    r = rm.add("早会", datetime(2026, 10, 17, 9, 0), "weekdays", now=NOW)       # 周六 → 周一
    assert r.due == "2026-10-19T09:00"


@pytest.mark.parametrize("text, when, repeat, message", [
    ("  ", datetime(2026, 10, 14, 20, 0), "", "empty"),
    ("x", datetime(2026, 10, 14, 14, 0), "", "already passed"),
    ("x", datetime(2027, 12, 1, 9, 0), "", "more than a year"),
    ("x", datetime(2026, 10, 14, 20, 0), "monthly", "Unknown repeat"),
])
def test_bad_reminders_are_refused(text, when, repeat, message):
    with pytest.raises(rm.ReminderError, match=message):
        rm.add(text, when, repeat, now=NOW)


def test_limit_cancel_clear_and_describe(monkeypatch):
    monkeypatch.setattr(rm, "MAX_REMINDERS", 2)
    rm.add("一", datetime(2026, 10, 14, 15, 0), now=NOW)
    rm.add("二", datetime(2026, 10, 15, 9, 0), "weekdays", now=NOW)
    with pytest.raises(rm.ReminderError, match="too many"):
        rm.add("三", datetime(2026, 10, 14, 16, 0), now=NOW)
    assert rm.describe(rm.load()[1]) == "t2 · 2026-10-15 Thu 09:00 · on weekdays · 二"
    assert rm.cancel("t1") and not rm.cancel("t1") and [r.id for r in rm.load()] == ["t2"]
    rm.clear()
    assert rm.load() == []


def test_broken_file_is_treated_as_empty(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"reminders": [{"id": "t1", "text": "x", "due": "not a time"}, {"id": "t2", "text": "ok", '
                   '"due": "2026-10-14T15:00", "repeat": "hourly"}]}', encoding="utf-8")
    assert [(r.id, r.repeat) for r in rm.load(bad)] == [("t2", "")]
    bad.write_text("{oops", encoding="utf-8")
    assert rm.load(bad) == []
