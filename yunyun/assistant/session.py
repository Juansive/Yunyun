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
from yunyun.assistant import ledger, memory, protocol, recollections, transcripts

log = logging.getLogger(__name__)

_TOOL_TEXT = {
    "WebSearch": "assistant_tool_web_search", "WebFetch": "assistant_tool_web_fetch",
    "Read": "assistant_tool_read", "Glob": "assistant_tool_search_files", "Grep": "assistant_tool_search_files",
    "Edit": "assistant_tool_edit", "Write": "assistant_tool_write", "NotebookEdit": "assistant_tool_edit",
    "Bash": "assistant_tool_command", "PowerShell": "assistant_tool_command",
    memory.REMEMBER_TOOL: "assistant_tool_remember", memory.RECALL_TOOL: "assistant_tool_recall",
    memory.FORGET_TOOL: "assistant_tool_forget", protocol.SCREENSHOT_TOOL: "assistant_tool_screenshot",
    protocol.FIND_APP_TOOL: "assistant_tool_find_app", protocol.OPEN_APP_TOOL: "assistant_tool_open_app",
    protocol.BROWSER_OPEN_TOOL: "assistant_tool_browser_open", protocol.BROWSER_READ_TOOL: "assistant_tool_browser_read",
    protocol.BROWSER_BACK_TOOL: "assistant_tool_browser_back", protocol.BROWSER_CLICK_TOOL: "assistant_tool_browser_click",
    protocol.BROWSER_TYPE_TOOL: "assistant_tool_browser_type", protocol.BROWSER_SELECT_TOOL: "assistant_tool_browser_select",
    protocol.BROWSER_DOWNLOAD_TOOL: "assistant_tool_browser_download",
    recollections.RECALL_CHATS_TOOL: "assistant_tool_recall_chats",
    protocol.SET_REMINDER_TOOL: "assistant_tool_set_reminder", protocol.LIST_REMINDERS_TOOL: "assistant_tool_reminders",
    protocol.CANCEL_REMINDER_TOOL: "assistant_tool_reminders",
    protocol.COPY_TO_CLIPBOARD_TOOL: "assistant_tool_copy", protocol.READ_CLIPBOARD_TOOL: "assistant_tool_read_clipboard",
    protocol.LIST_WINDOWS_TOOL: "assistant_tool_windows", protocol.WINDOW_ACTION_TOOL: "assistant_tool_windows",
}
SETTINGS_ERRORS = {"no_key", "auth", "model", "budget", "reply_budget"}   # 这几种错误在设置页能改好（界面可以给个"去设置"）
UNDO_NOTE = transcripts.UNDO_NOTE   # 历史对话读回来时要认出它、去掉它，所以放在 transcripts 里
_generations = itertools.count(1)   # 子进程的编号：芸芸整个运行期间不重复（助手模式退出再唤出也不会撞号）


def default_process(language, parent, resume=""):
    """真正的助手子进程（测试时换成假的）。resume：接着哪一段以前的对话聊（Claude Code 的会话编号）。"""
    from yunyun.assistant.process import AssistantProcess
    return AssistantProcess(language, parent, resume=resume)


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
    session_id: str = ""       # Claude Code 的会话编号（历史对话"接着聊"用）
    context_tokens: int = 0    # 答完这一轮时对话有多长（token）
    context_limit: int = 0     # 超过多少自动整理（0 = 不整理）
    compact: str = ""          # 这一轮答完后整理前面的对话："" 没整理、"running" 正在整理、"done" 整理好了、"failed" 没做成
    remembered: int = 0        # 这一轮往记事本里记了几条
    images: int = 0            # 这一问带了几张图片（输入框里粘贴的）

    def action(self, tool_id):
        return next((a for a in self.actions if a.tool_id and a.tool_id == tool_id), None)


class AssistantSession(QObject):
    busy_changed = Signal(bool)       # 开始 / 结束回答
    model_changed = Signal(str)       # 大脑准备好了，用的是哪个模型
    turn_started = Signal(object)     # Turn
    answer_changed = Signal(object)   # Turn
    progress_changed = Signal(str)
    turn_finished = Signal(object)    # Turn
    error_occurred = Signal(object)   # protocol.Error
    permissions_changed = Signal()    # pending_permissions 变了（来了新的 / 回答了 / 作废了）
    activity_changed = Signal(str)    # 助手在做什么变了（见 activity；芸芸的样子跟着变）
    rewind_finished = Signal(str, bool, object)   # 检查点、成没成、撤销掉的那几轮（[Turn]）
    compact_changed = Signal(object)  # 某一轮答完后的"整理前面的对话"开始了 / 做完了（Turn.compact）
    screenshot_requested = Signal(object)   # 大脑要看屏幕（protocol.ScreenshotRequest）：界面截好后调 answer
    open_app_requested = Signal(object)     # 大脑要打开软件（protocol.OpenAppRequest，你已同意）：打开后调 answer
    window_requested = Signal(object)       # 大脑要列出 / 切换窗口（protocol.WindowRequest，11.5）：做完调 answer
    downloaded = Signal(object)             # 芸芸的浏览器下载好了一个文件（protocol.Downloaded）
    rapport_learned = Signal(int)           # 回想以前的对话后，有几条相处笔记开始生效（11.3）：冒个气泡
    reminders_changed = Signal()            # 助手加了 / 取消了提醒（11.5）：芸芸重新排下一次响的时间

    def __init__(self, parent=None, make_process=None):
        """make_process(语言, parent, resume=会话编号) → 和 AssistantProcess 一样的对象；不传就用 default_process。"""
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
        self.session_id = ""          # 当前这段对话的 Claude Code 会话编号（闲置释放后再唤出时接着它聊）
        self.rewinding = ""           # 正在撤销的检查点（等 Rewound）
        self._undo_note = []          # 撤销过的文件：下一问发给大脑时附一句说明
        self._activity = ""           # 上次告诉外面的 activity
        self._compact_turn = None     # 正在整理：是哪一轮答完后开始的（Turn）；None = 没在整理
        self.browser_open = False     # 芸芸的浏览器开着（阶段 6.2；开着时总管不闲置释放大脑）

    # ---------- 对外 ----------

    @property
    def busy(self):
        return self._turn is not None

    @property
    def compacting(self):
        """大脑正在整理前面的对话（这时可以照常提问：子进程等整理完再问；不能撤销、不能释放大脑）。"""
        return self._compact_turn is not None

    @property
    def activity(self):
        """助手在做什么："" 没在回答、"waiting" 等你确认、"busy" 在用工具（读文件、跑命令、读网页）、"thinking" 在想 / 写回答。"""
        if not self.busy:
            return ""
        if self.pending_permissions:
            return "waiting"
        return "busy" if self._tools else "thinking"

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

    def start(self, resume=""):
        """把大脑启动起来（已经在运行就什么也不做）。resume：接着哪一段以前的对话聊（Claude Code 的会话编号）。"""
        if self._process is not None:
            return
        # parent 不用会话自己：会话关掉后，子进程还要几百毫秒才能好好退出
        owner = self.parent() or QCoreApplication.instance()
        process = self._make_process(i18n.current_language(), owner, resume=resume)
        self.session_id = resume   # 当前这段对话的会话编号：接着聊就是那段的；新对话等第一问答完才知道
        process.event.connect(lambda event, p=process: self._on_event(p, event))
        process.finished.connect(lambda p=process: self._on_process_finished(p))
        self._process = process
        self.generation = next(_generations)
        self.rewinding = ""
        self._undo_note = []
        process.start()

    def ask(self, text, images=()):
        """发出一个问题；正在回答或是空的就不发，返回 False。
        images：一起发的图片（{"data": base64, "mime": …}，输入框里粘贴的）；只有图片没有字时，问"看看这张图"。"""
        text = text.strip()
        images = tuple(images)
        if images and not text:
            text = i18n.tr("assistant_image_question")
        if not text or self.busy:
            return False
        if self.rewinding:   # 撤销还没做完：等一下
            return False
        self._turn = Turn(text, images=len(images))
        self.turns.append(self._turn)
        self._tools.clear()
        self.busy_changed.emit(True)
        self._update_activity()
        self.turn_started.emit(self._turn)
        self.start()
        self._turn.generation = self.generation
        if self._undo_note:   # 刚撤销过：告诉 Claude 文件已经恢复了（界面上显示的问题不带这句）
            text = UNDO_NOTE.format(files=", ".join(self._undo_note)) + "\n\n" + text
            self._undo_note = []
        if self._process.ready:
            self._process.ask(text, images)
            self._set_progress(i18n.tr("assistant_compacting" if self.compacting else "assistant_thinking"))
        else:
            self._pending = (text, images)
            self._set_progress(i18n.tr("assistant_connecting"))
        return True

    def can_rewind(self, generation, checkpoint):
        """能不能撤销这一轮：同一个子进程、它在运行、没在回答、没在撤销别的。"""
        return bool(checkpoint and generation == self.generation and self._process is not None
                    and self._process.ready and not self.busy and not self.rewinding and not self.compacting)

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
        self._update_activity()
        self.permissions_changed.emit()
        return True

    def answer(self, reply):
        """芸芸进程替大脑做完的事（截好的图 ScreenshotReply、打开软件的结果 OpenAppReply）交回去。子进程换了 / 没了就算了。"""
        if self._process is not None:
            self._process.send(reply)

    def new_conversation(self, resume=""):
        """换一个新的子进程：忘掉现在的对话（resume 为空），或者接着以前的某一段聊（resume = 那段的会话编号）。"""
        self._retire_process()
        self._pending = None
        if self.busy:
            self._end_turn(stopped=True)
        self.turns = []
        self.start(resume)

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
        process = self._drop_process()
        if process is not None:
            process.finished.connect(process.deleteLater)
            process.stop()

    def _drop_process(self):
        """不再用这个子进程了：浏览器跟着它关了，等着的撤销、整理都当没做成。返回原来的子进程。"""
        process, self._process = self._process, None
        self.browser_open = False
        self._abandon_rewind()
        self._abandon_compact()
        return process

    def _set_progress(self, text):
        if text != self._progress:
            self._progress = text
            self.progress_changed.emit(text)

    def _update_activity(self):
        activity = self.activity
        if activity != self._activity:
            self._activity = activity
            self.activity_changed.emit(activity)

    def _set_activity(self, text):
        """工具 / 文字带来的进度：等确认时先记着（进度行一直显示"等你确认"），答完再换上。"""
        if self.pending_permissions:
            self._progress_before_wait = text
        else:
            self._set_progress(text)

    def _drop_permissions(self):
        if self.pending_permissions:
            self.pending_permissions = []
            self._update_activity()
            self.permissions_changed.emit()

    def _end_turn(self, stopped=False):
        turn, self._turn = self._turn, None
        turn.finished = True
        turn.stopped = turn.stopped or stopped
        self._drop_permissions()
        self._tools.clear()
        self._set_progress("")
        self._update_activity()
        self.busy_changed.emit(False)
        self.turn_finished.emit(turn)

    def _on_event(self, process, event):
        if process is not self._process:
            return
        self._handle_event(process, event)
        self._update_activity()   # 用起 / 用完工具、来了确认请求……芸芸的样子跟着变

    def _handle_event(self, process, event):
        turn = self._turn
        if isinstance(event, protocol.Rewound):
            self._on_rewound(event)
        elif isinstance(event, (protocol.Compacting, protocol.Compacted)):
            self._on_compact(event)
        elif isinstance(event, (protocol.ScreenshotRequest, protocol.OpenAppRequest, protocol.WindowRequest)):
            reply, signal = {protocol.ScreenshotRequest: (protocol.ScreenshotReply, self.screenshot_requested),
                             protocol.OpenAppRequest: (protocol.OpenAppReply, self.open_app_requested),
                             protocol.WindowRequest: (protocol.WindowReply, self.window_requested)}[type(event)]
            if turn is None:   # 停下之后迟到的：不做了
                process.send(reply(event.request_id, False, detail="The user stopped the task."))
            else:
                signal.emit(event)
        elif isinstance(event, protocol.BrowserChanged):
            self.browser_open = event.open
        elif isinstance(event, protocol.Downloaded):
            self.downloaded.emit(event)
        elif isinstance(event, protocol.RemindersChanged):   # 助手加了 / 取消了提醒（11.5）：芸芸重新排时间
            self.reminders_changed.emit()
        elif isinstance(event, protocol.Reflected):   # 后台回想完一段以前的对话（11.3）：记账；有新的相处笔记生效就告诉界面
            ledger.record(event.cost_usd)
            if event.activated:
                self.rapport_learned.emit(event.activated)
        elif isinstance(event, protocol.Ready):
            self._model = event.model
            self.model_changed.emit(event.model)
            if self._pending is not None:
                (text, images), self._pending = self._pending, None
                process.ask(text, images)
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
            turn.files, turn.checkpoint, turn.session_id = tuple(event.files), event.checkpoint, event.session_id
            turn.context_tokens, turn.context_limit = event.context_tokens, event.context_limit
            turn.remembered = event.remembered
            self.session_id = event.session_id or self.session_id
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

    def _on_compact(self, event):
        """整理前面的对话：开始时记在刚答完的那一轮上；做完了记账、报结果。整理时问的那一问，进度行跟着换。"""
        if isinstance(event, protocol.Compacting):
            target = next((t for t in reversed(self.turns) if t.finished), None)
            if target is None:
                return
            self._compact_turn = target
            target.compact = "running"
            self.compact_changed.emit(target)
            return
        target, self._compact_turn = self._compact_turn, None
        ledger.record(event.cost_usd)   # 整理也花钱：一样记账
        log.info("整理前面的对话%s：约 %d → %d token", "好了" if event.ok else "没做成", event.before, event.after)
        if self.busy and self._progress == i18n.tr("assistant_compacting"):
            self._set_progress(i18n.tr("assistant_thinking"))
        if target is not None:
            target.compact = "done" if event.ok else "failed"
            self.compact_changed.emit(target)

    def _abandon_compact(self):
        """子进程没了，整理等不到结果了：当作没做成。"""
        target, self._compact_turn = self._compact_turn, None
        if target is not None:
            target.compact = "failed"
            self.compact_changed.emit(target)

    def _on_process_finished(self, process):
        if process is not self._process:
            return
        self._drop_process()
        process.deleteLater()
        if self.busy:   # 回答到一半子进程没了（出错事件已经由 AssistantProcess 发过）
            self._pending = None
            self._end_turn()
