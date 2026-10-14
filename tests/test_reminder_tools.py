"""提醒（阶段 11.5）：助手的三个工具、芸芸本体的闹钟、设置 → 提醒、右键菜单、气泡点一下收起。不联网、不弹真的系统通知。"""
import asyncio
from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QMessageBox

from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.assistant.tools.reminders import parse_time, reminder_tools
from yunyun.notify import reminders as rm
from yunyun.notify.reminders import ReminderError
from yunyun.pet.reminder_clock import ReminderClock
from yunyun.pet.reminders_page import RemindersPage, when_text

NOW = datetime(2026, 10, 14, 14, 30)   # 周三


class FakeBridge:
    def __init__(self):
        self.told = []

    def notify(self, event):
        self.told.append(event)


def call(tools, name, args):
    tool = next(t for t in tools if t.name == name)
    result = asyncio.run(tool.handler(args))
    return result["content"][0]["text"], bool(result.get("is_error"))


# ---------- 助手的工具 ----------

def test_parse_time_understands_minutes_clock_times_and_dates():
    assert parse_time({"in_minutes": 25}, NOW) == datetime(2026, 10, 14, 14, 55)
    assert parse_time({"at": "20:30"}, NOW) == datetime(2026, 10, 14, 20, 30)
    assert parse_time({"at": "09:00"}, NOW) == datetime(2026, 10, 15, 9, 0)            # 今天的已经过了：明天
    assert parse_time({"at": "2026-10-20T09:00"}, NOW) == datetime(2026, 10, 20, 9, 0)
    for bad in ({"at": "下午三点"}, {"in_minutes": "abc"}, {"in_minutes": 0}, {}):
        with pytest.raises(ReminderError):
            parse_time(bad, NOW)


def test_set_list_and_cancel_tell_yunyun_to_reschedule():
    bridge = FakeBridge()
    tools = reminder_tools(bridge, clock=lambda: NOW)
    text, error = call(tools, "set_reminder", {"text": "喝水", "at": "15:00", "repeat": "daily"})
    assert not error and text == "Set t1 · 2026-10-14 Wed 15:00 · every day · 喝水. Now is 2026-10-14 Wed 14:30."
    assert call(tools, "set_reminder", {"text": "休息", "in_minutes": 25})[0].startswith("Set t2 · 2026-10-14 Wed 14:55")
    text, error = call(tools, "set_reminder", {"text": "x", "at": "2026-10-01 09:00"})
    assert error and "already passed" in text and "Now is" in text                       # 过去的时间：告诉它现在几点
    assert bridge.told == [protocol.RemindersChanged()] * 2
    text, _ = call(tools, "list_reminders", {})
    assert text.splitlines()[0] == "Now is 2026-10-14 Wed 14:30." and "t2 · 2026-10-14 Wed 14:55 · once · 休息" in text
    assert call(tools, "cancel_reminder", {"id": "t1"}) == ("Cancelled t1.", False)
    assert call(tools, "cancel_reminder", {"id": "t1"}) == ("There is no reminder t1.", True)
    assert len(bridge.told) == 3 and [r.id for r in rm.load()] == ["t2"]


# ---------- 芸芸本体的闹钟 ----------

class FakePet(QObject):
    def __init__(self):
        super().__init__()
        self.said, self.assistant = [], None

    def say(self, text, duration_ms=4000):
        self.said.append((text, duration_ms))


class FakeAssistant:
    def __init__(self):
        self.aloud = []

    def say_aloud(self, text):
        self.aloud.append(text)


def test_clock_rings_on_time_and_tells_about_missed_ones(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    pet, notes, clock_now = FakePet(), [], [datetime(2026, 10, 14, 9, 0)]
    pet.assistant = FakeAssistant()
    rm.add("开组会", datetime(2026, 10, 14, 10, 0), now=clock_now[0])
    rm.add("喝水", datetime(2026, 10, 14, 15, 0), "daily", now=clock_now[0])
    clock = ReminderClock(pet, notify=lambda title, text: notes.append((title, text)), now=lambda: clock_now[0])
    clock.check()
    assert pet.said == [] and clock._timer.isActive()                                   # 还没到：排好了下一次
    assert 3_590_000 <= clock._timer.remainingTime() <= 3_600_300                      # 一小时后（10:00）
    clock_now[0] = datetime(2026, 10, 14, 10, 0, 30)
    clock.check()
    assert pet.said == [("⏰ 提醒：开组会", 30_000)] and notes == [("芸芸提醒你", "⏰ 提醒：开组会")]
    assert pet.assistant.aloud == ["开组会"]                                            # 助手开着：说出来
    clock_now[0] = datetime(2026, 10, 15, 8, 0)                                         # 芸芸关着时错过了 15:00
    clock.check()
    assert pet.said[-1] == ("⏰ 刚才错过了一个提醒（10-14 15:00）：喝水", 30_000)
    assert pet.assistant.aloud == ["开组会"]                                            # 错过的不念
    assert rm.load()[0].due == "2026-10-15T15:00"                                       # 每天的：挪到今天


def test_clock_works_without_a_tray_or_reminders(qapp):
    pet = FakePet()
    clock = ReminderClock(pet)   # 没有托盘也不出错（notify=None）
    clock.check()
    assert not clock._timer.isActive()                                                 # 没有提醒：不排
    rm.add("马上", datetime.now() + timedelta(minutes=1))
    clock.check()
    assert clock._timer.isActive() and pet.said == []


# ---------- 设置 → 提醒 ----------

def test_page_lists_reminders_and_deletes_them(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    rm.add("喝水", datetime(2026, 10, 14, 15, 0), "daily", now=NOW)
    rm.add("交报告", datetime(2026, 10, 15, 9, 0), now=NOW)
    rm.add("开组会", datetime(2026, 10, 20, 9, 0), "weekdays", now=NOW)
    page = RemindersPage(now=lambda: NOW)
    rows = [[page._tree.topLevelItem(i).text(c) for c in range(3)] for i in range(page._tree.topLevelItemCount())]
    assert rows == [["今天 15:00", "每天", "喝水"], ["明天 09:00", "—", "交报告"], ["10-20 周二 09:00", "工作日", "开组会"]]
    assert not page._delete_button.isEnabled()
    page._tree.setCurrentItem(page._tree.topLevelItem(1))
    page._tree.topLevelItem(1).setSelected(True)
    page._delete()
    assert [r.text for r in rm.load()] == ["喝水", "开组会"]
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page._clear()
    assert rm.load() == [] and page._tree.isHidden() and not page._empty.isHidden()
    assert when_text(datetime(2026, 10, 14, 8, 5), NOW) == "今天 08:05"


# ---------- 右键菜单、气泡 ----------

def test_menu_has_a_reminders_entry_when_there_are_some(qapp, monkeypatch):
    from yunyun.pet import window as window_module
    from yunyun.pet.sprites import load_sprites
    from yunyun.pet.window import PetWindow
    monkeypatch.setattr(i18n, "_current", "zh")
    monkeypatch.setattr(window_module, "assistant_available", lambda: True)
    pet = PetWindow(load_sprites())
    texts = lambda: [a.text() for a in pet._build_menu().actions()]
    assert not any(t.startswith("提醒") for t in texts())
    rm.add("喝水", datetime.now() + timedelta(days=1))
    assert "提醒（1）…" in texts()
    pet.close()


def test_clicking_a_bubble_hides_it(qapp):
    from yunyun.pet.bubble import Bubble
    from PySide6.QtWidgets import QWidget
    owner = QWidget()
    bubble = Bubble("⏰ 提醒：喝水", owner)
    gone = []
    bubble.expired.connect(gone.append)
    bubble.show()
    bubble.start_countdown(30_000)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    bubble.mousePressEvent(event)
    assert gone == [bubble] and not bubble.isVisible()
