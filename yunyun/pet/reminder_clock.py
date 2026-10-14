"""提醒的闹钟（阶段 11.5）：芸芸本体到点响提醒（提醒本身在 notify/reminders.py，助手用工具设）。

- 到点：冒气泡（RING_MS，点一下收起）+ Windows 系统通知（托盘图标弹出，进通知中心）+ 说出来（助手模式开着、
  "芸芸出声回答"不是"不说"时，见 AssistantMode.say_aloud）；
- 芸芸没开着时错过的：打开后补一句"刚才错过了……"（晚了 reminders.LATE_S 秒以上）；
- 定时器排到下一个要响的时间；另外每分钟对一次（电脑睡眠醒来、改了系统时间、别的进程改了提醒文件都能接上）。
免打扰时段照样响：提醒是你自己要的（和闹钟一样）。日志不写提醒的内容。
"""
import logging
from datetime import datetime

from PySide6.QtCore import QObject, QTimer

from yunyun import i18n
from yunyun.notify import reminders

log = logging.getLogger(__name__)

RING_MS = 30_000          # 气泡留多久
CHECK_MS = 60_000         # 每分钟对一次
FIRST_CHECK_MS = 3_000    # 芸芸刚打开：等她出现在桌面上再补错过的
MAX_WAIT_MS = 60 * 60_000   # 定时器最多排这么远（再远的到时候再排，免得睡眠后时间对不上）


class ReminderClock(QObject):
    def __init__(self, pet, notify=None, now=datetime.now):
        """pet：芸芸（say 冒气泡、assistant 说出来）；notify(title, text)：Windows 系统通知（托盘图标的 showMessage，
        没有托盘就是 None）；now：现在几点（测试换成假的）。"""
        super().__init__(pet)
        self._pet = pet
        self._notify = notify
        self._now = now
        self._timer = QTimer(self, singleShot=True, timeout=self.check)
        self._every_minute = QTimer(self, interval=CHECK_MS, timeout=self.check)
        self._every_minute.start()
        QTimer.singleShot(FIRST_CHECK_MS, self, self.check)

    def check(self):
        """响到点的（错过的补一句），再排下一次。"""
        now = self._now()
        try:
            due = reminders.take_due(now)
        except OSError as e:
            log.warning("提醒文件写不了（%s）", type(e).__name__)
            due = []
        for reminder, late in due:
            self._ring(reminder, late > reminders.LATE_S)
        self._schedule(now)

    def _schedule(self, now):
        upcoming = reminders.next_time()
        if upcoming is None:
            self._timer.stop()
            return
        wait = max(0, int((upcoming - now).total_seconds() * 1000)) + 200   # 稍晚一点点：保证到点了
        self._timer.start(min(wait, MAX_WAIT_MS))

    def _ring(self, reminder, missed):
        when = reminders.due_time(reminder)
        text = (i18n.tr("reminder_missed", f"{when:%m-%d %H:%M}", reminder.text) if missed
                else i18n.tr("reminder_ring", reminder.text))
        log.info("提醒 %s 响了%s", reminder.id, "（错过的）" if missed else "")
        self._pet.say(text, RING_MS)
        if self._notify is not None:
            self._notify(i18n.tr("reminder_title"), text)
        assistant = getattr(self._pet, "assistant", None)
        if assistant is not None and not missed:
            assistant.say_aloud(reminder.text)
