"""芸芸这一头的"一次助手会话"：管子进程、管每一轮问答，不管界面长什么样。

界面（输入框、气泡、卡片堆、阅读框）只听这里的信号：

    session = AssistantSession()
    session.turn_started.connect(...)    # 发出了一个问题（Turn）
    session.answer_changed.connect(...)  # 回答多了几个字（同一个 Turn；界面自己决定多久重画一次）
    session.progress_changed.connect(...)  # 进度行："正在搜索网页…… 关键词" / "芸芸在想……" / ""（不用显示）
    session.turn_finished.connect(...)   # 这一轮结束（回答完、被停止或出错）
    session.error_occurred.connect(...)  # 出错（包括没在回答时的错误，比如启动失败）
    session.start()                      # 先把大脑启动起来（约 2 秒），用户打字的工夫就好了
    session.ask("你好")

- 子进程没准备好时先发的问题会先存着，准备好了再交给它。
- "新对话" = 关掉旧的子进程、启动新的（大脑也就是一个新会话）。旧子进程之后发来的事件一律不理。
- 只用 Qt 和 protocol，不碰 Agent SDK（SDK 在子进程里）。
"""
import logging
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QObject, Signal

from yunyun import i18n
from yunyun.assistant import ledger, protocol

log = logging.getLogger(__name__)

_TOOL_TEXT = {
    "WebSearch": "assistant_tool_web_search", "WebFetch": "assistant_tool_web_fetch",
    "Read": "assistant_tool_read", "Glob": "assistant_tool_search_files", "Grep": "assistant_tool_search_files",
    "Edit": "assistant_tool_edit", "Write": "assistant_tool_write", "NotebookEdit": "assistant_tool_edit",
    "Bash": "assistant_tool_command", "PowerShell": "assistant_tool_command",
}
# 确认卡片（2.4）做好之前：要确认的操作一律拒绝，免得大脑一直等着
NO_CONFIRM_UI_REASON = "Yunyun's confirmation UI isn't ready yet, so this action can't be approved for now."
SETTINGS_ERRORS = {"no_key", "auth", "model", "budget", "reply_budget"}   # 这几种错误在设置页能改好（界面可以给个"去设置"）


def default_process(language, parent):
    """真正的助手子进程（测试时换成假的）。"""
    from yunyun.assistant.process import AssistantProcess
    return AssistantProcess(language, parent)


def error_text(error):
    """protocol.Error → 给人看的一段话（和设置页的说法一样）。"""
    kind = error.kind if error.kind in protocol.ERROR_KINDS else "other"
    text = i18n.tr(f"assistant_error_{kind}")
    if error.detail:
        text += "\n" + i18n.tr("assistant_error_detail", error.detail)
    return text


@dataclass(eq=False)
class Turn:
    """一问一答。"""
    question: str
    answer: str = ""                   # Markdown，逐字变长
    finished: bool = False
    stopped: bool = False
    error: protocol.Error | None = None
    cost_usd: float | None = None
    session_cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    def cost_text(self):
        """底部那行小字："这一轮 $… · 这次对话共 $…"（按 token 计费的服务商显示 token 数）；什么都不知道就是空的。"""
        if self.cost_usd is not None or self.session_cost_usd is not None:
            turn = self.cost_usd or 0.0
            total = self.session_cost_usd if self.session_cost_usd is not None else turn
            return i18n.tr("assistant_cost", turn, total)
        if self.input_tokens or self.output_tokens:
            return i18n.tr("assistant_cost_tokens", self.input_tokens, self.output_tokens)
        return ""


class AssistantSession(QObject):
    busy_changed = Signal(bool)       # 开始 / 结束回答
    model_changed = Signal(str)       # 大脑准备好了，用的是哪个模型
    turn_started = Signal(object)     # Turn
    answer_changed = Signal(object)   # Turn
    progress_changed = Signal(str)
    turn_finished = Signal(object)    # Turn
    error_occurred = Signal(object)   # protocol.Error

    def __init__(self, parent=None, make_process=None):
        """make_process(语言, parent) → 和 AssistantProcess 一样的对象；不传就用 default_process。"""
        super().__init__(parent)
        self._make_process = make_process or default_process
        self._process = None
        self._model = ""
        self._turn = None             # 正在进行的一轮
        self._pending = None          # 子进程还没准备好时先发的问题
        self._tools = set()           # 正在用的工具
        self._progress = ""
        self.turns = []               # 这次会话的所有问答（从旧到新）

    # ---------- 对外 ----------

    @property
    def busy(self):
        return self._turn is not None

    @property
    def current_turn(self):
        """正在进行的一轮（没在回答就是 None）。"""
        return self._turn

    @property
    def model(self):
        return self._model

    @property
    def process(self):
        return self._process

    @property
    def progress(self):
        return self._progress

    def start(self):
        """把大脑启动起来（已经在运行就什么也不做）。"""
        if self._process is not None:
            return
        # parent 不用会话自己：会话关掉后，子进程还要几百毫秒才能好好退出
        owner = self.parent() or QCoreApplication.instance()
        process = self._make_process(i18n.current_language(), owner)
        process.event.connect(lambda event, p=process: self._on_event(p, event))
        process.finished.connect(lambda p=process: self._on_process_finished(p))
        self._process = process
        process.start()

    def ask(self, text):
        """发出一个问题；正在回答或是空的就不发，返回 False。"""
        text = text.strip()
        if not text or self.busy:
            return False
        self._turn = Turn(text)
        self.turns.append(self._turn)
        self._tools.clear()
        self.busy_changed.emit(True)
        self.turn_started.emit(self._turn)
        self.start()
        if self._process.ready:
            self._process.ask(text)
            self._set_progress(i18n.tr("assistant_thinking"))
        else:
            self._pending = text
            self._set_progress(i18n.tr("assistant_connecting"))
        return True

    def stop(self):
        """停止这一轮回答（停下后可以接着聊）。"""
        if not self.busy:
            return
        if self._pending is not None or self._process is None:   # 还没交给大脑：直接收回
            self._pending = None
            self._end_turn(stopped=True)
        else:
            self._process.interrupt()

    def new_conversation(self):
        """忘掉之前的对话：换一个新的子进程。"""
        self._retire_process()
        self._pending = None
        if self.busy:
            self._end_turn(stopped=True)
        self.turns = []
        self.start()

    def close(self, wait=False):
        """关掉子进程。wait=True（芸芸退出时）：等它真的退出再返回。"""
        process = self._process
        self._retire_process()
        self._pending = None
        if self.busy:
            self._end_turn(stopped=True)
        if wait and process is not None:
            process.stop_and_wait()

    # ---------- 内部 ----------

    def _retire_process(self):
        """不再管这个子进程：请它退出，退出后自己销毁。它之后发来的事件，_on_event 认出不是当前的就不理。"""
        process, self._process = self._process, None
        if process is not None:
            process.finished.connect(process.deleteLater)
            process.stop()

    def _set_progress(self, text):
        if text != self._progress:
            self._progress = text
            self.progress_changed.emit(text)

    def _end_turn(self, stopped=False):
        turn, self._turn = self._turn, None
        turn.finished = True
        turn.stopped = turn.stopped or stopped
        self._tools.clear()
        self._set_progress("")
        self.busy_changed.emit(False)
        self.turn_finished.emit(turn)

    def _on_event(self, process, event):
        if process is not self._process:
            return
        turn = self._turn
        if isinstance(event, protocol.Ready):
            self._model = event.model
            self.model_changed.emit(event.model)
            if self._pending is not None:
                text, self._pending = self._pending, None
                process.ask(text)
                self._set_progress(i18n.tr("assistant_thinking"))
        elif isinstance(event, protocol.Error):
            if turn is not None and turn.error is None:
                turn.error = event
            self.error_occurred.emit(event)
            if turn is not None and not process.ready:   # 还没准备好就出错（没有 Key 等）：它会自己退出
                self._pending = None
                self._end_turn()
        elif turn is None:
            return   # 没在回答时来的文字 / 工具消息（停止之后迟到的）：不理
        elif isinstance(event, protocol.TextDelta):
            turn.answer += event.text
            if not self._tools:
                self._set_progress("")
            self.answer_changed.emit(turn)
        elif isinstance(event, protocol.ToolStarted):
            self._tools.add(event.tool_id)
            key = _TOOL_TEXT.get(event.name)
            text = i18n.tr(key) if key else i18n.tr("assistant_tool_other", event.name)
            self._set_progress(f"{text} {event.detail}".strip())
        elif isinstance(event, protocol.ToolFinished):
            self._tools.discard(event.tool_id)
            if not self._tools:
                self._set_progress(i18n.tr("assistant_thinking"))
        elif isinstance(event, protocol.TurnFinished):
            turn.cost_usd, turn.session_cost_usd = event.cost_usd, event.session_cost_usd
            turn.input_tokens, turn.output_tokens = event.input_tokens, event.output_tokens
            ledger.record(event.cost_usd)   # 记账：设置页显示今天 / 本月 / 一共花了多少
            self._end_turn(stopped=event.stopped)
        elif isinstance(event, protocol.PermissionRequest):
            log.info("确认请求 %d（%s）：确认卡片还没做，先拒绝", event.request_id, event.kind)
            process.send(protocol.PermissionReply(event.request_id, False, NO_CONFIRM_UI_REASON))
        else:
            log.info("暂不处理的事件：%s", type(event).__name__)

    def _on_process_finished(self, process):
        if process is not self._process:
            return
        self._process = None
        process.deleteLater()
        if self.busy:   # 回答到一半子进程没了（出错事件已经由 AssistantProcess 发过）
            self._pending = None
            self._end_turn()
