"""助手模式的历史对话面板：以前的每一段对话，点开看全部问答，"接着聊"就回到那段对话继续。

    ┌ 历史对话（保留 30 天）            × ┐        ┌ ← 返回                          × ┐
    │ 今天                                │        │ 运行测试，把 calc.py 里的 bug 修好  │
    │  帮我读一下 README……                │  点一段 │ 10-08 00:35 · calc-bug · 1 问 · $0.04 │
    │  14:20 · calc-bug · 3 问 · $0.02    │ ─────▶ │ 你：……                              │
    │ 昨天                                │        │ 回答……                              │
    │  Python 里怎么倒序？                │        │ （从 Claude Code 的记录里找回的……）  │
    │  19:40 · 只聊天 · 2 问              │        │                         [接着聊]    │
    └─────────────────────────────────────┘        └─────────────────────────────────────┘

- 放在阅读框那个位置（卡片组的一部分：常亮、能拖、"隐藏卡片"时一起收）；开着时阅读框让开。
- 当前正在聊的那一段不列（它就在卡片堆里）。
- 接着聊 → continue_requested(那段对话)；总管切到那段的工作文件夹、用它的会话编号重新启动大脑。
- Esc：详情里 = 返回列表；列表里 = 关掉。
"""
import os
from datetime import date

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem

from yunyun import i18n
from yunyun.assistant.settings import HISTORY_KEEP_DAYS
from yunyun.pet.assistant_reader import style_code_blocks
from yunyun.pet.side_panel import (
    BUTTON_STYLE, SCROLLBAR_STYLE, TEXT_STYLE, SidePanel, divider, make_button, text_view,
)
from yunyun.pet.widgets import day_label, note

TITLE_CHARS = 40

_LIST_STYLE = """
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { border-radius: 8px; padding: 6px 8px; margin: 1px 0; color: #1F2937; }
QListWidget::item:hover { background: #F1F5FD; }
QListWidget::item:selected { background: #E3ECFC; color: #1F2937; }
QListWidget::item:disabled { color: #8A94A6; padding: 8px 4px 2px 4px; }
""" + SCROLLBAR_STYLE
_CONTINUE_STYLE = """
QPushButton { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #4FA8FF, stop:1 #7C8CFF); color: white;
              border: none; border-radius: 11px; padding: 4px 16px; font-weight: bold; }
QPushButton:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #63B3FF, stop:1 #8E9BFF); }
"""


def folder_label(workspace):
    if not workspace:
        return i18n.tr("assistant_history_chat_only")
    return os.path.basename(os.path.normpath(workspace)) or workspace


def meta_text(conversation, with_date=False):
    """"14:20 · calc-bug · 3 问 · $0.02"（详情里带日期）。"""
    when = f"{conversation.updated:%m-%d %H:%M}" if with_date else f"{conversation.updated:%H:%M}"
    parts = [when, folder_label(conversation.workspace), i18n.tr("assistant_history_turns", len(conversation.cards))]
    if conversation.cost_usd:
        parts.append(f"${conversation.cost_usd:.2f}" if conversation.cost_usd >= 0.01 else f"${conversation.cost_usd:.4f}")
    return " · ".join(parts)


def short(text, limit=TITLE_CHARS):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def conversation_markdown(conversation):
    """一段对话的全部问答（Markdown）：你的问题加粗，回答原样，下面一行小字（时间、停止了没有）。"""
    parts = []
    for card in conversation.cards:
        meta = [f"{card.time:%m-%d %H:%M}"] + ([card.cost] if card.cost else [])
        if card.stopped:
            meta.append(i18n.tr("assistant_card_stopped"))
        answer = card.answer or card.error or i18n.tr("assistant_card_empty")
        # "**你：** 问题"：** 后面要有空格（紧挨着中文冒号和文字时 Markdown 不认它是加粗的结尾）
        parts.append(f"**{i18n.tr('assistant_card_you', '').strip()}** {card.question}\n\n{answer}\n\n"
                     f"*{' · '.join(meta)}*")
    return "\n\n---\n\n".join(parts)


class AssistantHistoryPanel(SidePanel):
    continue_requested = Signal(object)   # Conversation：接着这一段聊（× / Esc 在列表里：closed_by_user）

    PANEL_W = 470
    LIST_H = 420
    DETAIL_H = 520

    def __init__(self, owner):
        super().__init__(owner)
        self.conversation = None          # 详情里正在看的那段（列表里是 None）
        self._conversations = []

        layout = self._make_content(6)
        top = QHBoxLayout()
        self._back = make_button(BUTTON_STYLE, self.show_list)
        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setStyleSheet("color: #111827; font-weight: bold;")
        self._close = self._close_button()
        top.addWidget(self._back, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self._title, 1)
        top.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self._meta = QLabel()
        self._meta.setStyleSheet("color: #8A94A6; font-size: 9pt;")
        layout.addWidget(self._meta)
        layout.addWidget(divider())

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_STYLE)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemClicked.connect(self._on_item)
        layout.addWidget(self._list, 1)
        self._empty = note(color="#9AA3B2")
        layout.addWidget(self._empty)

        self._detail = text_view(TEXT_STYLE, web_links=True)
        self._detail.viewport().setAutoFillBackground(False)
        layout.addWidget(self._detail, 1)
        bottom = QHBoxLayout()
        self._note = note()   # 找回来的对话：说明少了些什么
        self._note.setStyleSheet("color: #8A94A6; font-size: 9pt;")
        self._continue = make_button(_CONTINUE_STYLE, self._on_continue)
        bottom.addWidget(self._note, 1)
        bottom.addWidget(self._continue, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(bottom)
        self.retranslate()
        self._show_mode(detail=False)

    # ---------- 对外 ----------

    def show_conversations(self, conversations, anchor, current_session=""):
        """打开列表。conversations：assistant_history.conversations()（最新的在前）；current_session：正在聊的那段（不列）。"""
        self._anchor = QRect(anchor)
        self._conversations = [c for c in conversations if c.session_id != current_session]
        self._fill_list()
        self.show_list()
        self._show_animated()

    def show_list(self):
        self.conversation = None
        self._show_mode(detail=False)

    def open_conversation(self, conversation):
        """详情：这一段的全部问答。"""
        self.conversation = conversation
        self._title.setText(short(conversation.title, 60))
        self._meta.setText(meta_text(conversation, with_date=True))
        self._detail.setMarkdown(conversation_markdown(conversation))
        style_code_blocks(self._detail.document())
        self._detail.verticalScrollBar().setValue(self._detail.verticalScrollBar().maximum())   # 从最近的一问看起
        self._note.setText(i18n.tr("assistant_history_recovered") if conversation.recovered else "")
        self._show_mode(detail=True)

    def retranslate(self):
        self._back.setText(i18n.tr("assistant_history_back"))
        self._close.setToolTip(i18n.tr("assistant_reader_close"))
        self._continue.setText(i18n.tr("assistant_history_continue"))
        self._empty.setText(i18n.tr("assistant_history_empty", HISTORY_KEEP_DAYS))
        if self.conversation is not None:
            self.open_conversation(self.conversation)
        else:
            self._fill_list()
            self._show_mode(detail=False)

    # ---------- 内容 ----------

    def _fill_list(self):
        self._list.clear()
        today, current_day = date.today(), None
        for conversation in self._conversations:
            day = conversation.updated.date()
            if day != current_day:
                current_day = day
                header = QListWidgetItem(day_label(day, today))
                header.setFlags(Qt.ItemFlag.NoItemFlags)   # 日期标题：不能选
                font = QFont(self.font())
                font.setPointSize(9)
                header.setFont(font)
                self._list.addItem(header)
            item = QListWidgetItem(f"{short(conversation.title)}\n{meta_text(conversation)}")
            item.setData(Qt.ItemDataRole.UserRole, conversation)
            item.setToolTip(conversation.title)
            self._list.addItem(item)

    def _show_mode(self, detail):
        has_any = bool(self._conversations)
        self._back.setVisible(detail)
        self._list.setVisible(not detail and has_any)
        self._empty.setVisible(not detail and not has_any)
        self._detail.setVisible(detail)
        self._continue.setVisible(detail)
        self._note.setVisible(detail and bool(self._note.text()))
        self._meta.setVisible(True)
        if not detail:
            self._title.setText(i18n.tr("assistant_history_title", HISTORY_KEEP_DAYS))
            self._meta.setText(i18n.tr("assistant_history_hint"))
        self._fit()

    def _fit(self):
        self._set_size(self.PANEL_W, self._height_limit(self.DETAIL_H if self.conversation is not None else self.LIST_H))
        if self.isVisible():
            self._place()
            self._apply_open()

    # ---------- 事件 ----------

    def _on_item(self, item):
        conversation = item.data(Qt.ItemDataRole.UserRole)
        if conversation is not None:
            self.open_conversation(conversation)

    def _on_continue(self):
        if self.conversation is not None:
            self.continue_requested.emit(self.conversation)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.conversation is not None:
                self.show_list()
            else:
                self._close_by_user()
            return
        super().keyPressEvent(event)
