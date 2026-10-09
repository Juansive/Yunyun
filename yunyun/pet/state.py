"""芸芸的状态机：决定芸芸此刻处于哪种状态。

    待机 IDLE ──── 敲键盘 ────▶ 工作 WORKING
        ▲                           │
        └── 一段时间没敲键盘 ◀──────┘

    任意状态 ── 收到邮件 ──▶ 激动 EXCITED ── 几秒后 ──▶ 回到 工作 或 待机

    任意状态 ── 用户点小红点 / 助手在回答 ──▶ 思考 THINKING ── 都结束了 ──▶ 回到 工作 或 待机
    （思考按原因分开算："mail" 处理邮件、"assistant" 助手在回答；两件事都结束才不想了。
      思考期间来了新邮件不打断思考，只是小红点 +1）

    助手模式（set_assistant，2026-10-08 用户定的优先级）：
        等你确认 WAITING > 在干活 BUSY > 思考 THINKING（助手在想 / 写回答，或者在处理邮件）
        > 说话 TALKING > 激动 EXCITED > 工作 WORKING > 待机 IDLE / 睡觉 SLEEPING
    助手在忙（前三种）时来了新邮件不激动，只是小红点 +1；你打字也不打断；免打扰时段照常显示（是你自己在用）。

    说话（set_talking，2026-10-09 用户定的）：头顶有气泡时，待机 / 睡觉 / 打字 / 激动都换成说话的样子
    （睡着时说话会醒来，说完再睡）；助手在想 / 干活 / 等确认时不变。说话时来了新邮件不激动、打字也不打断。

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
    THINKING = "thinking"  # 思考：正在用 AI 处理邮件 / 助手在想
    SLEEPING = "sleeping"  # 睡觉：免打扰时段里的"休息"
    BUSY = "busy"          # 在干活：助手在用工具（读文件、跑命令、读网页）
    WAITING = "waiting"    # 等你确认：助手有确认卡片在等你
    TALKING = "talking"    # 说话：头顶有气泡


_ASSISTANT_STATES = {"thinking": PetState.THINKING, "busy": PetState.BUSY, "waiting": PetState.WAITING}
_FOREGROUND = (PetState.THINKING, PetState.BUSY, PetState.WAITING)   # 占着芸芸的样子、结束了才回到平时
_TEMPORARY = (*_FOREGROUND, PetState.TALKING)   # 结束时要回到平时的样子（工作 / 待机 / 睡觉）


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
        self._thinking = set()  # 芸芸在想哪些事（"mail" / "assistant"）
        self._assistant = ""    # 助手在做什么（"" / "thinking" / "busy" / "waiting"，见 set_assistant）
        self._talking = False   # 头顶有没有气泡（见 set_talking）

        # 这个定时器在"运行中"就代表：用户最近 IDLE_AFTER_MS 内敲过键盘
        self._idle_timer = QTimer(self, singleShot=True, interval=self.IDLE_AFTER_MS, timeout=self._on_idle_timeout)

        self._excited_timer = QTimer(self, singleShot=True, interval=self.EXCITED_MS, timeout=self._on_excited_done)

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
        if self._foreground() is not None or self._talking or self._quiet:
            return  # 正在想 / 助手在忙 / 在说话不打断（小红点 +1 就够了）；免打扰时不激动
        self._set_state(PetState.EXCITED)
        self._excited_timer.start()  # 连续来邮件会重新计时

    @property
    def thinking_about(self):
        """芸芸正在想的事（测试和调试用）。"""
        return frozenset(self._thinking)

    def start_thinking(self, reason="mail"):
        """开始想一件事："mail" 用 AI 处理邮件，"assistant" 助手在回答。"""
        self._thinking.add(reason)
        self._refresh()

    def stop_thinking(self, reason="mail"):
        """这件事想完了。还有别的事在想就继续思考（比如邮件处理完了，助手还在回答）。"""
        self._thinking.discard(reason)
        self._refresh()

    def set_assistant(self, activity):
        """助手在做什么："" 没事、"thinking" 在想 / 写回答、"busy" 在用工具、"waiting" 等你确认（session.activity）。"""
        self._assistant = activity if activity in _ASSISTANT_STATES else ""
        self._refresh()

    def set_talking(self, talking):
        """头顶有没有气泡（BubbleStack.speaking_changed）。"""
        self._talking = bool(talking)
        self._refresh()

    def _foreground(self):
        """助手和邮件处理占着的样子（等你确认 > 在干活 > 思考）；都没有就是 None。"""
        if self._assistant in ("waiting", "busy"):
            return _ASSISTANT_STATES[self._assistant]
        if self._thinking or self._assistant == "thinking":
            return PetState.THINKING
        return None

    def _refresh(self):
        foreground = self._foreground() or (PetState.TALKING if self._talking else None)
        if foreground is not None:
            self._excited_timer.stop()   # 如果正在激动，直接换过去
            self._set_state(foreground)
        elif self._state in _TEMPORARY:
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
        """激动 / 思考 / 说话结束：最近在打字就回到工作，否则休息（待机或睡觉）。"""
        typing_recently = self._idle_timer.isActive()
        self._set_state(PetState.WORKING if typing_recently else self.rest_state)

    def _set_state(self, state):
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state)
