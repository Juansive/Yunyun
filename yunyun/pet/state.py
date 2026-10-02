"""芸芸的状态机：决定芸芸此刻处于哪种状态。

    待机 IDLE ──── 敲键盘 ────▶ 工作 WORKING
        ▲                           │
        └── 一段时间没敲键盘 ◀──────┘

    任意状态 ── 收到邮件 ──▶ 激动 EXCITED ── 几秒后 ──▶ 回到 工作 或 待机

    任意状态 ── 用户点小红点 ──▶ 思考 THINKING ── 邮件处理完 ──▶ 回到 工作 或 待机
    （思考期间来了新邮件不打断思考，只是小红点 +1）

状态机只负责"逻辑"，不管画面长什么样；
画面由 PetWindow 收到 state_changed / typed 信号后自己切换。
"""
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal


class PetState(Enum):
    IDLE = "idle"          # 待机：用户一段时间没敲键盘
    WORKING = "working"    # 工作：用户在敲键盘
    EXCITED = "excited"    # 激动：收到新邮件
    THINKING = "thinking"  # 思考：正在用 AI 处理邮件


class StateMachine(QObject):
    IDLE_AFTER_MS = 30_000   # 多久没敲键盘就进入待机（毫秒）
    EXCITED_MS = 5_000       # 激动状态持续多久（毫秒）

    state_changed = Signal(object)  # 状态变化时发出，参数是新的 PetState
    typed = Signal()                # 工作状态下，用户每按下一个键发出一次（按住不放的自动重复不算）
    released = Signal()             # 用户松开了一个键

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

    def on_key(self, repeat=False):
        """用户按了一下键盘。repeat=True 表示按住不放产生的自动重复。"""
        self._idle_timer.start()  # 重新开始计时：又过 IDLE_AFTER_MS 没按键才算待机（按住不放也算在活动）
        if repeat:
            return                # 自动重复：芸芸的手保持不动
        if self._state is PetState.IDLE:
            self._set_state(PetState.WORKING)
        if self._state is PetState.WORKING:
            self.typed.emit()
        # 激动 / 思考状态下按键：只记录"用户在活动"，不打断

    def on_key_release(self):
        self.released.emit()

    def on_mail(self):
        """收到新邮件。"""
        if self._state is PetState.THINKING:
            return  # 正在处理邮件：不打断思考
        self._set_state(PetState.EXCITED)
        self._excited_timer.start()  # 连续来邮件会重新计时

    def start_thinking(self):
        """开始用 AI 处理邮件。"""
        self._excited_timer.stop()   # 如果正在激动，直接转入思考
        self._set_state(PetState.THINKING)

    def stop_thinking(self):
        """邮件处理完了。"""
        if self._state is PetState.THINKING:
            self._back_to_normal()

    # ---------- 内部 ----------

    def _on_idle_timeout(self):
        if self._state is PetState.WORKING:
            self._set_state(PetState.IDLE)
        # 激动 / 思考状态下超时：先不管，等它们结束时再决定回到哪个状态

    def _on_excited_done(self):
        if self._state is PetState.EXCITED:
            self._back_to_normal()

    def _back_to_normal(self):
        """激动 / 思考结束：最近在打字就回到工作，否则待机。"""
        typing_recently = self._idle_timer.isActive()
        self._set_state(PetState.WORKING if typing_recently else PetState.IDLE)

    def _set_state(self, state):
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state)
