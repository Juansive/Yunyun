"""助手模式的确认卡片：芸芸要改文件、执行命令、碰工作文件夹以外的东西时，问你一下。

    ┌──────────────────────────────────────────┐
    │ 芸芸想执行一条命令              还有 1 个 │
    │ 芸芸说：跑一下测试                         │
    │ ┌──────────────────────────────────────┐ │   ← 命令全文 / 文件和逐行对比（change_diff）（等宽字、可以选中复制）
    │ │ pytest -q                            │ │
    │ └──────────────────────────────────────┘ │
    │ [不同意的话可以写一句理由（Enter = 拒绝）] │
    │ [拒绝]        [这次对话 pytest 都允许] [允许一次] │
    └──────────────────────────────────────────┘

- 放在阅读框那个位置（"芸芸 + 输入框 + 卡片堆"旁边），出现时不抢输入框的键盘焦点；
- 危险命令、敏感文件：标题红色带 ⚠，边框也是红的，下面一行说明为什么每次都问；不提供"都允许"；
- 拒绝时可以写一句理由，芸芸（Claude）会看到；Esc = 拒绝。没有"什么都不选就关掉"：不同意就是拒绝。
- 回答后发 answered(请求号, 允许, 理由, 这次对话都允许)。
"""
import html
import os

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from yunyun import i18n
from yunyun.pet.change_diff import change_html, proposed_change
from yunyun.pet.side_panel import SidePanel

REMEMBER_SCOPE_CHARS = 24  # "这次对话 xxx 都允许"里 xxx 最多显示多长

_ALLOW_STYLE = """
QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4FA8FF, stop:1 #7C8CFF); color: white;
              border: none; border-radius: 11px; padding: 4px 14px; font-weight: bold; }
QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #63B3FF, stop:1 #8E9BFF); }
"""
_ALLOW_DANGER_STYLE = """
QPushButton { background: #E5534B; color: white; border: none; border-radius: 11px; padding: 4px 14px;
              font-weight: bold; }
QPushButton:hover { background: #EE6A62; }
"""
_SECONDARY_STYLE = """
QPushButton { background: #EEF3FC; color: #4C6FD8; border: 1px solid #D5E0F5; border-radius: 11px; padding: 4px 12px; }
QPushButton:hover { background: #E1EBFD; }
"""
_DENY_STYLE = """
QPushButton { background: transparent; color: #6B7280; border: 1px solid #DDE3EE; border-radius: 11px;
              padding: 4px 14px; }
QPushButton:hover { background: #F4F6FA; color: #374151; }
"""
_DETAIL_STYLE = """
QTextBrowser { background: #F3F5F9; color: #1F2937; border: none; border-radius: 8px; padding: 4px; }
QScrollBar:vertical { width: 6px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #CBD3E1; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
_REASON_STYLE = """
QLineEdit { background: white; border: 1px solid #DDE3EE; border-radius: 9px; padding: 4px 8px; color: #1F2937; }
QLineEdit:focus { border: 1px solid #8FB4F5; }
"""


# ---------- 内容（纯函数，方便测） ----------

def show_path(path, workspace):
    """工作文件夹里的文件显示相对路径，外面的显示完整路径。"""
    if not path:
        return ""
    if workspace and os.path.isabs(path):
        try:
            relative = os.path.relpath(path, workspace)
        except ValueError:   # 不在同一个盘
            return path
        if not relative.startswith(".."):
            return relative
    return path


def title_key(request):
    """标题用哪句话（i18n 的 key）。"""
    if request.kind == "command":
        return "assistant_confirm_command_danger" if request.danger else "assistant_confirm_command"
    if request.kind == "read_outside":
        return "assistant_confirm_read_outside" if request.tool == "Read" else "assistant_confirm_search_outside"
    if request.kind == "edit":
        return "assistant_confirm_write" if request.tool == "Write" else "assistant_confirm_edit"
    if request.kind in ("edit_sensitive", "write_outside", "screenshot"):
        return f"assistant_confirm_{request.kind}"
    return "assistant_confirm_other"


def note_key(request):
    """标题下面的一行说明（为什么每次都问）；没有就是 None。"""
    if request.danger and request.kind == "command":
        return "assistant_confirm_note_danger"
    if request.kind == "edit_sensitive":
        return "assistant_confirm_note_sensitive"
    if request.kind == "command" and not request.can_remember:
        return "assistant_confirm_note_unclear"
    return None


MONO = 'style="font-family: Consolas, \'Microsoft YaHei\'; white-space: pre-wrap;"'


def detail_html(request, workspace):
    """详细内容（HTML）：命令全文；要读 / 搜索的位置；要改 / 写的文件 + 逐行对比（红删绿增，标题行写 +几 −几）。"""
    data = request.input or {}
    if request.kind == "command":
        return f'<div {MONO}>{html.escape(str(data.get("command", "")))}</div>'
    if request.kind == "screenshot":   # 截哪里 + 截图会发给模型（芸芸自己的窗口不截）
        target = data.get("target") if data.get("target") in ("window", "screen") else "window"
        return (f'<div><b>{html.escape(i18n.tr(f"assistant_screenshot_target_{target}"))}</b></div>'
                f'<div style="color:#6B7280;">{html.escape(i18n.tr("assistant_confirm_screenshot_note"))}</div>')
    path = show_path(str(data.get("file_path") or data.get("notebook_path") or data.get("path") or ""), workspace)
    head = f'<b>{html.escape(path)}</b>' if path else ""
    if request.tool in ("Glob", "Grep"):
        return f'<div {MONO}>{head}</div><div {MONO}>{html.escape(str(data.get("pattern", "")))}</div>'
    change = proposed_change(request.tool, data)
    if change is None:
        return f'<div {MONO}>{head}</div>' if head else ""
    body, added, removed = change_html(change)
    stats = (f'&nbsp;&nbsp;<span style="color:#2E7D32;">+{added}</span>'
             f'&nbsp;<span style="color:#C62828;">−{removed}</span>')
    return f'<div {MONO}>{head}{stats}</div><div {MONO}>{body}</div>'


def remember_text(request):
    """"这次对话都允许"按钮上的字；不提供就是空的。"""
    if not request.can_remember:
        return ""
    if request.kind == "command":
        scope = request.scope
        if len(scope) > REMEMBER_SCOPE_CHARS:
            scope = scope[:REMEMBER_SCOPE_CHARS - 1] + "…"
        return i18n.tr("assistant_confirm_remember_command", scope)
    if request.kind == "read_outside":
        return i18n.tr("assistant_confirm_remember_folder")
    if request.kind == "screenshot":
        return i18n.tr("assistant_confirm_remember_screenshot")
    return i18n.tr("assistant_confirm_remember_edit")


# ---------- 卡片 ----------

class AssistantConfirm(SidePanel):
    answered = Signal(int, bool, str, bool)   # 请求号、允许、理由、这次对话都允许

    PANEL_W = 470
    MIN_H = 170
    MAX_H = 600
    DETAIL_MAX_H = 330
    DANGER_BORDER = QColor("#F2B8B5")

    def __init__(self, owner):
        super().__init__(owner)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)   # 出现时不抢输入框的键盘焦点
        self.request = None
        self._workspace = ""

        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._more = QLabel()
        self._more.setStyleSheet("color: #8A94A6; font-size: 9pt;")
        top.addWidget(self._title, 1)
        top.addWidget(self._more, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #B4423B; font-size: 9pt;")
        layout.addWidget(self._note)
        self._said = QLabel()   # Claude 自己说的"这条命令是干什么的"（Bash 的 description）
        self._said.setWordWrap(True)
        self._said.setTextFormat(Qt.TextFormat.PlainText)
        self._said.setStyleSheet("color: #4B5563; font-size: 9pt;")
        layout.addWidget(self._said)
        self._detail = QTextBrowser()
        self._detail.setFrameShape(QFrame.Shape.NoFrame)
        self._detail.setStyleSheet(_DETAIL_STYLE)
        self._detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail.document().setDocumentMargin(6)
        layout.addWidget(self._detail, 1)
        self._reason = QLineEdit()
        self._reason.setStyleSheet(_REASON_STYLE)
        self._reason.returnPressed.connect(self._deny)
        layout.addWidget(self._reason)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._deny_button = self._button(_DENY_STYLE, self._deny)
        self._remember_button = self._button(_SECONDARY_STYLE, lambda: self._answer(True, remember=True))
        self._allow_button = self._button(_ALLOW_STYLE, lambda: self._answer(True))
        buttons.addWidget(self._deny_button)
        buttons.addStretch(1)
        buttons.addWidget(self._remember_button)
        buttons.addWidget(self._allow_button)
        layout.addLayout(buttons)
        self.retranslate()
        self.resize(self.PANEL_W, 300)

    def _button(self, style, slot):
        button = QPushButton()
        button.setStyleSheet(style)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    # ---------- 对外 ----------

    def show_request(self, request, anchor, waiting_more=0, workspace=""):
        """显示一个确认请求（已经在显示同一个就只更新"还有几个"）。anchor：放在它旁边。"""
        self._anchor = QRect(anchor)
        self._more.setText(i18n.tr("assistant_confirm_more", waiting_more) if waiting_more else "")
        if request is not self.request or not self.is_open:
            if request is not self.request:
                self._reason.clear()
            self._workspace = workspace
            self._fill(request)
        self._show_animated()

    def retranslate(self):
        self._reason.setPlaceholderText(i18n.tr("assistant_confirm_reason"))
        self._deny_button.setText(i18n.tr("assistant_confirm_deny"))
        self._deny_button.setToolTip(i18n.tr("assistant_confirm_deny_tip"))
        self._allow_button.setText(i18n.tr("assistant_confirm_allow"))
        if self.request is not None:
            self._fill(self.request)

    # ---------- 内容 ----------

    def _fill(self, request):
        self.request = request
        danger = request.danger or request.kind == "edit_sensitive"
        self.border_color = self.DANGER_BORDER if danger else QColor("#DDE3EE")
        title = i18n.tr(title_key(request), request.tool)
        self._title.setText(("⚠ " if danger else "") + title)
        self._title.setStyleSheet(f"color: {'#C62828' if danger else '#111827'}; font-weight: bold; font-size: 11pt;")
        note = note_key(request)
        self._note.setText(i18n.tr(note) if note else "")
        self._note.setVisible(bool(note))
        said = str((request.input or {}).get("description") or "").strip()
        self._said.setText(i18n.tr("assistant_confirm_said_by", said) if said else "")
        self._said.setVisible(bool(said))
        self._detail.setHtml(detail_html(request, self._workspace))
        remember = remember_text(request)
        self._remember_button.setText(remember)
        self._remember_button.setToolTip(request.scope if request.can_remember else "")
        self._remember_button.setVisible(bool(remember))
        self._allow_button.setStyleSheet(_ALLOW_DANGER_STYLE if danger else _ALLOW_STYLE)
        self._fit_height()
        self.update()

    def _fit_height(self):
        m, pad = self.MARGIN, self.PAD
        width = self.PANEL_W - 2 * (m + pad)
        self._detail.document().setTextWidth(width - 16)
        detail_h = min(int(self._detail.document().size().height()) + 14, self.DETAIL_MAX_H)
        self._detail.setFixedHeight(max(detail_h, 36))
        layout = self._content.layout()
        layout.invalidate()
        content_h = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        screen = QGuiApplication.screenAt(self._anchor.center()) or QGuiApplication.primaryScreen()
        limit = min(self.MAX_H, screen.availableGeometry().height() - 40)
        height = max(self.MIN_H, min(2 * (m + pad) + content_h, limit))
        self.resize(self.PANEL_W, height)
        self._content.setGeometry(m + pad, m + pad, width, height - 2 * (m + pad))

    # ---------- 回答 ----------

    def _deny(self):
        self._answer(False)

    def _answer(self, allow, remember=False):
        request = self.request
        if request is None or not self.is_open:
            return
        reason = "" if allow else self._reason.text().strip()
        self._reason.clear()
        self.answered.emit(request.request_id, allow, reason, remember)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:   # 不同意就是拒绝：没有"不回答就关掉"
            self._deny()
            return
        super().keyPressEvent(event)
