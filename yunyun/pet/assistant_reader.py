"""助手模式的阅读框：单击卡片后，从卡片堆旁边滑出来，显示这一问一答的全文。

    ┌──────────────────────────────────┐
    │ 你：Python 里怎么把列表倒过来？ × │
    │ 14:20 · $0.0123                  │
    │ ▸ 芸芸做了 3 件事                 │   ← 操作记录（用过工具才有），点一下展开 / 收起
    │──────────────────────────────────│
    │ 有两种常用写法：                   │
    │ ┌────────────────────── [复制] ┐ │   ← 代码块灰底，右上角浮着"复制"
    │ │ nums[::-1]                   │ │
    │ └──────────────────────────────┘ │
    │ ↶ 撤销这次改动   🔊 读一遍  复制回答 │   ← 这一问改了文件才有"撤销"；"读一遍"：芸芸从头读这段回答
    └──────────────────────────────────┘

- 放在"芸芸 + 输入框 + 卡片堆"旁边（哪边空间大放哪边），从卡片堆那边滑出、淡入；高度跟着内容，短回答不会空一大块。
  （滑出、定位、阴影、焦点去了别的程序就 dismissed：公共部分在 side_panel.SidePanel）
- 换一张卡片：内容先淡出、换好再淡入（高度在看不见的那一刻变）。
- Esc / × → closed_by_user（只关阅读框）；焦点去了别的程序 → dismissed（助手模式整个收起）。
- 撤销（这一问改了文件才显示）：点一下变成"确定撤销？再点一次"，3 秒内再点才发 undo_requested；
  能不能撤销问总管（undo_state_for(卡片) → "hidden" / "ready" / "busy" / "working" / "expired" / "done"），
  状态可能变了（开始 / 结束回答、撤销完）时总管调 refresh_undo()。
"""
import html

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel

from shiboken6 import isValid

from yunyun import i18n
from yunyun.assistant import memory, protocol, recollections
from yunyun.pet.assistant_deck import context_status
from yunyun.pet.bubble import animate
from yunyun.pet.side_panel import (
    BUTTON_STYLE, SCROLLBAR_STYLE, TEXT_STYLE, SidePanel, divider, make_button, text_view,
)

CODE_BACKGROUND = QColor("#F3F5F9")


def code_groups(document):
    """文档里的代码块：[[block, ...], ...]（连着的几行算一块）。Markdown 导入后代码块的行是"不自动换行"的。"""
    groups, current = [], []
    block = document.begin()
    while block.isValid():
        if block.blockFormat().nonBreakableLines() or block.blockFormat().property(_CODE_MARK):
            current.append(block)
        elif current:
            groups.append(current)
            current = []
        block = block.next()
    if current:
        groups.append(current)
    return groups


_CODE_MARK = 0x100100   # 自定义的块属性：标记"这是代码块"（改成自动换行后 nonBreakableLines 就没了）


def style_code_blocks(document):
    """代码块：灰底、改成自动换行（窄的框里右边不会被截掉；复制出来还是原样），并做个标记方便以后找到。"""
    for group in code_groups(document):
        for block in group:
            fmt = block.blockFormat()
            fmt.setNonBreakableLines(False)
            fmt.setBackground(CODE_BACKGROUND)
            fmt.setProperty(_CODE_MARK, True)
            fmt.setLeftMargin(4)
            fmt.setRightMargin(4)
            QTextCursor(block).setBlockFormat(fmt)


_ACTION_VERBS = {
    "Read": "assistant_action_read", "Glob": "assistant_action_search", "Grep": "assistant_action_search",
    "Edit": "assistant_action_edit", "NotebookEdit": "assistant_action_edit", "Write": "assistant_action_write",
    "Bash": "assistant_action_command", "PowerShell": "assistant_action_command",
    "WebSearch": "assistant_action_web_search", "web_search": "assistant_action_web_search",
    "WebFetch": "assistant_action_web_fetch",
    memory.REMEMBER_TOOL: "assistant_action_remember", memory.RECALL_TOOL: "assistant_action_recall",
    memory.FORGET_TOOL: "assistant_action_forget", protocol.SCREENSHOT_TOOL: "assistant_action_screenshot",
    protocol.FIND_APP_TOOL: "assistant_action_find_app", protocol.OPEN_APP_TOOL: "assistant_action_open_app",
    protocol.BROWSER_OPEN_TOOL: "assistant_action_browser_open", protocol.BROWSER_READ_TOOL: "assistant_action_browser_read",
    protocol.BROWSER_BACK_TOOL: "assistant_action_browser_back",
    protocol.BROWSER_CLICK_TOOL: "assistant_action_browser_click", protocol.BROWSER_TYPE_TOOL: "assistant_action_browser_type",
    protocol.BROWSER_SELECT_TOOL: "assistant_action_browser_select",
    protocol.BROWSER_DOWNLOAD_TOOL: "assistant_action_browser_download",
    recollections.RECALL_CHATS_TOOL: "assistant_action_recall_chats",
    protocol.SET_REMINDER_TOOL: "assistant_action_set_reminder", protocol.LIST_REMINDERS_TOOL: "assistant_action_reminders",
    protocol.CANCEL_REMINDER_TOOL: "assistant_action_cancel_reminder",
    protocol.COPY_TO_CLIPBOARD_TOOL: "assistant_action_copy", protocol.READ_CLIPBOARD_TOOL: "assistant_action_read_clipboard",
    protocol.LIST_WINDOWS_TOOL: "assistant_action_list_windows", protocol.WINDOW_ACTION_TOOL: "assistant_action_window",
    protocol.UI_READ_TOOL: "assistant_action_ui_read", protocol.UI_CLICK_TOOL: "assistant_action_ui_click",
    protocol.UI_TYPE_TOOL: "assistant_action_ui_type", protocol.UI_KEY_TOOL: "assistant_action_ui_key",
    protocol.UI_CLICK_AT_TOOL: "assistant_action_ui_click_at",
    protocol.POWER_TOOL: "assistant_action_power", protocol.LIST_PROCESSES_TOOL: "assistant_action_list_processes",
    protocol.END_PROCESS_TOOL: "assistant_action_end_process",
}
_DECISION_COLORS = {"auto": "#8A94A6", "granted": "#2E7D32", "allowed": "#2E7D32", "remembered": "#2E7D32",
                    "denied": "#C62828", "blocked": "#C62828", "stopped": "#B7791F"}
ACTION_DETAIL_CHARS = 56   # 目标太长就中间省略


def _short(text, limit=ACTION_DETAIL_CHARS):
    text = " ".join(str(text).split())   # 命令里的换行压成一行
    return text if len(text) <= limit else text[:limit - 16] + "…" + text[-15:]


def actions_html(actions):
    """操作记录 → 一张小表：时间、做了什么、目标、怎么定的（没成功的标出来）。"""
    rows = []
    for action in actions:
        key = _ACTION_VERBS.get(action.tool)
        verb = i18n.tr(key) if key else action.tool
        detail = action.detail
        if action.tool == protocol.SCREENSHOT_TOOL and detail in protocol.SCREENSHOT_TARGETS:
            detail = i18n.tr(f"assistant_screenshot_target_{detail}")   # window / screen → 给人看的说法
        decision = i18n.tr(f"assistant_decision_{action.decision}")
        if action.ok is False:
            decision += i18n.tr("assistant_action_failed")
        color = _DECISION_COLORS.get(action.decision, "#8A94A6")
        rows.append(
            f'<tr><td style="color:#9AA3B2;">{action.time:%H:%M}</td><td>{_escape(verb)}</td>'
            f'<td style="font-family:Consolas,\'Microsoft YaHei\';">{_escape(_short(detail))}</td>'
            f'<td style="color:{color};">{_escape(decision)}</td></tr>')
    return f'<table cellspacing="0" cellpadding="2">{"".join(rows)}</table>'


def set_clipboard(text):
    """放进剪贴板（测试时换掉，免得动到用户真实的剪贴板）。"""
    QApplication.clipboard().setText(text)


def group_text(group):
    return "\n".join(block.text() for block in group).replace(" ", "\n")


_ACTIONS_TOGGLE_STYLE = """
QPushButton { background: transparent; color: #4C6FD8; border: none; padding: 0; font-size: 9pt; text-align: left; }
QPushButton:hover { color: #2F55C9; text-decoration: underline; }
"""
_ACTIONS_STYLE = ("QTextBrowser { background: #F7F8FB; color: #374151; border: none; border-radius: 6px; "
                  "font-size: 9pt; }" + SCROLLBAR_STYLE)
_UNDO_STYLE = """
QPushButton { background: #FFF6E8; color: #B7791F; border: 1px solid #F3DDB8; border-radius: 9px;
              padding: 1px 9px; font-size: 9pt; }
QPushButton:hover { background: #FDEBD0; }
QPushButton:disabled { background: #F4F6FA; color: #9AA3B2; border: 1px solid #E6EAF1; }
"""


class AssistantReader(SidePanel):
    undo_requested = Signal(object)   # 卡片：要撤销这一问的改动（已经点了两次确认）
    read_requested = Signal(object)   # 卡片：要芸芸把这段回答读一遍（2026-10-13 用户要的）
    withdraw_requested = Signal(object)   # 卡片："这句不是跟你说的"，撤回这一问（7.9 ⑤）

    READER_W = 470
    MIN_H = 200
    MAX_H = 640
    ACTIONS_MAX_H = 200  # 操作记录最多占多高（再多就在框里滚动）
    SWAP_MS = 110        # 换卡片时淡出 / 淡入各多久
    UNDO_ARM_MS = 3000   # 点了一次"撤销"后，多久内再点才算数

    def __init__(self, owner):
        super().__init__(owner)
        self.card = None
        self._pending = None          # 换卡片：淡出后要换上的那张
        self._copy_buttons = []

        layout = self._make_content(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._question = QLabel()
        self._question.setWordWrap(True)
        self._question.setTextFormat(Qt.TextFormat.PlainText)
        self._question.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._question.setStyleSheet("color: #111827; font-weight: bold;")
        self._close = self._close_button()
        top.addWidget(self._question, 1)
        top.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self._meta = QLabel()
        self._meta.setStyleSheet("color: #8A94A6; font-size: 9pt;")
        layout.addWidget(self._meta)
        # "▸ 芸芸做了 3 件事"：点一下展开操作记录
        self._actions_toggle = make_button(_ACTIONS_TOGGLE_STYLE, self._toggle_actions)
        layout.addWidget(self._actions_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._actions = text_view(_ACTIONS_STYLE, margin=6)   # 高度按排版出来的算（_fit_height），太多就在框里滚动
        layout.addWidget(self._actions)
        self._actions_open = False   # 展开着：换卡片也保持展开
        layout.addWidget(divider())
        self._body = text_view(TEXT_STYLE, margin=2)
        self._body.setMinimumHeight(40)   # 放不下时回答在框里滚动，不和上面的挤在一起
        self._body.setOpenExternalLinks(True)
        self._body.viewport().setAutoFillBackground(False)
        self._body.verticalScrollBar().valueChanged.connect(self._place_copy_buttons)
        layout.addWidget(self._body, 1)
        bottom = QHBoxLayout()
        self._undo = make_button(_UNDO_STYLE, self._on_undo)
        self.undo_state_for = lambda card: "hidden"   # 总管换成自己的
        self._undo_state = "hidden"
        self._undo_armed = False
        self._undo_timer = QTimer(self, singleShot=True, interval=self.UNDO_ARM_MS, timeout=self._disarm_undo)
        bottom.addWidget(self._undo)
        bottom.addStretch(1)
        self._read = make_button(BUTTON_STYLE, lambda: self.card and self.read_requested.emit(self.card))
        bottom.addWidget(self._read)
        self._copy_all = make_button(BUTTON_STYLE, lambda: self._copy(self.card.answer if self.card else "",
                                                                       self._copy_all, "assistant_reader_copy_all"))
        bottom.addWidget(self._copy_all)
        # "🙈 不是跟你说的"：只在最新一张、用语音问的卡片上（总管换上 can_withdraw）
        self._withdraw = make_button(BUTTON_STYLE, lambda: self.card and self.withdraw_requested.emit(self.card))
        self.can_withdraw = lambda card: False
        bottom.addWidget(self._withdraw)
        layout.addLayout(bottom)

        self._swap = QVariantAnimation(self, duration=self.SWAP_MS, valueChanged=self._on_swap,
                                       finished=self._on_swap_finished)
        self._fading_out = False
        self.retranslate()
        self.resize(self.READER_W, 360)

    # ---------- 对外 ----------

    def show_card(self, card, anchor):
        """显示一张卡片的全文。anchor：芸芸 + 输入框 + 卡片堆占的区域（屏幕坐标），阅读框放它旁边。"""
        self._anchor = QRect(anchor)
        if self.is_open:
            if card is not self.card:
                self._pending = card          # 已经开着：内容淡出 → 换 → 淡入
                self._start_swap(out=True)
            return
        self._fill(card)
        self._show_animated()

    def close_now(self):
        self._swap.stop()
        super().close_now()

    def refresh_undo(self):
        """撤销按钮重新问一下状态：hidden（这一问没改文件）/ ready / busy（芸芸在回答）/ working（正在撤销）/
        expired（换过对话了，撤销不了）/ done（已经撤销了）。"""
        state = self.undo_state_for(self.card) if self.card is not None else "hidden"
        if state != self._undo_state:
            self._undo_state = state
            self._undo_armed = False
            self._undo_timer.stop()
        if self.card is not None:
            self._meta.setText(self._meta_text(self.card))
        self._show_undo()
        self._withdraw.setVisible(self.card is not None and self.can_withdraw(self.card))

    def retranslate(self):
        self._copy_all.setText(i18n.tr("assistant_reader_copy_all"))
        self._read.setText(i18n.tr("assistant_reader_read"))
        self._withdraw.setText(i18n.tr("assistant_reader_withdraw"))
        self._close.setToolTip(i18n.tr("assistant_reader_close"))
        if self.card is not None:
            self._fill(self.card)
        else:
            self._show_undo()
            self._show_actions()

    # ---------- 操作记录 ----------

    def _show_actions(self):
        actions = self.card.actions if self.card is not None else ()
        self._actions_toggle.setVisible(bool(actions))
        self._actions_toggle.setText(("▾ " if self._actions_open else "▸ ")
                                     + i18n.tr("assistant_actions_toggle", len(actions)))
        self._actions.setVisible(bool(actions) and self._actions_open)
        if actions and self._actions_open:
            self._actions.setHtml(actions_html(actions))

    def _toggle_actions(self):
        self._actions_open = not self._actions_open
        self._show_actions()
        self._fit_height()
        self._place()
        self._apply_open()

    # ---------- 撤销 ----------

    def _show_undo(self):
        state, armed = self._undo_state, self._undo_armed
        self._undo.setVisible(state != "hidden")
        self._undo.setEnabled(state == "ready")
        key = {"working": "assistant_undo_working", "done": "assistant_undo_done"}.get(
            state, "assistant_undo_confirm" if armed else "assistant_undo")
        self._undo.setText(i18n.tr(key))
        files = len(self.card.files) if self.card is not None else 0
        tip = {"busy": "assistant_undo_tip_busy", "expired": "assistant_undo_tip_expired"}.get(state, "assistant_undo_tip")
        self._undo.setToolTip(i18n.tr(tip, files))

    def _on_undo(self):
        if self._undo_state != "ready" or self.card is None:
            return
        if not self._undo_armed:   # 第一次点：先问一句
            self._undo_armed = True
            self._undo_timer.start()
            self._show_undo()
            return
        self._undo_armed = False
        self._undo_timer.stop()
        self.undo_requested.emit(self.card)

    def _disarm_undo(self):
        self._undo_armed = False
        self._undo_timer.stop()
        self._show_undo()

    # ---------- 内容 ----------

    @staticmethod
    def _meta_text(card):
        """"14:20 · $0.0123 · 上下文 … · 改了 2 个文件 · ……"：有的才写。"""
        tr = i18n.tr
        parts = [f"{card.time:%H:%M}", card.cost, context_status(card)[0],
                 card.files and tr("assistant_card_files", len(card.files)),
                 card.remembered and tr("assistant_card_remembered", card.remembered),
                 card.images and tr("assistant_card_images", card.images),
                 card.stopped and tr("assistant_card_stopped"), card.undone and tr("assistant_card_undone")]
        return " · ".join(part for part in parts if part)

    def _fill(self, card):
        self.card = card
        self._question.setText(i18n.tr("assistant_card_you", card.question))
        self._undo_state = None   # 换了卡片：撤销按钮重新问
        self.refresh_undo()
        self._show_actions()
        if card.answer:
            self._body.setMarkdown(card.answer)
            style_code_blocks(self._body.document())
        elif card.error:
            self._body.setHtml(f'<p style="color:#C62828;">{_escape(card.error)}</p>')
        else:
            self._body.setHtml(f'<p style="color:#9AA3B2;">{_escape(i18n.tr("assistant_card_empty"))}</p>')
        self._copy_all.setVisible(bool(card.answer))
        self._read.setVisible(bool(card.answer))
        self._make_copy_buttons()
        self._fit_height()
        self._body.verticalScrollBar().setValue(0)

    def _fit_height(self):
        """高度跟着内容（MIN_H～MAX_H，也不超过屏幕）。"""
        m, pad = self.MARGIN, self.PAD
        width = self.READER_W - 2 * (m + pad)
        self._body.document().setTextWidth(width - 10)
        doc_h = int(self._body.document().size().height()) + 18   # 多留一点：估少了就会冒出滚动条
        bottom_row = any(b.isVisibleTo(self) for b in (self._copy_all, self._undo, self._withdraw))
        others = (self._question.heightForWidth(width - 32) + self._meta.sizeHint().height() + 1
                  + (self._copy_all.sizeHint().height() if bottom_row else 0) + 6 * 4)
        if self._actions_toggle.isVisibleTo(self):
            others += self._actions_toggle.sizeHint().height() + 6
        limit = self._height_limit(self.MAX_H)
        if self._actions.isVisibleTo(self):
            self._actions.document().setTextWidth(width - 10)
            # 操作记录最多占三分之一（屏幕矮的时候也给回答留地方），再多就在框里滚动
            actions_h = min(int(self._actions.document().size().height()) + 8, self.ACTIONS_MAX_H, limit // 3)
            self._actions.setFixedHeight(actions_h)
            others += actions_h + 6
        self._set_size(self.READER_W, max(self.MIN_H, min(2 * (m + pad) + others + doc_h, limit)))
        self._place_copy_buttons()

    def _make_copy_buttons(self):
        for button in self._copy_buttons:
            button.deleteLater()
        self._copy_buttons = []
        for group in code_groups(self._body.document()):
            button = make_button(BUTTON_STYLE, None, i18n.tr("assistant_reader_copy"), self._body.viewport())
            text = group_text(group)
            button.clicked.connect(lambda checked=False, b=button, t=text: self._copy(t, b, "assistant_reader_copy"))
            button.group = group
            button.show()
            self._copy_buttons.append(button)

    def _place_copy_buttons(self):
        """每个代码块右上角浮一个"复制"（跟着滚动；滚出去了就藏起来）。"""
        layout = self._body.document().documentLayout()
        offset = self._body.verticalScrollBar().value()
        view = self._body.viewport().rect()
        for button in self._copy_buttons:
            rect = QRectF()
            for block in button.group:
                rect = rect.united(layout.blockBoundingRect(block))
            button.adjustSize()
            x = int(rect.right()) - button.width() - 4
            y = int(rect.top()) - offset + 4
            button.move(max(0, min(x, view.width() - button.width())), y)
            button.setVisible(view.contains(QRect(button.pos(), QSize(button.width(), 1))))

    def _copy(self, text, button, key):
        set_clipboard(text)
        button.setText(i18n.tr("assistant_reader_copied"))
        # 1.5 秒后按钮可能已经没了（换了卡片）
        QTimer.singleShot(1500, lambda: button.setText(i18n.tr(key)) if isValid(button) else None)

    # ---------- 换卡片的动画 ----------

    def _start_swap(self, out):
        self._fading_out = out
        effect = self._content.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self._content)
            self._content.setGraphicsEffect(effect)
        animate(self._swap, effect.opacity(), 0.0 if out else 1.0)

    def _on_swap(self, value):
        effect = self._content.graphicsEffect()
        if isinstance(effect, QGraphicsOpacityEffect):
            effect.setOpacity(float(value))

    def _on_swap_finished(self):
        if self._fading_out:
            card, self._pending = self._pending, None
            if card is not None:
                self._fill(card)
                self._place()
                self._apply_open()
            self._start_swap(out=False)
        else:
            self._content.setGraphicsEffect(None)   # 淡入完就拿掉：平时不多一层绘制

    # ---------- 事件 ----------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._close_by_user()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_copy_buttons()


def _escape(text):
    return html.escape(text, quote=False).replace("\n", "<br>")
