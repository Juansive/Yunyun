"""卡片堆：点小红点后，一封邮件一张卡片，卡片上是 AI 写的一句话。

样子（正在看第 2 封时）：

        ┌──────────┐   ← 第 1 封：缩小、淡化，藏到右上后方
      ┌─┴────────┐ │
      │  第 2 封  │─┘  ← 当前这封：正常颜色，在最前面
      │ AI 的话…  │
      └┬────────┬┘     ← 第 3 封：往下错开、淡化
       └────────┘      ← 第 4 封：更淡（再往后的不显示）

- 滚轮 / 上下方向键：切换卡片（平滑滑动）
- 单击当前卡片：放大，显示原始的标题、发件人、时间、附件；再点一下缩回
- 单击后面露出来的卡片：直接跳到那张
- 按 Esc、点卡片堆外面：收起

所有卡片都画在这一个窗口里（不是一张卡片一个窗口），错位、淡化、缩放都靠计算，动画才流畅。
它是芸芸的从属窗口，和芸芸在同一层。
"""
import time
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.pet import winapi
from yunyun.pet.bubble import place_block


@dataclass
class MailCard:
    """一张卡片要显示的内容（界面层自己的数据，不依赖邮件模块）。"""
    bubble: str                  # AI 写的一句话
    subject: str
    sender: str
    date: datetime | None = None
    attachments: list[str] = field(default_factory=list)


class CardDeck(QWidget):
    CARD_W = 260          # 卡片宽度（逻辑像素）
    PAD = 14              # 卡片内边距
    MARGIN = 8            # 窗口四周留白（给阴影）
    BEHIND_DY = 12        # 后面的卡片每张往下错开多少
    PREV_DX = 16          # 前面的卡片往右错开多少
    PREV_DY = 16          # 前面的卡片往上错开多少
    SHRINK = 0.06         # 每隔一张缩小多少
    BUBBLE_LINES = 3      # 收起时，AI 的话最多显示几行
    GAP = 10              # 卡片堆和芸芸之间的距离
    CLOSE_GRACE_S = 0.4   # 收起后这段时间内点小红点，也算"点小红点收起"

    closed = Signal()     # 用户收起了卡片堆

    def __init__(self, owner):
        super().__init__(owner)  # owner = 芸芸窗口：成为它的从属窗口
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # 能接收键盘（Esc、方向键）
        self._owner = owner
        self._bubble_font = QFont("Microsoft YaHei", 10)
        self._small_font = QFont("Microsoft YaHei", 8)

        self._cards = []
        self._current = 0          # 当前在最前面的是第几张
        self._pos = 0.0            # 动画中的"当前位置"（小数），画图时用它，所以切换是平滑的
        self._expanded = False     # 当前卡片是否放大
        self._note = ""            # 底部的小字提示（比如"还没设置 AI"）
        self._anchor = QRect()
        self._placement = None
        self._wheel_accum = 0      # 触摸板滚动很细碎，攒够一格再翻页
        self._was_active = False
        self._closed_at = 0.0

        self._scroll_anim = QVariantAnimation(self)
        self._scroll_anim.setDuration(220)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.valueChanged.connect(self._on_scroll)

    # ---------- 对外接口 ----------

    @property
    def card_count(self):
        return len(self._cards)

    @property
    def current_index(self):
        return self._current

    @property
    def expanded(self):
        return self._expanded

    def reset(self):
        """开始新的一轮：清空卡片，收起来。"""
        self._cards.clear()
        self._current, self._pos, self._expanded, self._note = 0, 0.0, False, ""
        self._placement = None
        self.hide()

    def add_card(self, card, anchor):
        """加一张卡片（加在最后面）。第一张到达时卡片堆出现。"""
        self._cards.append(card)
        self._anchor = QRect(anchor)
        self._relayout()
        if not self.isVisible():
            # 用户刚点过小红点（我们的程序是前台）时才拿焦点；用户已经去别处打字了就不抢键盘
            take_focus = QApplication.activeWindow() is not None
            with winapi.keep_z_order(int(self._owner.winId())):
                self.show()
            if take_focus:
                self.activateWindow()
                self.setFocus()

    def set_note(self, text):
        self._note = text
        self._relayout()

    def follow(self, anchor):
        self._anchor = QRect(anchor)
        if self.isVisible():
            self._relayout()

    def go_to(self, index):
        index = max(0, min(index, len(self._cards) - 1))
        self._expanded = False
        if index == self._current and self._scroll_anim.state() != QVariantAnimation.State.Running:
            self._relayout()
            return
        self._current = index
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(self._pos)
        self._scroll_anim.setEndValue(float(index))
        self._scroll_anim.start()
        self._relayout()

    def toggle_expanded(self):
        self._expanded = not self._expanded
        self._relayout()

    def close_deck(self):
        if self.isVisible():
            self.hide()
            self._closed_at = time.monotonic()
            self.closed.emit()

    def recently_closed(self):
        """刚刚收起（比如点小红点时，卡片堆先因为失去焦点收起了）。"""
        return time.monotonic() - self._closed_at < self.CLOSE_GRACE_S

    # ---------- 尺寸与位置 ----------

    def _text_height(self, text, font, width, max_lines=None):
        fm = QFontMetrics(font)
        h = fm.boundingRect(QRect(0, 0, int(width), 100000), Qt.TextFlag.TextWordWrap, text).height()
        return min(h, fm.lineSpacing() * max_lines) if max_lines else h

    def _collapsed_height(self):
        """所有卡片收起时用同一个高度：后面的卡片才能整齐地从下面露出一条边。"""
        text_w = self.CARD_W - 2 * self.PAD - 30  # 右上角给"2 / 5"留位置
        bubble_h = max((self._text_height(c.bubble, self._bubble_font, text_w, self.BUBBLE_LINES)
                        for c in self._cards), default=0)
        return self.PAD + bubble_h + 8 + QFontMetrics(self._small_font).lineSpacing() + self.PAD

    def _detail_lines(self, card):
        tr = i18n.tr
        lines = [
            (tr("card_subject"), card.subject or tr("card_no_subject")),
            (tr("card_sender"), card.sender),
        ]
        if card.date:
            lines.append((tr("card_time"), f"{card.date.astimezone():%Y-%m-%d %H:%M}"))
        if card.attachments:
            lines.append((tr("card_attachments"), tr("list_separator").join(a or "…" for a in card.attachments)))
        return [tr("card_detail_line", label, value) for label, value in lines]

    def _expanded_height(self):
        card = self._cards[self._current]
        text_w = self.CARD_W - 2 * self.PAD
        h = self.PAD + self._text_height(card.bubble, self._bubble_font, text_w - 30) + 14
        for line in self._detail_lines(card):
            h += self._text_height(line, self._small_font, text_w) + 4
        return h + self.PAD

    def _note_height(self):
        if not self._note:
            return 0
        return self._text_height(self._note, self._small_font, self.CARD_W - 16) + 8

    def _card_height(self):
        return self._expanded_height() if self._expanded else self._collapsed_height()

    def _relayout(self):
        if not self._cards:
            return
        card_h = self._card_height()
        stack_h = 0 if self._expanded else 2 * self.BEHIND_DY  # 放大时只显示当前这张
        w = 2 * self.MARGIN + self.CARD_W + self.PREV_DX
        h = 2 * self.MARGIN + self.PREV_DY + card_h + stack_h + self._note_height()
        self.resize(w, h)
        x, y, self._placement = place_block(self._anchor, w, h, gap=self.GAP, current=self._placement)
        self.move(int(x), int(y))
        self.update()

    # ---------- 每张卡片画在哪、多透明 ----------

    def _front_rect(self, h):
        return QRectF(self.MARGIN, self.MARGIN + self.PREV_DY, self.CARD_W, h)

    def _card_rect(self, d, h):
        """离当前卡片 d 张（d 可以是小数，动画中间态）的卡片矩形。d>0 在后面（往下），d<0 在前面（往右上）。"""
        base = self._front_rect(h)
        k = abs(d)
        scale = 1 - self.SHRINK * k
        w, hh = self.CARD_W * scale, h * scale
        if d >= 0:   # 后面的：底边往下错开，露出一条
            x = base.center().x() - w / 2
            y = base.bottom() + d * self.BEHIND_DY - hh
        else:        # 前面的：往右上错开，藏到后面
            x = base.x() + k * self.PREV_DX + (self.CARD_W - w) / 2
            y = base.y() - k * self.PREV_DY
        return QRectF(x, y, w, hh), scale

    @staticmethod
    def _opacity(d):
        """越远越淡：后面第 1 张 0.55、第 2 张 0.28、第 3 张看不见；前面第 1 张 0.4、第 2 张看不见。"""
        points = [(0, 1.0), (1, 0.55), (2, 0.28), (3, 0.0)] if d >= 0 else [(0, 1.0), (1, 0.4), (2, 0.0)]
        k = abs(d)
        for (k0, o0), (k1, o1) in zip(points, points[1:]):
            if k <= k1:
                return o0 + (o1 - o0) * (k - k0) / (k1 - k0)  # 两点之间线性插值
        return 0.0

    def _visible_cards(self):
        """要画的卡片：[(序号, d, 透明度)]，从最远到最近排好（先画远的，近的盖在上面）。"""
        if self._expanded:
            return [(self._current, 0.0, 1.0)]
        items = []
        for i in range(len(self._cards)):
            d = i - self._pos
            opacity = self._opacity(d)
            if opacity > 0.01:
                items.append((i, d, opacity))
        return sorted(items, key=lambda item: -abs(item[1]))

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        if not self._cards:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self._card_height()
        for i, d, opacity in self._visible_cards():
            rect, scale = self._card_rect(d, h)
            painter.save()
            painter.setOpacity(opacity)
            painter.translate(rect.topLeft())
            painter.scale(scale, scale)          # 整张卡片（包括文字）一起缩小
            self._paint_card(painter, self._cards[i], i, h, d)
            painter.restore()
        if self._note:
            # 底部提示：深色半透明圆角底 + 白字，桌面是深色还是浅色都看得清
            painter.setOpacity(1.0)
            note_rect = QRectF(self.MARGIN, self.height() - self.MARGIN - self._note_height(),
                               self.CARD_W, self._note_height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(40, 40, 40, 200))
            painter.drawRoundedRect(note_rect, 8, 8)
            painter.setFont(self._small_font)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(note_rect.adjusted(8, 3, -8, -3), Qt.TextFlag.TextWordWrap, self._note)

    def _paint_card(self, p, card, index, h, d):
        """画一张卡片。d：离当前卡片多远（0 = 当前这张）。"""
        w = self.CARD_W
        k = min(abs(d), 1.0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawRoundedRect(QRectF(1, 3, w, h), 12, 12)          # 简单的阴影
        # 离得越远底色越灰、边框越深：在浅色桌面上也看得出一层层的
        p.setPen(QPen(_blend("#C8C8C8", "#A8ADB3", k), 1))
        p.setBrush(_blend("#FFFFFF", "#E4E7EB", k))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 12, 12)

        # 文字只画在当前这张上；切换途中随距离淡出，后面露出来的边就不会有杂乱的文字
        text_opacity = 1.0 - k
        if text_opacity <= 0.01:
            return
        p.setOpacity(p.opacity() * text_opacity)
        front = abs(d) < 0.5

        pad = self.PAD
        p.setFont(self._small_font)
        p.setPen(QColor("#9AA0A6"))
        if len(self._cards) > 1:                                # 右上角："2 / 5"
            p.drawText(QRectF(w - pad - 40, pad - 2, 40, 16), Qt.AlignmentFlag.AlignRight,
                       f"{index + 1} / {len(self._cards)}")

        text_w = w - 2 * pad - 30
        expanded = front and self._expanded
        bubble_h = self._text_height(card.bubble, self._bubble_font, text_w,
                                     None if expanded else self.BUBBLE_LINES)
        p.setFont(self._bubble_font)
        p.setPen(QColor("#222222"))
        p.drawText(QRectF(pad, pad, text_w, bubble_h), Qt.TextFlag.TextWordWrap, card.bubble)

        p.setFont(self._small_font)
        p.setPen(QColor("#80868B"))
        if not expanded:                                        # 收起：底部一行"发件人 · 时间"
            line = card.sender + (f" · {_short_time(card.date)}" if card.date else "")
            fm = QFontMetrics(self._small_font)
            line = fm.elidedText(line, Qt.TextElideMode.ElideRight, int(w - 2 * pad))  # 太长就用…截断
            p.drawText(QRectF(pad, h - pad - fm.lineSpacing(), w - 2 * pad, fm.lineSpacing()), 0, line)
            return

        y = pad + bubble_h + 6                                   # 放大：分隔线 + 原始信息
        p.setPen(QPen(QColor("#E8EAED"), 1))
        p.drawLine(int(pad), int(y), int(w - pad), int(y))
        y += 8
        p.setPen(QColor("#5F6368"))
        for line in self._detail_lines(card):
            lh = self._text_height(line, self._small_font, w - 2 * pad)
            p.drawText(QRectF(pad, y, w - 2 * pad, lh), Qt.TextFlag.TextWordWrap, line)
            y += lh + 4

    # ---------- 交互 ----------

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._cards:
            return
        pos = event.position()
        h = self._card_height()
        # 从最近的往最远的找：点到哪张算哪张
        for i, d, opacity in reversed(self._visible_cards()):
            rect, _ = self._card_rect(d, h)
            if opacity > 0.05 and rect.contains(pos):
                if i == self._current:
                    self.toggle_expanded()
                else:
                    self.go_to(i)
                return

    def wheelEvent(self, event):
        self._wheel_accum += event.angleDelta().y()
        while self._wheel_accum <= -120:    # 往下滚：看下一封（更旧的）
            self._wheel_accum += 120
            self.go_to(self._current + 1)
        while self._wheel_accum >= 120:     # 往上滚：看上一封
            self._wheel_accum -= 120
            self.go_to(self._current - 1)
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close_deck()
        elif key == Qt.Key.Key_Down:
            self.go_to(self._current + 1)
        elif key == Qt.Key.Key_Up:
            self.go_to(self._current - 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle_expanded()
        else:
            super().keyPressEvent(event)

    def changeEvent(self, event):
        # 失去焦点 = 用户点了卡片堆外面 → 收起（只在它曾经拿到过焦点之后才算）
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._was_active = True
            elif self._was_active:
                self._was_active = False
                self.close_deck()
        super().changeEvent(event)

    def hideEvent(self, event):
        self._was_active = False
        super().hideEvent(event)

    def _on_scroll(self, value):
        self._pos = value
        self.update()


def _blend(color_a, color_b, t):
    """两个颜色按比例混合：t=0 是 a，t=1 是 b。"""
    a, b = QColor(color_a), QColor(color_b)
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def _short_time(date):
    """今天的只显示时:分，其他日子显示 月-日 时:分。"""
    local = date.astimezone()
    if local.date() == datetime.now().astimezone().date():
        return f"{local:%H:%M}"
    return f"{local:%m-%d %H:%M}"
