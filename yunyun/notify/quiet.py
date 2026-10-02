"""免打扰时段：比如每天 23:00 到第二天 08:00。

- 设置（开关、开始时间、结束时间）存在 QSettings，默认关闭、23:00–08:00；
- 每 30 秒看一次钟，进入 / 离开免打扰时发出 changed(True / False)；
- 支持跨午夜（23:00–08:00）和白天的时段（比如 12:00–13:30 午休）。

免打扰期间：芸芸睡觉；来新邮件只计数（不激动、小红点不去角落）；用户打字时芸芸照常醒来陪着。
"""
from datetime import datetime, time

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

_ENABLED_KEY = "quiet/enabled"
_START_KEY = "quiet/start"
_END_KEY = "quiet/end"
DEFAULT_START = time(23, 0)
DEFAULT_END = time(8, 0)


def in_range(now, start, end):
    """now 是否落在 [start, end) 里；start > end 表示跨午夜（比如 23:00–08:00）。"""
    if start == end:
        return False             # 开始和结束一样：没有时段
    if start < end:
        return start <= now < end
    return now >= start or now < end   # 跨午夜：晚上这一段 或 早上那一段


class QuietHours(QObject):
    CHECK_MS = 30_000  # 多久看一次钟

    changed = Signal(bool)  # 进入（True）/ 离开（False）免打扰

    def __init__(self, clock=None, parent=None):
        """clock：返回"现在几点"（datetime.time）的函数；测试时可以换成假的钟。"""
        super().__init__(parent)
        self._clock = clock or (lambda: datetime.now().time())
        settings = QSettings()
        self._enabled = settings.value(_ENABLED_KEY, False, type=bool)
        self._start = _parse(settings.value(_START_KEY), DEFAULT_START)
        self._end = _parse(settings.value(_END_KEY), DEFAULT_END)
        self._active = self._compute()

        self._timer = QTimer(self)
        self._timer.setInterval(self.CHECK_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    @property
    def enabled(self):
        return self._enabled

    @property
    def start(self):
        return self._start

    @property
    def end(self):
        return self._end

    @property
    def active(self):
        """此刻是不是在免打扰。"""
        return self._active

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        QSettings().setValue(_ENABLED_KEY, self._enabled)
        self.check()

    def set_range(self, start, end):
        self._start, self._end = start, end
        settings = QSettings()
        settings.setValue(_START_KEY, start.strftime("%H:%M"))
        settings.setValue(_END_KEY, end.strftime("%H:%M"))
        self.check()

    def check(self):
        """重新看一次钟：状态变了就发出 changed。"""
        active = self._compute()
        if active != self._active:
            self._active = active
            self.changed.emit(active)

    def _compute(self):
        return self._enabled and in_range(self._clock(), self._start, self._end)


def _parse(value, default):
    """"23:00" → time(23, 0)；读不懂就用默认值。"""
    try:
        hour, minute = str(value).split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return default
