"""助手模式的阅读框：单击卡片后，从卡片堆旁边滑出来，显示这一问一答的全文。

    ┌──────────────────────────────────┐
    │ 你：Python 里怎么把列表倒过来？ × │
    │ 14:20 · $0.0123                  │
    │──────────────────────────────────│
    │ 有两种常用写法：                   │
    │ ┌────────────────────── [复制] ┐ │   ← 代码块灰底，右上角浮着"复制"
    │ │ nums[::-1]                   │ │
    │ └──────────────────────────────┘ │
    │                        复制回答   │
    └──────────────────────────────────┘

- 放在"芸芸 + 输入框 + 卡片堆"旁边（哪边空间大放哪边），从卡片堆那边滑出、淡入；高度跟着内容，短回答不会空一大块。
- 换一张卡片：内容先淡出、换好再淡入（高度在看不见的那一刻变）。
- Esc / × → closed_by_user（只关阅读框）；焦点去了别的程序 → dismissed（助手模式整个收起）。
"""
from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout,
    QWidget,
)

from shiboken6 import isValid

from yunyun import i18n
from yunyun.pet.bubble import place_block

CODE_BACKGROUND = QColor("#F3F5F9")

_BUTTON_STYLE = """
QPushButton { background: #EEF3FC; color: #4C6FD8; border: 1px solid #D5E0F5; border-radius: 9px;
              padding: 1px 9px; font-size: 9pt; }
QPushButton:hover { background: #E1EBFD; }
"""
_CLOSE_STYLE = """
QPushButton { background: transparent; color: #8A94A6; border: none; border-radius: 12px; font-size: 13pt; }
QPushButton:hover { background: #EEF1F6; color: #374151; }
"""
_BODY_STYLE = """
QTextBrowser { background: transparent; color: #1F2937; }
QScrollBar:vertical { width: 6px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #CBD3E1; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


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


def set_clipboard(text):
    """放进剪贴板（测试时换掉，免得动到用户真实的剪贴板）。"""
    QApplication.clipboard().setText(text)


def group_text(group):
    return "\n".join(block.text() for block in group).replace(" ", "\n")


class AssistantReader(QWidget):
    closed_by_user = Signal()    # Esc / ×：只关阅读框（焦点回到输入框）
    dismissed = Signal()         # 焦点去了别的程序：助手模式整个收起

    READER_W = 470
    MIN_H = 200
    MAX_H = 640
    MARGIN = 12          # 窗口四周留白（给阴影）
    PAD = 18
    GAP = 14
    SLIDE = 22           # 滑出来的距离
    OPEN_MS = 280
    SWAP_MS = 110        # 换卡片时淡出 / 淡入各多久

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Microsoft YaHei", 10))
        self.card = None
        self._anchor = QRect()
        self._placement = None
        self._target_pos = QPoint()
        self._shown = 0.0
        self._hiding = False
        self._pending = None          # 换卡片：淡出后要换上的那张
        self._copy_buttons = []

        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(8)
        self._question = QLabel()
        self._question.setWordWrap(True)
        self._question.setTextFormat(Qt.TextFormat.PlainText)
        self._question.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._question.setStyleSheet("color: #111827; font-weight: bold;")
        self._close = QPushButton("×")
        self._close.setFixedSize(24, 24)
        self._close.setStyleSheet(_CLOSE_STYLE)
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.clicked.connect(self._close_by_user)
        top.addWidget(self._question, 1)
        top.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        self._meta = QLabel()
        self._meta.setStyleSheet("color: #8A94A6; font-size: 9pt;")
        layout.addWidget(self._meta)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: #E6EAF1;")
        layout.addWidget(line)
        self._body = QTextBrowser()
        self._body.setOpenExternalLinks(True)
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setStyleSheet(_BODY_STYLE)
        self._body.viewport().setAutoFillBackground(False)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body.document().setDocumentMargin(2)
        self._body.verticalScrollBar().valueChanged.connect(self._place_copy_buttons)
        layout.addWidget(self._body, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self._copy_all = QPushButton()
        self._copy_all.setStyleSheet(_BUTTON_STYLE)
        self._copy_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy_all.clicked.connect(lambda: self._copy(self.card.answer if self.card else "", self._copy_all,
                                                          "assistant_reader_copy_all"))
        bottom.addWidget(self._copy_all)
        layout.addLayout(bottom)

        self._open = QVariantAnimation(self, duration=self.OPEN_MS, easingCurve=QEasingCurve.Type.OutCubic)
        self._open.valueChanged.connect(self._on_open)
        self._open.finished.connect(self._on_open_finished)
        self._swap = QVariantAnimation(self, duration=self.SWAP_MS)
        self._swap.valueChanged.connect(self._on_swap)
        self._swap.finished.connect(self._on_swap_finished)
        self._fading_out = False
        self.retranslate()
        self.resize(self.READER_W, 360)

    # ---------- 对外 ----------

    @property
    def is_open(self):
        return self.isVisible() and not self._hiding

    def show_card(self, card, anchor):
        """显示一张卡片的全文。anchor：芸芸 + 输入框 + 卡片堆占的区域（屏幕坐标），阅读框放它旁边。"""
        self._anchor = QRect(anchor)
        if self.isVisible() and not self._hiding:
            if card is not self.card:
                self._pending = card          # 已经开着：内容淡出 → 换 → 淡入
                self._start_swap(out=True)
            return
        self._hiding = False
        self._fill(card)
        self._place()
        if not self.isVisible():
            self._shown = 0.0
            self._apply_open()
            self.show()
        self.raise_()
        self._animate(self._open, self._shown, 1.0)

    def hide_animated(self):
        if self.isVisible() and not self._hiding:
            self._hiding = True
            self._animate(self._open, self._shown, 0.0)

    def close_now(self):
        self._open.stop()
        self._swap.stop()
        self._hiding = False
        self.hide()

    def follow(self, anchor):
        self._anchor = QRect(anchor)
        if self.isVisible():
            self._place()
            self._apply_open()

    def retranslate(self):
        self._copy_all.setText(i18n.tr("assistant_reader_copy_all"))
        self._close.setToolTip(i18n.tr("assistant_reader_close"))
        if self.card is not None:
            self._fill(self.card)

    # ---------- 内容 ----------

    def _fill(self, card):
        self.card = card
        self._question.setText(i18n.tr("assistant_card_you", card.question))
        parts = [f"{card.time:%H:%M}"] + ([card.cost] if card.cost else [])
        if card.stopped:
            parts.append(i18n.tr("assistant_card_stopped"))
        self._meta.setText(" · ".join(parts))
        if card.answer:
            self._body.setMarkdown(card.answer)
            style_code_blocks(self._body.document())
        elif card.error:
            self._body.setHtml(f'<p style="color:#C62828;">{_escape(card.error)}</p>')
        else:
            self._body.setHtml(f'<p style="color:#9AA3B2;">{_escape(i18n.tr("assistant_card_empty"))}</p>')
        self._copy_all.setVisible(bool(card.answer))
        self._make_copy_buttons()
        self._fit_height()
        self._body.verticalScrollBar().setValue(0)

    def _fit_height(self):
        """高度跟着内容（MIN_H～MAX_H，也不超过屏幕）。"""
        m, pad = self.MARGIN, self.PAD
        width = self.READER_W - 2 * (m + pad)
        self._body.document().setTextWidth(width - 10)
        doc_h = int(self._body.document().size().height()) + 18   # 多留一点：估少了就会冒出滚动条
        others = (self._question.heightForWidth(width - 32) + self._meta.sizeHint().height() + 1
                  + (self._copy_all.sizeHint().height() if self._copy_all.isVisibleTo(self) else 0) + 6 * 4)
        screen = QGuiApplication.screenAt(self._anchor.center()) or QGuiApplication.primaryScreen()
        limit = min(self.MAX_H, screen.availableGeometry().height() - 40)
        height = max(self.MIN_H, min(2 * (m + pad) + others + doc_h, limit))
        self.resize(self.READER_W, height)
        self._content.setGeometry(m + pad, m + pad, width, height - 2 * (m + pad))
        self._place_copy_buttons()

    def _make_copy_buttons(self):
        for button in self._copy_buttons:
            button.deleteLater()
        self._copy_buttons = []
        for group in code_groups(self._body.document()):
            button = QPushButton(i18n.tr("assistant_reader_copy"), self._body.viewport())
            button.setStyleSheet(_BUTTON_STYLE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
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
        QTimer.singleShot(1500, lambda: button.setText(i18n.tr(key)) if _alive(button) else None)

    # ---------- 位置和动画 ----------

    def _place(self):
        m = self.MARGIN
        x, y, self._placement = place_block(
            self._anchor, self.width() - 2 * m, self.height() - 2 * m, gap=self.GAP, current=self._placement,
            prefer="side")
        self._target_pos = QPoint(int(x) - m, int(y) - m)

    @staticmethod
    def _animate(animation, start, end):
        animation.stop()
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.start()

    def _on_open(self, value):
        self._shown = float(value)
        self._apply_open()

    def _apply_open(self):
        """从卡片堆那边滑出来：placement 是 right 就从左往右滑，left 就从右往左。"""
        direction = -1 if self._placement == "right" else 1 if self._placement == "left" else 0
        dy = 0 if direction else -self.SLIDE   # 放在上 / 下方时竖着滑
        offset = int(self.SLIDE * (1 - self._shown))
        self.move(self._target_pos + QPoint(direction * offset, int(dy * (1 - self._shown))))
        self.setWindowOpacity(self._shown)

    def _on_open_finished(self):
        if self._hiding:
            self._hiding = False
            self.hide()

    def _start_swap(self, out):
        self._fading_out = out
        effect = self._content.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self._content)
            self._content.setGraphicsEffect(effect)
        self._animate(self._swap, effect.opacity(), 0.0 if out else 1.0)

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

    def _close_by_user(self):
        self.hide_animated()
        self.closed_by_user.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._close_by_user()
            return
        super().keyPressEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, self._check_focus_lost)
        super().changeEvent(event)

    def _check_focus_lost(self):
        if self.isVisible() and not self._hiding and QApplication.activeWindow() is None:
            self.dismissed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_copy_buttons()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN)
        for i in range(6, 0, -1):   # 柔和的阴影
            shadow = QPainterPath()
            shadow.addRoundedRect(rect.adjusted(-i, -i + 3, i, i + 3), 14 + i, 14 + i)
            p.fillPath(shadow, QColor(30, 40, 70, int(8 * (7 - i) / 6)))
        body = QPainterPath()
        body.addRoundedRect(rect, 14, 14)
        p.fillPath(body, QColor(255, 255, 255, 252))
        p.setPen(QColor("#DDE3EE"))
        p.drawPath(body)


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"))


def _alive(widget):
    return isValid(widget)   # 1.5 秒后按钮可能已经没了（换了卡片）
