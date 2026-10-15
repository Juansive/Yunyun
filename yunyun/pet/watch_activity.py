"""8.6a（2026-10-16）芸芸当你自己的 Claude Code 的"脸"——表情：它在干活，芸芸就是忙碌的样子；要你批准 / 让你选，就是等待的样子；
做完、出错停下、闲着（被你按 Esc 打断时没有"做完"，靠它）、会话结束，就回到平时。状态机里和助手模式的合在一起算（StateMachine.set_watch）。

同时开着几段对话时分开算：有一段在等你 → 等待；不然有一段在干活 → 忙碌。
收不到结束的情况（Claude Code 被关掉了……）：一段对话太久没动静就当它不忙了（STALE_S；跑很久的命令中途没有事件，
会先回到平时，命令跑完 PostToolUse 一来又是忙碌）。
"""
import time

from PySide6.QtCore import QObject, QTimer, Signal

BUSY_EVENTS = ("busy",)
WAITING_EVENTS = ("approval", "permission", "choice")
DONE_EVENTS = ("stop", "idle", "failed", "end")


class WatchActivity(QObject):
    STALE_S = {"busy": 10 * 60, "waiting": 30 * 60}
    CHECK_MS = 30_000

    activity_changed = Signal(str)   # "" / "busy" / "waiting"

    def __init__(self, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self._clock = clock
        self._sessions = {}   # 哪段对话 → (在做什么, 最后一次有动静的时间)
        self._activity = ""
        self._check = QTimer(self, interval=self.CHECK_MS, timeout=self._expire)

    @property
    def activity(self):
        return self._activity

    def on_event(self, event):
        key = event.session or event.cwd   # 阶段 8 的老连接没有对话编号：按工作目录分
        if event.event in BUSY_EVENTS:
            self._sessions[key] = ("busy", self._clock())
        elif event.event in WAITING_EVENTS:
            self._sessions[key] = ("waiting", self._clock())
        elif event.event in DONE_EVENTS:
            self._sessions.pop(key, None)
        else:
            return
        self._update()

    def _expire(self):
        now = self._clock()
        self._sessions = {key: (what, seen) for key, (what, seen) in self._sessions.items()
                          if now - seen < self.STALE_S[what]}
        self._update()

    def _update(self):
        doing = {what for what, _ in self._sessions.values()}
        activity = "waiting" if "waiting" in doing else "busy" if "busy" in doing else ""
        if self._sessions:
            if not self._check.isActive():
                self._check.start()
        else:
            self._check.stop()
        if activity != self._activity:
            self._activity = activity
            self.activity_changed.emit(activity)
