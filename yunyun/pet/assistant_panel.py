"""助手模式的对话面板：芸芸旁边的一个小窗口，和助手子进程聊天。

    ┌─ 芸芸 · 助手 ─────────────────────────┐
    │ 模型：claude-haiku-4-5         [新对话] │
    │                     ┌──────────────┐  │
    │                     │ 你的话（靠右） │  │
    │ ┌──────────────────────────┐         │
    │ │ 芸芸的回答（Markdown，靠左）│         │
    │ 正在搜索网页…… 关键词                  │  ← 进度行（查网页时首字要等十几秒，必须有）
    │ ┌─────────────────────────┐ [发送]   │
    │ └─────────────────────────┘          │
    │ 这一轮 $0.0012 · 这次对话共 $0.0034    │
    └───────────────────────────────────────┘

- 只和"事件"打交道（assistant/protocol.py），不知道背后是哪家大脑。
- 子进程由面板管：显示时启动；"新对话" = 关掉旧的、启动新的；关掉面板 = 关掉子进程（窗口也销毁）。
- 回答逐字到达，Markdown 每 RENDER_INTERVAL_MS 最多重新排一次版，回答再长也不卡。
- 窗口是芸芸的从属窗口（parent = 芸芸），和设置窗口一样；用系统标题栏，所以能拖动、调大小，大小会记住。
"""
import logging
import math

from PySide6.QtCore import QCoreApplication, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor, QTextOption
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTextBrowser, QVBoxLayout,
    QWidget,
)

from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.pet.bubble import place_block

log = logging.getLogger(__name__)

RENDER_INTERVAL_MS = 100            # 逐字显示时，Markdown 最多每 0.1 秒重新排一次版
DEFAULT_SIZE = QSize(420, 560)
MIN_SIZE = QSize(320, 360)
USER_BUBBLE_RATIO = 0.8             # 你的话最宽占面板的 80%
_SIZE_KEY = "assistant/panel_size"
_TOOL_TEXT = {"WebSearch": "assistant_tool_web_search", "WebFetch": "assistant_tool_web_fetch"}
_SETTINGS_ERRORS = {"no_key", "auth", "model", "budget"}   # 这几种错误在设置页能改好，附"去设置"按钮

_USER_STYLE = "background: #DCEBFF; color: #1A1A1A; border-radius: 10px; padding: 6px 10px;"
_REPLY_STYLE = "QTextBrowser { background: #F4F1FA; color: #1A1A1A; border-radius: 10px; }"
_ERROR_STYLE = "color: #C62828;"
_NOTE_STYLE = "color: #888888;"
_USER_PADDING = 2 * 10 + 6          # 左右 padding 各 10，再留一点余量


def default_process(language, parent):
    """真正的助手子进程。用到时才 import（虽然 process.py 本身不碰 SDK，也没必要提前载入）。"""
    from yunyun.assistant.process import AssistantProcess
    return AssistantProcess(language, parent)


def error_text(error):
    """protocol.Error → 给人看的一段话（和设置页的说法一样）。"""
    kind = error.kind if error.kind in protocol.ERROR_KINDS else "other"
    text = i18n.tr(f"assistant_error_{kind}")
    if error.detail:
        text += "\n" + i18n.tr("assistant_error_detail", error.detail)
    return text


def _wrap_code_blocks(document):
    """Markdown 的代码块默认不换行，窄窗口里右边会被截掉：改成自动换行（复制出来还是原样）。"""
    block = document.begin()
    while block.isValid():
        fmt = block.blockFormat()
        if fmt.nonBreakableLines():
            fmt.setNonBreakableLines(False)
            QTextCursor(block).setBlockFormat(fmt)
        block = block.next()


class _Reply(QTextBrowser):
    """芸芸的一条回答：按内容自动长高，自己不出滚动条（整个消息列表一起滚）。文字可以选中复制。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(_REPLY_STYLE)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)   # 长网址也能折行
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(8)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self.markdown_text = ""

    def set_markdown(self, text):
        self.markdown_text = text
        self.setMarkdown(text)
        _wrap_code_blocks(self.document())
        self._fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self, *_):
        self.setFixedHeight(math.ceil(self.document().size().height()) + 2)


class _Input(QPlainTextEdit):
    """输入框：Enter 发送，Shift+Enter 换行。"""

    submit = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class AssistantPanel(QWidget):
    busy_changed = Signal(bool)       # 芸芸开始 / 结束回答（1D 用来切换芸芸的思考状态）
    error_occurred = Signal(object)   # 收到 protocol.Error（1D 用来让芸芸说一句）
    settings_requested = Signal()     # 点了错误下面的"去设置"
    closed = Signal()                 # 面板关掉了（子进程也已经在关）

    def __init__(self, parent=None, make_process=None):
        """make_process(语言, parent) → 和 AssistantProcess 一样的对象；不传就用 default_process（测试时换成假的）。"""
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(MIN_SIZE)
        self.resize(QSettings().value(_SIZE_KEY, DEFAULT_SIZE, type=QSize))

        self._make_process = make_process or default_process
        self._process = None
        self._model = ""
        self._busy = False
        self._pending = None          # 子进程还没准备好时先发的问题，准备好了再交给它
        self._reply = None            # 这一轮芸芸的回答（还没出第一个字时是 None）
        self._reply_text = ""
        self._tools = set()           # 正在用的工具
        self._stick_to_bottom = True  # 你往上翻着看时为 False：新消息不强行跳到底部
        self._entries = []            # [(种类, 控件)]：user / reply / note / error

        self._render_timer = QTimer(self, singleShot=True, interval=RENDER_INTERVAL_MS)
        self._render_timer.timeout.connect(self._render)
        self._build()
        self._retranslate()
        if hasattr(parent, "language_changed"):   # 芸芸换语言时面板上的字跟着换（已经说过的话不变）
            parent.language_changed.connect(self._retranslate)

    # ---------- 对外 ----------

    @property
    def busy(self):
        return self._busy

    @property
    def process(self):
        return self._process

    def show_beside(self, body_rect):
        """显示在芸芸身体（屏幕坐标）旁边，并拿到焦点；已经开着就只提到前面。"""
        if not self.isVisible():
            x, y, _ = place_block(body_rect, self.width(), self.height(), gap=12)
            self.move(int(x), int(y))
            self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def transcript(self):
        """[(种类, 文字)]，种类是 user / reply / note / error。测试用，以后"复制整段对话"也能用。"""
        return [(kind, widget.markdown_text if kind == "reply" else widget.text()) for kind, widget in self._entries]

    def send(self):
        """把输入框里的话发出去（芸芸正在回答时不能发，但可以先打字）。"""
        text = self._input.toPlainText().strip()
        if not text or self._busy:
            return
        self._input.clear()
        self._stick_to_bottom = True
        self._add_user_message(text)
        self._reply, self._reply_text = None, ""
        self._tools.clear()
        self._set_busy(True)
        self._ensure_process()
        if self._process.ready:
            self._process.ask(text)
            self._set_progress(i18n.tr("assistant_thinking"))
        else:
            self._pending = text
            self._set_progress(i18n.tr("assistant_connecting"))

    def stop(self):
        """停止这一轮回答（停下后可以接着聊）。"""
        if not self._busy:
            return
        if self._pending is not None or self._process is None:   # 还没交给大脑：直接收回
            self._pending = None
            self._end_turn(stopped=True)
        else:
            self._process.interrupt()

    def new_conversation(self):
        """清空界面，换一个新的子进程（大脑也就是一个新会话）。"""
        self._retire_process()
        self._render_timer.stop()
        while self._messages.count() > 1:   # 最后一项是把消息顶到上面的弹簧
            item = self._messages.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._entries.clear()
        self._reply, self._reply_text, self._pending = None, "", None
        self._tools.clear()
        self._cost.clear()
        self._set_progress("")
        self._set_busy(False)
        self._ensure_process()

    # ---------- 界面 ----------

    def _build(self):
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self._model_label = QLabel()
        self._model_label.setStyleSheet(_NOTE_STYLE)
        self._new_button = QPushButton()
        self._new_button.clicked.connect(self.new_conversation)
        top.addWidget(self._model_label, 1)
        top.addWidget(self._new_button)
        root.addLayout(top)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self._messages = QVBoxLayout(content)
        self._messages.setContentsMargins(4, 4, 4, 4)
        self._messages.setSpacing(8)
        self._messages.addStretch(1)
        self._scroll.setWidget(content)
        bar = self._scroll.verticalScrollBar()
        bar.valueChanged.connect(self._on_scrolled)
        bar.rangeChanged.connect(self._on_range_changed)
        root.addWidget(self._scroll, 1)

        self._progress = QLabel()
        self._progress.setStyleSheet(_NOTE_STYLE)
        self._progress.hide()
        root.addWidget(self._progress)

        bottom = QHBoxLayout()
        self._input = _Input()
        self._input.setFixedHeight(72)
        self._input.submit.connect(self.send)
        self._send_button = QPushButton()
        self._send_button.clicked.connect(self._on_button)
        bottom.addWidget(self._input, 1)
        bottom.addWidget(self._send_button, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(bottom)

        self._cost = QLabel()
        self._cost.setStyleSheet(_NOTE_STYLE)
        root.addWidget(self._cost)

    def _retranslate(self):
        self.setWindowTitle(i18n.tr("assistant_panel_title"))
        self._model_label.setText(i18n.tr("assistant_panel_model", self._model) if self._model else "")
        self._new_button.setText(i18n.tr("assistant_new_chat"))
        self._input.setPlaceholderText(i18n.tr("assistant_input_placeholder"))
        self._send_button.setText(i18n.tr("assistant_stop" if self._busy else "assistant_send"))

    def _add_row(self, widget, align_right=False):
        """在消息列表末尾（弹簧前面）加一行。靠右的（你的话）左边垫一个弹簧，宽度按文字长短。"""
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        if align_right:
            row.addStretch(1)
        row.addWidget(widget)
        self._messages.insertWidget(self._messages.count() - 1, holder)
        return holder

    def _add_user_message(self, text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(_USER_STYLE)
        self._entries.append(("user", label))
        self._fit_user_label(label)
        self._add_row(label, align_right=True)

    def _add_reply(self):
        self._reply = _Reply()
        self._entries.append(("reply", self._reply))
        self._add_row(self._reply)

    def _add_note(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(_NOTE_STYLE)
        self._entries.append(("note", label))
        self._add_row(label)

    def _add_error(self, error):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(error_text(error))
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setStyleSheet(_ERROR_STYLE)
        layout.addWidget(label)
        self._entries.append(("error", label))
        if error.kind in _SETTINGS_ERRORS:
            button = QPushButton(i18n.tr("assistant_open_settings"))
            button.clicked.connect(self.settings_requested)
            layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        self._add_row(box)

    def _fit_user_label(self, label):
        """你的话：按文字长短定宽度，最宽 USER_BUBBLE_RATIO（QLabel 自己估的宽度偏窄，短短一句也会折行）。"""
        metrics = label.fontMetrics()
        natural = max(metrics.horizontalAdvance(line) for line in label.text().splitlines() or [""])
        maximum = max(120, int(self._scroll.viewport().width() * USER_BUBBLE_RATIO))
        label.setFixedWidth(min(natural + _USER_PADDING, maximum))

    def _set_progress(self, text):
        self._progress.setText(text)
        self._progress.setVisible(bool(text))

    def _set_busy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self._send_button.setText(i18n.tr("assistant_stop" if busy else "assistant_send"))
            self.busy_changed.emit(busy)

    def _on_button(self):
        if self._busy:
            self.stop()
        else:
            self.send()

    def _on_scrolled(self, value):
        bar = self._scroll.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 4

    def _on_range_changed(self, _minimum, maximum):
        if self._stick_to_bottom:
            self._scroll.verticalScrollBar().setValue(maximum)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for kind, label in self._entries:
            if kind == "user":
                self._fit_user_label(label)

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_process()   # 一打开就启动大脑（约 2 秒），你打字的工夫它就准备好了

    def closeEvent(self, event):
        QSettings().setValue(_SIZE_KEY, self.size())
        self._render_timer.stop()
        self._retire_process()
        if self._busy:
            self._set_busy(False)
        self.closed.emit()
        super().closeEvent(event)

    # ---------- 回答逐字显示 ----------

    def _schedule_render(self):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _render(self):
        self._render_timer.stop()
        if not self._reply_text:
            return
        if self._reply is None:
            self._add_reply()
        if self._reply.markdown_text != self._reply_text:
            self._reply.set_markdown(self._reply_text)

    def _end_turn(self, stopped=False):
        self._render()
        if stopped:
            self._add_note(i18n.tr("assistant_stopped"))
        self._reply, self._reply_text = None, ""
        self._tools.clear()
        self._set_progress("")
        self._set_busy(False)

    # ---------- 子进程 ----------

    def _ensure_process(self):
        if self._process is not None:
            return
        # parent 不用面板：面板关掉就销毁了，子进程还要几百毫秒才能好好退出
        owner = self.parent() or QCoreApplication.instance()
        process = self._make_process(i18n.current_language(), owner)
        process.event.connect(lambda event, p=process: self._on_event(p, event))
        process.finished.connect(lambda p=process: self._on_process_finished(p))
        self._process = process
        process.start()

    def _retire_process(self):
        """不再管这个子进程：请它退出，退出后自己销毁。它之后发来的事件，_on_event 认出不是当前的就不理。"""
        process, self._process = self._process, None
        if process is None:
            return
        process.finished.connect(process.deleteLater)
        process.stop()

    def _on_event(self, process, event):
        if process is not self._process:
            return
        if isinstance(event, protocol.Ready):
            self._model = event.model
            self._model_label.setText(i18n.tr("assistant_panel_model", event.model))
            if self._pending is not None:
                text, self._pending = self._pending, None
                process.ask(text)
                self._set_progress(i18n.tr("assistant_thinking"))
        elif not self._busy:
            if isinstance(event, protocol.Error):   # 没在回答时的错误（比如启动失败）也要说出来
                self._add_error(event)
                self.error_occurred.emit(event)
            return
        elif isinstance(event, protocol.TextDelta):
            self._reply_text += event.text
            if not self._tools:
                self._set_progress("")
            self._schedule_render()
        elif isinstance(event, protocol.ToolStarted):
            self._render()   # 先把用工具之前说的话显示完
            self._tools.add(event.tool_id)
            key = _TOOL_TEXT.get(event.name)
            text = i18n.tr(key) if key else i18n.tr("assistant_tool_other", event.name)
            self._set_progress(f"{text} {event.detail}".strip())
        elif isinstance(event, protocol.ToolFinished):
            self._tools.discard(event.tool_id)
            if not self._tools:
                self._set_progress(i18n.tr("assistant_thinking"))
        elif isinstance(event, protocol.TurnFinished):
            self._show_cost(event)
            self._end_turn(stopped=event.stopped)
        elif isinstance(event, protocol.Error):
            self._render()
            self._add_error(event)
            self.error_occurred.emit(event)
            if not process.ready:   # 还没准备好就出错（没有 Key 等）：它会自己退出，这一轮到此为止
                self._pending = None
                self._end_turn()
        else:
            log.info("对话面板暂不处理的事件：%s", type(event).__name__)   # PermissionRequest：阶段 2

    def _on_process_finished(self, process):
        if process is not self._process:
            return
        self._process = None
        process.deleteLater()
        if self._busy:   # 回答到一半子进程没了（出错事件已经由 AssistantProcess 发过）
            self._pending = None
            self._end_turn()

    def _show_cost(self, event):
        if event.cost_usd is not None or event.session_cost_usd is not None:   # 按美元计费（出错的那一轮可能没有单轮花费）
            turn = event.cost_usd or 0.0
            total = event.session_cost_usd if event.session_cost_usd is not None else turn
            self._cost.setText(i18n.tr("assistant_cost", turn, total))
        elif event.input_tokens or event.output_tokens:
            self._cost.setText(i18n.tr("assistant_cost_tokens", event.input_tokens, event.output_tokens))
        else:
            self._cost.clear()   # 什么都不知道：别留着上一轮的数
