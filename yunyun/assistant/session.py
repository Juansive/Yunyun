"""芸芸这一头的"一次助手会话"：管子进程、管每一轮问答，不管界面长什么样。

界面（输入框、气泡、卡片堆、阅读框）只听这里的信号：

    session = AssistantSession()
    session.turn_started.connect(...)    # 发出了一个问题（Turn）
    session.answer_changed.connect(...)  # 回答多了几个字（同一个 Turn；界面自己决定多久重画一次）
    session.progress_changed.connect(...)  # 进度行："正在搜索网页…… 关键词" / "芸芸在想……" / ""（不用显示）
    session.turn_finished.connect(...)   # 这一轮结束（回答完、被停止或出错）
    session.error_occurred.connect(...)  # 出错（包括没在回答时的错误，比如启动失败）
    session.permissions_changed.connect(...)   # 等你确认的操作变了（session.pending_permissions）
    session.start()                      # 先把大脑启动起来（约 2 秒），用户打字的工夫就好了
    session.ask("你好")
    session.answer_permission(request_id, allow=True)   # 回答确认请求
    session.rewind(turn.generation, turn.checkpoint)    # 撤销这一轮的改动（rewind_finished 报结果）

- 子进程没准备好时先发的问题会先存着，准备好了再交给它。
- "新对话" = 关掉旧的子进程、启动新的（大脑也就是一个新会话）。旧子进程之后发来的事件一律不理。
- 确认请求排成队（Claude 可能一次要用好几个工具），界面一次显示第一个；停止 / 新对话 / 关掉时整队作废
  （大脑那边停下时会把等着的都当成拒绝，这里不用一个个回）。
- 撤销（rewind）：把某一轮用 Edit / Write 改的文件恢复原样（之后几轮的也一起）。只能撤销**同一个子进程**里的
  （检查点在那个 Claude Code 会话里）：每启动一个子进程 generation 换一个新号，Turn 记着自己是哪个号的。
  撤销成功后，下一问发给大脑时前面附一句说明，免得 Claude 以为它的改动还在。
- 只用 Qt 和 protocol，不碰 Agent SDK（SDK 在子进程里）。
"""
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime

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
SETTINGS_ERRORS = {"no_key", "auth", "model", "budget", "reply_budget"}   # 这几种错误在设置页能改好（界面可以给个"去设置"）
UNDO_NOTE = ("[Note from Yunyun, not from the user: the user undid the file changes you made in earlier replies "
             "({files}). Those files are back to how they were before your changes; read them again before editing.]")
_generations = itertools.count(1)   # 子进程的编号：芸芸整个运行期间不重复（助手模式退出再唤出也不会撞号）


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
class Action:
    """操作记录里的一条：芸芸用了一次工具（只在内存里，不写硬盘、不进日志；不记文件内容）。"""
    time: datetime
    tool: str
    detail: str = ""              # 目标：文件、命令全文、网址、搜索词
    decision: str = "auto"        # protocol.DECISIONS 里的一个
    ok: bool | None = None        # 用完了没有出错；None = 还没用完 / 没用（被拒绝）
    tool_id: str = ""


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
    files: tuple = ()          # 这一轮用 Edit / Write 改了的文件
    checkpoint: str = ""       # 撤销这一轮要用的标记
    generation: int = 0        # 在哪个子进程里问的（撤销只能在同一个子进程里）
    undone: bool = False
    actions: list = field(default_factory=list)   # 操作记录：这一轮用过的工具（Action，按先后）

    def action(self, tool_id):
        return next((a for a in self.actions if a.tool_id and a.tool_id == tool_id), None)

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
    permissions_changed = Signal()    # pending_permissions 变了（来了新的 / 回答了 / 作废了）
    rewind_finished = Signal(str, bool, object)   # 检查点、成没成、撤销掉的那几轮（[Turn]）

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
        self._progress_before_wait = ""   # 等确认之前的进度行（答完了换回来）
        self.pending_permissions = []     # 等你确认的 protocol.PermissionRequest（先来的在前）
        self.turns = []               # 这次会话的所有问答（从旧到新）
        self.generation = 0           # 当前子进程的编号（还没启动是 0）
        self.rewinding = ""           # 正在撤销的检查点（等 Rewound）
        self._undo_note = []          # 撤销过的文件：下一问发给大脑时附一句说明

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
        self.generation = next(_generations)
        self.rewinding = ""
        self._undo_note = []
        process.start()

    def ask(self, text):
        """发出一个问题；正在回答或是空的就不发，返回 False。"""
        text = text.strip()
        if not text or self.busy:
            return False
        if self.rewinding:   # 撤销还没做完：等一下
            return False
        self._turn = Turn(text)
        self.turns.append(self._turn)
        self._tools.clear()
        self.busy_changed.emit(True)
        self.turn_started.emit(self._turn)
        self.start()
        self._turn.generation = self.generation
        if self._undo_note:   # 刚撤销过：告诉 Claude 文件已经恢复了（界面上显示的问题不带这句）
            text = UNDO_NOTE.format(files=", ".join(self._undo_note)) + "\n\n" + text
            self._undo_note = []
        if self._process.ready:
            self._process.ask(text)
            self._set_progress(i18n.tr("assistant_thinking"))
        else:
            self._pending = text
            self._set_progress(i18n.tr("assistant_connecting"))
        return True

    def can_rewind(self, generation, checkpoint):
        """能不能撤销这一轮：同一个子进程、它在运行、没在回答、没在撤销别的。"""
        return bool(checkpoint and generation == self.generation and self._process is not None
                    and self._process.ready and not self.busy and not self.rewinding)

    def rewind(self, generation, checkpoint):
        """撤销这一轮（和之后几轮）用 Edit / Write 做的改动。结果见 rewind_finished。"""
        if not self.can_rewind(generation, checkpoint):
            return False
        self.rewinding = checkpoint
        self._process.send(protocol.Rewind(checkpoint))
        return True

    def stop(self):
        """停止这一轮回答（停下后可以接着聊）。等着确认的一起作废（大脑停下时都当成拒绝）。"""
        if not self.busy:
            return
        self._drop_permissions()
        if self._pending is not None or self._process is None:   # 还没交给大脑：直接收回
            self._pending = None
            self._end_turn(stopped=True)
        else:
            self._process.interrupt()

    def answer_permission(self, request_id, allow, reason="", remember=False):
        """回答一个确认请求：允许一次（allow）/ 这次对话都允许（allow + remember）/ 拒绝（可以附一句理由）。"""
        request = next((r for r in self.pending_permissions if r.request_id == request_id), None)
        if request is None:
            return False
        self.pending_permissions.remove(request)
        if self._process is not None:
            self._process.send(protocol.PermissionReply(request_id, allow, reason.strip(), remember and allow))
        log.info("确认 %d（%s）：%s", request_id, request.kind,
                 "拒绝" if not allow else "这次对话都允许" if remember else "允许一次")
        if not self.pending_permissions:
            self._set_progress(self._progress_before_wait)
        self.permissions_changed.emit()
        return True

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
        self._abandon_rewind()
        if process is not None:
            process.finished.connect(process.deleteLater)
            process.stop()

    def _set_progress(self, text):
        if text != self._progress:
            self._progress = text
            self.progress_changed.emit(text)

    def _set_activity(self, text):
        """工具 / 文字带来的进度：等确认时先记着（进度行一直显示"等你确认"），答完再换上。"""
        if self.pending_permissions:
            self._progress_before_wait = text
        else:
            self._set_progress(text)

    def _drop_permissions(self):
        if self.pending_permissions:
            self.pending_permissions = []
            self.permissions_changed.emit()

    def _end_turn(self, stopped=False):
        turn, self._turn = self._turn, None
        turn.finished = True
        turn.stopped = turn.stopped or stopped
        self._drop_permissions()
        self._tools.clear()
        self._set_progress("")
        self.busy_changed.emit(False)
        self.turn_finished.emit(turn)

    def _on_event(self, process, event):
        if process is not self._process:
            return
        turn = self._turn
        if isinstance(event, protocol.Rewound):
            self._on_rewound(event)
        elif isinstance(event, protocol.Ready):
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
                self._set_activity("")
            self.answer_changed.emit(turn)
        elif isinstance(event, protocol.ToolStarted):
            self._tools.add(event.tool_id)
            if turn.action(event.tool_id) is None:
                turn.actions.append(Action(datetime.now(), event.name, event.detail, tool_id=event.tool_id))
            key = _TOOL_TEXT.get(event.name)
            text = i18n.tr(key) if key else i18n.tr("assistant_tool_other", event.name)
            self._set_activity(f"{text} {event.detail}".strip())
        elif isinstance(event, protocol.ToolDecision):
            action = turn.action(event.tool_id)
            if action is None:   # 没见过开始（不应该发生）：也记上
                action = Action(datetime.now(), event.tool, tool_id=event.tool_id)
                turn.actions.append(action)
            action.decision = event.decision if event.decision in protocol.DECISIONS else "auto"
            action.detail = event.detail or action.detail   # 命令记全文（开始时的线索只有开头）
        elif isinstance(event, protocol.ToolFinished):
            self._tools.discard(event.tool_id)
            action = turn.action(event.tool_id)
            if action is not None and action.decision not in ("denied", "blocked", "stopped"):
                action.ok = event.ok
            if not self._tools:
                self._set_activity(i18n.tr("assistant_thinking"))
        elif isinstance(event, protocol.TurnFinished):
            turn.cost_usd, turn.session_cost_usd = event.cost_usd, event.session_cost_usd
            turn.input_tokens, turn.output_tokens = event.input_tokens, event.output_tokens
            turn.files, turn.checkpoint = tuple(event.files), event.checkpoint
            ledger.record(event.cost_usd)   # 记账：设置页显示今天 / 本月 / 一共花了多少
            self._end_turn(stopped=event.stopped)
        elif isinstance(event, protocol.PermissionRequest):
            if not self.pending_permissions:
                self._progress_before_wait = self._progress
            self.pending_permissions.append(event)
            self._set_progress(i18n.tr("assistant_waiting_confirm"))
            self.permissions_changed.emit()
        else:
            log.info("暂不处理的事件：%s", type(event).__name__)

    def _on_rewound(self, event):
        if event.checkpoint != self.rewinding:
            return
        self.rewinding = ""
        undone = []
        if event.ok:   # 恢复到那一轮之前：那一轮和之后几轮的改动都没了
            index = next((i for i, t in enumerate(self.turns) if t.checkpoint == event.checkpoint), len(self.turns))
            undone = [t for t in self.turns[index:] if t.files and not t.undone]
            for t in undone:
                t.undone = True
                self._undo_note.extend(f for f in t.files if f not in self._undo_note)
            log.info("撤销做完了：%d 轮、%d 个文件", len(undone), sum(len(t.files) for t in undone))
        else:
            log.warning("撤销没做成：%s", event.detail)
        self.rewind_finished.emit(event.checkpoint, event.ok, undone)

    def _abandon_rewind(self):
        """子进程没了，撤销等不到结果了：当作没做成。"""
        if self.rewinding:
            checkpoint, self.rewinding = self.rewinding, ""
            self.rewind_finished.emit(checkpoint, False, [])

    def _on_process_finished(self, process):
        if process is not self._process:
            return
        self._process = None
        self._abandon_rewind()
        process.deleteLater()
        if self.busy:   # 回答到一半子进程没了（出错事件已经由 AssistantProcess 发过）
            self._pending = None
            self._end_turn()
