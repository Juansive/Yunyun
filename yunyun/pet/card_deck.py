"""卡片堆：点小红点后，一封邮件一张卡片，卡片上是 AI 写的一句话。

样子（正在看第 2 封时）：

        ┌──────────┐   ← 第 1 封：缩小、淡化，藏到右上后方
      ┌─┴────────┐ │
      │  第 2 封  │─┘  ← 当前这封：正常颜色，在最前面
      │ AI 的话…  │
      └┬────────┬┘     ← 第 3 封：往下错开、淡化
       └────────┘      ← 第 4 封：更淡（再往后的不显示）

- 滚轮 / 上下方向键：切换卡片（平滑滑动）
- 单击当前卡片：按下时微微下沉，松开后平滑展开，显示原始的标题、发件人、时间、附件；再点一下平滑缩回
- 添加了两个以上邮箱时，底部右边有个小标签写着是哪个邮箱收的（mailbox_label）
- 单击后面露出来的卡片：直接跳到那张
- 按 Esc、点卡片堆外面：收起

所有卡片都画在这一个窗口里（不是一张卡片一个窗口），错位、淡化、缩放、展开都靠计算，动画才流畅。
它是芸芸的从属窗口，和芸芸在同一层。
"""
import time
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.pet import winapi
from yunyun.pet.bubble import animate, blend, place_block


@dataclass
class MailCard:
    """一张卡片要显示的内容（界面层自己的数据，不依赖邮件模块）。"""
    bubble: str                  # AI 写的一句话
    subject: str
    sender: str
    date: datetime | None = None
    attachments: list[str] = field(default_factory=list)
    mailbox: str = ""            # 底部右边的邮箱小标签（mailbox_label 算的；空 = 不标）
    mailbox_address: str = ""    # 展开后"收件邮箱"那一行（有标签时才有）
    suggestions: list = field(default_factory=list)   # 邮件 agent 的提醒建议（有 op / text / when / target / repeat）
    suspicious: bool = False     # 邮件 agent 认为是钓鱼 / 诈骗：左上角标出来


def mailbox_label(address, accounts):
    """是哪个邮箱收的：只有一个邮箱时不标（返回 ""）；类型不重复写"163邮箱"，同类型有好几个（或邮箱已经删了）写 @ 前面那部分。
    accounts：现在添加的邮箱（有 .provider / .address 的对象）。"""
    if not address or len(accounts) < 2:
        return ""
    provider = next((a.provider for a in accounts if a.address.lower() == address.lower()), None)
    if provider is None or sum(a.provider == provider for a in accounts) > 1:
        return address.partition("@")[0]
    return i18n.tr(f"provider_{provider}")


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
    PRESS_MS = 90         # 按下时"下沉"的时长
    EXPAND_MS = 280       # 展开 / 缩回的时长
    PRESS_SCALE = 0.035   # 按下时缩小多少（3.5%）
    PRESS_SINK = 2.0      # 按下时往下沉多少像素

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
        self._expanded = False     # 当前卡片是否放大（目标状态；动画中途就已经是新的值）
        self._note = ""            # 底部的小字提示（比如"还没设置 AI"）
        self._anchor = QRect()
        self._placement = None
        self._wheel_accum = 0      # 触摸板滚动很细碎，攒够一格再翻页
        self._was_active = False
        self._closed_at = 0.0

        self._scroll_anim = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.Type.OutCubic,
                                              valueChanged=self._on_scroll)

        # ---- 按下"下沉" ----
        self._press_t = 0.0        # 0 = 正常，1 = 完全按下去
        self._pressed_index = None # 按下时点中的是第几张
        self._press_anim = QVariantAnimation(self, easingCurve=QEasingCurve.Type.OutQuad,
                                             valueChanged=self._on_press_progress)

        # ---- 展开 / 缩回 ----
        # 动画开始时，窗口先一次性变成"展开前"和"展开后"都装得下的大小；动画过程中只在窗口里
        # 画出卡片的中间状态（位置和大小在两种样子之间插值）；结束后窗口再收成最终大小。
        # 这样卡片在屏幕上的位置是连续的，不会因为窗口反复改大小而抖动。
        self._morph_v = 0.0        # 动画进度 0~1
        self._morph_from = QRectF()  # 当前卡片在屏幕上的起点矩形
        self._morph_to = QRectF()    # 终点矩形
        self._morph_end_geometry = QRect()
        self._relayout_pending = False
        self._expand_anim = QVariantAnimation(   # 先快后慢，停得柔和
            self, startValue=0.0, endValue=1.0, duration=self.EXPAND_MS, easingCurve=QEasingCurve.Type.OutCubic,
            valueChanged=self._on_morph, finished=self._on_morph_finished)

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

    @property
    def animating(self):
        return self._expand_anim.state() == QVariantAnimation.State.Running

    def reset(self):
        """开始新的一轮：清空卡片，收起来。"""
        self._expand_anim.stop()
        self._cards.clear()
        self._current, self._pos, self._expanded, self._note = 0, 0.0, False, ""
        self._press_t, self._pressed_index, self._relayout_pending = 0.0, None, False
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
        if self.animating:
            return  # 正在展开 / 缩回：等动画结束再切换
        index = max(0, min(index, len(self._cards) - 1))
        if self._expanded:
            self._expanded = False  # 切换卡片时直接缩回
        if index == self._current and self._scroll_anim.state() != QVariantAnimation.State.Running:
            self._relayout()
            return
        self._current = index
        animate(self._scroll_anim, self._pos, index)
        self._relayout()

    def toggle_expanded(self):
        """平滑地展开 / 缩回当前卡片。"""
        if not self._cards or self.animating:
            return
        start_geometry = self.geometry()
        self._morph_from = self._front_rect(self._card_height()).translated(QPointF(start_geometry.topLeft()))

        self._expanded = not self._expanded
        end_geometry, end_h, self._placement = self._target_geometry(self._expanded)
        self._morph_to = self._front_rect(end_h).translated(QPointF(end_geometry.topLeft()))
        self._morph_end_geometry = end_geometry

        self.setGeometry(start_geometry.united(end_geometry))  # 动画期间：两种样子都装得下
        self._morph_v = 0.0
        self._expand_anim.start()

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
        if card.mailbox_address:
            lines.append((tr("card_mailbox"), card.mailbox_address))
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

    def _target_geometry(self, expanded):
        """某种状态（收起 / 展开）下，窗口应该在的位置和大小。返回 (QRect, 卡片高度, 方向)。"""
        card_h = self._expanded_height() if expanded else self._collapsed_height()
        stack_h = 0 if expanded else 2 * self.BEHIND_DY  # 放大时只显示当前这张
        w = 2 * self.MARGIN + self.CARD_W + self.PREV_DX
        h = 2 * self.MARGIN + self.PREV_DY + card_h + stack_h + self._note_height()
        x, y, placement = place_block(self._anchor, w, h, gap=self.GAP, current=self._placement)
        return QRect(int(x), int(y), w, h), card_h, placement

    def _relayout(self):
        if not self._cards:
            return
        if self.animating:
            self._relayout_pending = True  # 正在展开 / 缩回：等动画结束再重新排
            return
        geometry, _, self._placement = self._target_geometry(self._expanded)
        self.setGeometry(geometry)
        self.update()

    # ---------- 每张卡片画在哪、多透明 ----------

    def _front_rect(self, h):
        return QRectF(self.MARGIN, self.MARGIN + self.PREV_DY, self.CARD_W, h)

    def _card_rect(self, d, h, base=None):
        """离当前卡片 d 张（d 可以是小数，动画中间态）的卡片矩形。d>0 在后面（往下），d<0 在前面（往右上）。

        base：当前卡片收起时的矩形（默认是静止时的位置）。
        """
        base = base or self._front_rect(h)
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
        if self._expanded and not self.animating:
            return [(self._current, 0.0, 1.0)]
        items = []
        for i in range(len(self._cards)):
            d = i - self._pos
            opacity = self._opacity(d)
            if opacity > 0.01:
                items.append((i, d, opacity))
        return sorted(items, key=lambda item: -abs(item[1]))

    def _expand_amount(self):
        """当前卡片"展开了多少"：0 = 收起，1 = 完全展开（动画中间是小数）。"""
        if self.animating:
            return self._morph_v if self._expanded else 1.0 - self._morph_v
        return 1.0 if self._expanded else 0.0

    def _current_front_rect(self):
        """当前卡片此刻在窗口里的矩形（展开动画中是起点和终点之间的插值）。"""
        if self.animating:
            r = _lerp_rect(self._morph_from, self._morph_to, self._morph_v)
            return r.translated(-QPointF(self.geometry().topLeft()))  # 屏幕坐标 → 窗口坐标
        return self._front_rect(self._card_height())

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        if not self._cards:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        front = self._current_front_rect()
        t = self._expand_amount()
        collapsed_h = self._collapsed_height()
        # 其他卡片都相对于"当前卡片收起时的位置"来排；展开时它们逐渐淡出、被当前卡片盖住
        base = QRectF(front.x(), front.y(), self.CARD_W, collapsed_h)
        others_opacity = 1.0 - t
        # 正在滚动切换：所有卡片（包括当前这张）都按各自滑动中的位置画；
        # 静止或正在展开时：当前卡片在最后单独画（它可能在展开，也可能被按下去了一点）
        scrolling = abs(self._current - self._pos) > 1e-3

        for i, d, opacity in self._visible_cards():
            if i == self._current and not scrolling:
                continue
            if others_opacity * opacity <= 0.01:
                continue
            rect, scale = self._card_rect(d, collapsed_h, base)
            painter.save()
            painter.setOpacity(opacity * others_opacity)
            painter.translate(rect.topLeft())
            painter.scale(scale, scale)          # 整张卡片（包括文字）一起缩小
            self._paint_card(painter, self._cards[i], i, collapsed_h, d, detail=0.0)
            painter.restore()
        if scrolling:
            self._paint_note(painter, front, t)
            return

        painter.save()
        press = 1 - self.PRESS_SCALE * self._press_t
        center = front.center()
        painter.translate(center.x(), center.y() + self.PRESS_SINK * self._press_t)
        painter.scale(press, press)              # 按下：以中心为原点微微缩小、往下沉
        painter.translate(-front.width() / 2, -front.height() / 2)
        self._paint_card(painter, self._cards[self._current], self._current, front.height(), 0.0, detail=t)
        painter.restore()
        self._paint_note(painter, front, t)

    def _paint_note(self, painter, front, t):
        """底部提示：深色半透明圆角底 + 白字，桌面是深色还是浅色都看得清。跟在卡片（和露出的边）下面。"""
        if not self._note:
            return
        painter.setOpacity(1.0)
        stack_h = 2 * self.BEHIND_DY * (1 - t)
        note_rect = QRectF(front.x(), front.bottom() + stack_h, self.CARD_W, self._note_height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(40, 40, 40, 200))
        painter.drawRoundedRect(note_rect, 8, 8)
        painter.setFont(self._small_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(note_rect.adjusted(8, 3, -8, -3), Qt.TextFlag.TextWordWrap, self._note)

    def _paint_card(self, p, card, index, h, d, detail):
        """画一张卡片（左上角在原点）。d：离当前卡片多远；detail：详细信息显示多少（0~1）。"""
        w = self.CARD_W
        k = min(abs(d), 1.0)
        p.setPen(Qt.PenStyle.NoPen)
        pressed = self._press_t if index == self._current and k == 0 else 0.0
        p.setBrush(QColor(0, 0, 0, round(40 * (1 - 0.5 * pressed))))
        p.drawRoundedRect(QRectF(1, 3 - pressed, w, h), 12, 12)  # 阴影：按下去时变淡变短，像贴近了桌面
        # 离得越远底色越灰、边框越深：在浅色桌面上也看得出一层层的
        p.setPen(QPen(blend("#C8C8C8", "#A8ADB3", k), 1))
        p.setBrush(blend("#FFFFFF", "#E4E7EB", k))
        body = QRectF(0.5, 0.5, w - 1, h - 1)
        p.drawRoundedRect(body, 12, 12)

        # 文字只画在当前这张上；切换途中随距离淡出，后面露出来的边就不会有杂乱的文字
        text_opacity = 1.0 - k
        if text_opacity <= 0.01:
            return
        p.setOpacity(p.opacity() * text_opacity)
        clip = QPainterPath()
        clip.addRoundedRect(body, 12, 12)
        p.setClipPath(clip)  # 文字只画在卡片里面：展开到一半时，详细信息不会溢出卡片

        pad = self.PAD
        p.setFont(self._small_font)
        p.setPen(QColor("#9AA0A6"))
        if len(self._cards) > 1:                                # 右上角："2 / 5"
            p.drawText(QRectF(w - pad - 40, pad - 2, 40, 16), Qt.AlignmentFlag.AlignRight,
                       f"{index + 1} / {len(self._cards)}")

        text_w = w - 2 * pad - 30
        bubble_h = self._text_height(card.bubble, self._bubble_font, text_w,
                                     None if detail > 0 else self.BUBBLE_LINES)
        p.setFont(self._bubble_font)
        p.setPen(QColor("#222222"))
        p.drawText(QRectF(pad, pad, text_w, bubble_h), Qt.TextFlag.TextWordWrap, card.bubble)

        p.setFont(self._small_font)
        base_opacity = p.opacity()
        fm = QFontMetrics(self._small_font)

        # 收起时底部那行"发件人 · 时间"（右边是邮箱小标签）：展开过程中先淡出
        footer = 1.0 - min(detail / 0.35, 1.0)
        if footer > 0.01:
            p.setOpacity(base_opacity * footer)
            line_y = h - pad - fm.lineSpacing()
            tag_w = fm.horizontalAdvance(card.mailbox) + 12 if card.mailbox else 0
            if tag_w:
                tag = QRectF(w - pad - tag_w, line_y + (fm.lineSpacing() - fm.height() - 2) / 2, tag_w, fm.height() + 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#F1F3F4"))
                p.drawRoundedRect(tag, 6, 6)
                p.setPen(QColor("#5F6368"))
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, card.mailbox)
            p.setPen(QColor("#80868B"))
            line = card.sender + (f" · {_short_time(card.date)}" if card.date else "")
            text_w = w - 2 * pad - (tag_w + 6 if tag_w else 0)
            line = fm.elidedText(line, Qt.TextElideMode.ElideRight, int(text_w))  # 太长就用…截断
            p.drawText(QRectF(pad, line_y, text_w, fm.lineSpacing()), 0, line)

        # 展开后的详细信息：卡片长到一定大小后才渐渐浮现，并且稍微从下往上"浮"出来
        shown = max(0.0, (detail - 0.35) / 0.65)
        if shown <= 0.01:
            return
        p.setOpacity(base_opacity * shown)
        y = pad + bubble_h + 6 + (1 - shown) * 6
        p.setPen(QPen(QColor("#E8EAED"), 1))
        p.drawLine(int(pad), int(y), int(w - pad), int(y))
        y += 8
        p.setPen(QColor("#5F6368"))
        for line in self._detail_lines(card):
            lh = self._text_height(line, self._small_font, w - 2 * pad)
            p.drawText(QRectF(pad, y, w - 2 * pad, lh), Qt.TextFlag.TextWordWrap, line)
            y += lh + 4

    # ---------- 交互 ----------

    def _card_at(self, pos):
        """pos（窗口坐标）点中了第几张卡片；没点中返回 None。从最近的往最远的找。"""
        h = self._collapsed_height()
        front = self._current_front_rect()
        if front.contains(pos):
            return self._current
        base = QRectF(front.x(), front.y(), self.CARD_W, h)
        for i, d, opacity in reversed(self._visible_cards()):
            rect, _ = self._card_rect(d, h, base)
            if opacity > 0.05 and rect.contains(pos):
                return i
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._cards or self.animating:
            return
        self._pressed_index = self._card_at(event.position())
        if self._pressed_index == self._current:
            self._animate_press(1.0)    # 按下：微微下沉

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._pressed_index is None:
            return
        pressed, self._pressed_index = self._pressed_index, None
        released_on = self._card_at(event.position())
        if pressed == self._current:
            self._animate_press(0.0)    # 松开：弹回来
            if released_on == self._current:
                self.toggle_expanded()  # 同时平滑展开 / 缩回
        elif released_on == pressed:
            self.go_to(pressed)         # 点了后面露出来的卡片：跳过去

    def wheelEvent(self, event):
        self._wheel_accum += event.angleDelta().y()
        while abs(self._wheel_accum) >= 120:
            step = 1 if self._wheel_accum < 0 else -1   # 往下滚：看下一封（更旧的）；往上滚：看上一封
            self._wheel_accum += 120 * step
            self.go_to(self._current + step)
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

    # ---------- 动画回调 ----------

    def _animate_press(self, target):
        animate(self._press_anim, self._press_t, target, self.PRESS_MS if target > 0 else self.PRESS_MS + 40)

    def _on_press_progress(self, value):
        self._press_t = value
        self.update()

    def _on_scroll(self, value):
        self._pos = value
        self.update()

    def _on_morph(self, value):
        self._morph_v = value
        self.update()

    def _on_morph_finished(self):
        self.setGeometry(self._morph_end_geometry)  # 动画结束：窗口收成最终大小
        if self._relayout_pending:
            self._relayout_pending = False
            self._relayout()                       # 动画期间来了新卡片 / 芸芸移动了：现在补上
        self.update()


def _lerp_rect(a, b, t):
    """两个矩形之间按比例插值：t=0 是 a，t=1 是 b。"""
    return QRectF(
        a.x() + (b.x() - a.x()) * t,
        a.y() + (b.y() - a.y()) * t,
        a.width() + (b.width() - a.width()) * t,
        a.height() + (b.height() - a.height()) * t,
    )


def _short_time(date):
    """今天的只显示时:分，其他日子显示 月-日 时:分。"""
    local = date.astimezone()
    if local.date() == datetime.now().astimezone().date():
        return f"{local:%H:%M}"
    return f"{local:%m-%d %H:%M}"
