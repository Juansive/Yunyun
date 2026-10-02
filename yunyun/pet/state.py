"""芸芸的状态机：决定芸芸此刻处于哪种状态。

    待机 IDLE ──── 敲键盘 ────▶ 工作 WORKING
        ▲                           │
        └── 一段时间没敲键盘 ◀──────┘

    任意状态 ── 收到邮件 ──▶ 激动 EXCITED ── 几秒后 ──▶ 回到 工作 或 待机

    任意状态 ── 用户点小红点 ──▶ 思考 THINKING ── 邮件处理完 ──▶ 回到 工作 或 待机
    （思考期间来了新邮件不打断思考，只是小红点 +1）

    免打扰时段里，"休息"的样子从 待机 IDLE 换成 睡觉 SLEEPING：
    睡觉 SLEEPING ── 敲键盘 ──▶ 工作（醒来陪你打字）── 一段时间没敲 ──▶ 又睡着
    免打扰期间来了新邮件也不激动（只是小红点 +1）。

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
    SLEEPING = "sleeping"  # 睡觉：免打扰时段里的"休息"


class StateMachine(QObject):
    IDLE_AFTER_MS = 30_000   # 多久没敲键盘就进入待机（毫秒）
    EXCITED_MS = 5_000       # 激动状态持续多久（毫秒）

    state_changed = Signal(object)  # 状态变化时发出，参数是新的 PetState
    typed = Signal()                # 工作状态下，用户每按下一个键发出一次（按住不放的自动重复不算）
    released = Signal()             # 用户松开了一个键

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = PetState.IDLE
        self._quiet = False  # 是否在免打扰时段

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

    @property
    def rest_state(self):
        """休息时该是什么样子：平时待机，免打扰时睡觉。"""
        return PetState.SLEEPING if self._quiet else PetState.IDLE

    def set_quiet(self, quiet):
        """进入 / 离开免打扰时段。正在休息的话，马上换成对应的样子；其他状态等结束时再换。"""
        self._quiet = bool(quiet)
        if self._state in (PetState.IDLE, PetState.SLEEPING):
            self._set_state(self.rest_state)

    # ---------- 外部事件（槽） ----------

    def on_key(self, repeat=False):
        """用户按了一下键盘。repeat=True 表示按住不放产生的自动重复。"""
        self._idle_timer.start()  # 重新开始计时：又过 IDLE_AFTER_MS 没按键才算待机（按住不放也算在活动）
        if repeat:
            return                # 自动重复：芸芸的手保持不动
        if self._state in (PetState.IDLE, PetState.SLEEPING):
            self._set_state(PetState.WORKING)   # 睡着时打字也会醒来陪你
        if self._state is PetState.WORKING:
            self.typed.emit()
        # 激动 / 思考状态下按键：只记录"用户在活动"，不打断

    def on_key_release(self):
        self.released.emit()

    def on_mail(self):
        """收到新邮件。"""
        if self._state is PetState.THINKING or self._quiet:
            return  # 正在处理邮件不打断思考；免打扰时不激动
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
            self._set_state(self.rest_state)
        # 激动 / 思考状态下超时：先不管，等它们结束时再决定回到哪个状态

    def _on_excited_done(self):
        if self._state is PetState.EXCITED:
            self._back_to_normal()

    def _back_to_normal(self):
        """激动 / 思考结束：最近在打字就回到工作，否则休息（待机或睡觉）。"""
        typing_recently = self._idle_timer.isActive()
        self._set_state(PetState.WORKING if typing_recently else self.rest_state)

    def _set_state(self, state):
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state)
