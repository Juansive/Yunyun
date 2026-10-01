"""芸芸的状态机：决定芸芸此刻处于哪种状态。

    待机 IDLE ──── 敲键盘 ────▶ 工作 WORKING
        ▲                           │
        └── 一段时间没敲键盘 ◀──────┘

    任意状态 ── 收到邮件 ──▶ 激动 EXCITED ── 几秒后 ──▶ 回到 工作 或 待机

状态机只负责"逻辑"，不管画面长什么样；
画面由 PetWindow 收到 state_changed / typed 信号后自己切换。
"""
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal


class PetState(Enum):
    IDLE = "idle"          # 待机：用户一段时间没敲键盘
    WORKING = "working"    # 工作：用户在敲键盘
    EXCITED = "excited"    # 激动：收到新邮件


class StateMachine(QObject):
    IDLE_AFTER_MS = 30_000   # 多久没敲键盘就进入待机（毫秒）
    EXCITED_MS = 5_000       # 激动状态持续多久（毫秒）

    state_changed = Signal(object)  # 状态变化时发出，参数是新的 PetState
    typed = Signal()                # 工作状态下，用户每敲一下键盘发出一次

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = PetState.IDLE

        # 这个定时器在"运行中"就代表：用户最近 IDLE_AFTER_MS 内敲过键盘
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(self.IDLE_AFTER_MS)
        self._idle_timer.timeout.connect(self._on_idle_timeout)

        self._excited_timer = QTimer(self)
        self._excited_timer.setSingleShot(True)
        self._excited_timer.setInterval(self.EXCITED_MS)
        self._excited_timer.timeout.connect(self._on_excited_done)

    @property
    def state(self):
        return self._state

    # ---------- 外部事件（槽） ----------

    def on_key(self):
        """用户按了一下键盘。"""
        self._idle_timer.start()  # 重新开始计时：又过 IDLE_AFTER_MS 没按键才算待机
        if self._state is PetState.IDLE:
            self._set_state(PetState.WORKING)
        if self._state is PetState.WORKING:
            self.typed.emit()
        # 激动状态下按键：只记录"用户在活动"，不打断激动

    def on_mail(self):
        """收到新邮件。"""
        self._set_state(PetState.EXCITED)
        self._excited_timer.start()  # 连续来邮件会重新计时

    # ---------- 内部 ----------

    def _on_idle_timeout(self):
        if self._state is PetState.WORKING:
            self._set_state(PetState.IDLE)
        # 激动状态下超时：先不管，等激动结束时再决定回到哪个状态

    def _on_excited_done(self):
        typing_recently = self._idle_timer.isActive()
        self._set_state(PetState.WORKING if typing_recently else PetState.IDLE)

    def _set_state(self, state):
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state)
