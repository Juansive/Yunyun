"""助手模式的卡片堆：一问一答一张卡片，放在"芸芸 + 输入框"这一列的旁边。

    2 / 5                 ＋ 新对话      ← 顶上一行：第几张；鼠标移上来才浮现"新对话"
        ┌──────────────┐               ← 更新的一张：缩小、淡化，藏到右上后方
      ┌─┴────────────┐ │
      │ 你：问题       │─┘              ← 当前这张
      │ 回答的开头……   │
      │ 14:20 · $0.01 │
      └┬────────────┬┘                 ← 更早的：往下错开、越来越淡
       └────────────┘

- 最新的在最前面；滚轮往下翻是更早的。新卡片来了：从右上方滑到最前面，原来那张退到后面。
- 新对话 / 大脑重启时，插一张淡色的分隔卡"新对话 · 14:20"：芸芸只记得它之后的内容。
- 单击当前卡片 → card_clicked（阅读框，U3）；单击露出来的别的卡片 → 翻到那张。
- 窗口不拿键盘焦点（WindowDoesNotAcceptFocus）：看卡片时输入框照样能打字。
- 所有卡片画在这一个窗口里，错位、缩放、淡化都按一个连续的"翻到第几张"来算，翻动画才顺。
"""
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from yunyun import i18n
from yunyun.pet import winapi
from yunyun.pet.bubble import elide_lines, place_block


@dataclass(eq=False)
class AssistantCard:
    """一张卡片要显示的内容。kind = "turn"（一问一答）或 "divider"（新对话的分隔卡，question 里放工作文件夹的名字）。"""
    kind: str
    time: datetime
    question: str = ""
    answer: str = ""        # Markdown 原文（阅读框用）
    summary: str = ""       # 纯文字（卡片上显示开头几行）
    cost: str = ""          # "$0.0123" / "120 / 30 token"
    stopped: bool = False
    error: str = ""         # 出错时给人看的话
    files: tuple = ()       # 这一问里芸芸改了的文件（撤销用）
    checkpoint: str = ""    # 撤销这一问的改动要用的标记（session.rewind）
    generation: int = 0     # 是哪一个大脑会话（session.generation）里的：换了会话就撤销不了了
    undone: bool = False    # 已经撤销了
    actions: tuple = ()     # 操作记录（session.Action）：这一问芸芸用过哪些工具、怎么定的

    @classmethod
    def divider(cls, folder=""):
        return cls("divider", datetime.now(), folder)


def _font(size, bold=False):
    font = QFont("Microsoft YaHei", size)
    font.setBold(bold)
    return font


def _with_alpha(color, alpha):
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return c


class AssistantDeck(QWidget):
    card_clicked = Signal(object)            # 单击了当前卡片（AssistantCard）
    new_conversation_requested = Signal()    # 点了"+ 新对话"
    current_changed = Signal(int)            # 翻到了另一张（或者最前面换成了新卡片）；阅读框跟着换

    CARD_W = 290
    PAD = 14
    ANSWER_LINES = 3
    MARGIN = 10          # 窗口四周留白（给阴影）
    HEADER = 26          # 顶上一行（第几张、新对话）
    BEHIND_DY = 10       # 更早的卡片每张往下错开多少
    PREV_DX = 14         # 更新的那张往右错开
    PREV_DY = 12         # 更新的那张往上错开
    SHRINK = 0.05        # 每隔一张缩小多少
    GAP = 14             # 卡片堆和"芸芸 + 输入框"之间的距离
    SCROLL_MS = 300
    FADE_MS = 220
    SLIDE = 10           # 出现时从芸芸那边滑出来多远
    PRESS_SCALE = 0.03

    def __init__(self, owner, cards):
        """cards：所有卡片（从旧到新），和助手模式共用同一个 list；显示时最新的在最前面。"""
        super().__init__(owner)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._cards = cards
        self._q_font, self._a_font, self._small = _font(10, bold=True), _font(10), _font(9)
        self._card_h = self._measure_card_height()

        self._pos = 0.0               # 翻到第几张（0 = 最新）；动画里是小数
        self._target = 0
        self._shown = 0.0             # 出现进度（0 看不见，1 完全出现）
        self._hiding = False
        self._hover_header = 0.0      # "＋ 新对话"浮现的进度
        self._hovering = False
        self._pressed = None          # 按下的是第几张
        self._press_t = 0.0
        self._anchor = QRect()
        self._placement = None

        self._scroll = QVariantAnimation(self, duration=self.SCROLL_MS, easingCurve=QEasingCurve.Type.OutCubic)
        self._scroll.valueChanged.connect(self._on_scroll)
        self._fade = QVariantAnimation(self, duration=self.FADE_MS, easingCurve=QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade)
        self._fade.finished.connect(self._on_fade_finished)
        self._header_anim = QVariantAnimation(self, duration=160)
        self._header_anim.valueChanged.connect(self._on_header)
        self._press = QVariantAnimation(self, duration=90)
        self._press.valueChanged.connect(self._on_press)
        self._wheel = 0

        m = self.MARGIN
        self.resize(2 * m + self.CARD_W + self.PREV_DX,
                    2 * m + self.HEADER + self.PREV_DY + self._card_h + 2 * self.BEHIND_DY)

    # ---------- 对外 ----------

    @property
    def current_index(self):
        return self._target

    @property
    def card_count(self):
        return len(self._cards)

    def cards_rect(self):
        """卡片占的区域（屏幕坐标，不含四周给阴影的留白）。"""
        m = self.MARGIN
        return self.geometry().adjusted(m, m, -m, -m)

    def card(self, index):
        """第 index 张（0 = 最新）。"""
        return self._cards[-1 - index]

    def show_beside(self, column):
        """在 column（"芸芸 + 输入框"那一列，屏幕坐标）旁边淡入；没有卡片就不显示。"""
        if not self._cards:
            return
        self.follow(column)
        self._hiding = False
        if not self.isVisible():
            self._shown = 0.0
            with winapi.keep_z_order(int(self.parentWidget().winId())) if self.parentWidget() else _nothing():
                self.show()
        self._animate(self._fade, self._shown, 1.0)

    def hide_animated(self):
        if self.isVisible() and not self._hiding:
            self._hiding = True
            self._animate(self._fade, self._shown, 0.0)

    def close_now(self):
        self._fade.stop()
        self._hiding = False
        self.hide()

    def follow(self, column):
        self._anchor = QRect(column)
        m = self.MARGIN
        w, h = self.width() - 2 * m, self.height() - 2 * m
        x, y, self._placement = place_block(self._anchor, w, h, gap=self.GAP, current=self._placement, prefer="side")
        self.move(int(x) - m, int(y) - m)

    def newest_added(self):
        """最新一张刚加进来：从"更新的那张"的位置（右上后方）滑到最前面；在翻看旧卡片时不打扰，只是计数变了。"""
        if self._target == 0:
            self._pos = 1.0               # 原来最前面那张现在是第 1 张：先停在它，再滑到新的
            self.go_to(0)
            self.current_changed.emit(0)
        else:
            self._target += 1             # 还看着原来那张
            self._pos += 1
            self.update()

    def go_to(self, index):
        index = max(0, min(index, len(self._cards) - 1))
        changed = index != self._target
        self._target = index
        self._animate(self._scroll, self._pos, float(index))
        if changed:
            self.current_changed.emit(index)

    def reset(self):
        """卡片全换了（比如测试）：回到最新一张，不要动画。"""
        self._scroll.stop()
        self._pos, self._target = 0.0, 0
        self.update()

    # ---------- 动画 ----------

    @staticmethod
    def _animate(animation, start, end):
        animation.stop()
        animation.setStartValue(float(start))
        animation.setEndValue(float(end))
        animation.start()

    def _on_scroll(self, value):
        self._pos = float(value)
        self.update()

    def _on_fade(self, value):
        self._shown = float(value)
        self.update()

    def _on_fade_finished(self):
        if self._hiding:
            self._hiding = False
            self.hide()

    def _on_header(self, value):
        self._hover_header = float(value)
        self.update()

    def _on_press(self, value):
        self._press_t = float(value)
        self.update()

    # ---------- 尺寸和位置 ----------

    def _measure_card_height(self):
        q, a, s = QFontMetrics(self._q_font), QFontMetrics(self._a_font), QFontMetrics(self._small)
        return self.PAD + q.height() + 6 + a.lineSpacing() * self.ANSWER_LINES + 8 + s.height() + self.PAD

    def _front_rect(self):
        m = self.MARGIN
        slide = self.SLIDE * (1 - self._shown) * (1 if self._placement == "left" else -1)   # 从芸芸那边滑出来
        return QRectF(m + slide, m + self.HEADER + self.PREV_DY, self.CARD_W, self._card_h)

    def _card_rect(self, d):
        """离当前这张 d 张的卡片画在哪（d > 0 更早，往下错开；d < 0 更新，往右上缩到后面）。"""
        front = self._front_rect()
        scale = 1 - self.SHRINK * min(abs(d), 3)
        w, h = front.width() * scale, front.height() * scale
        if d >= 0:
            bottom = front.bottom() + self.BEHIND_DY * min(d, 2.5)
            return QRectF(front.center().x() - w / 2, bottom - h, w, h)
        right = front.right() + self.PREV_DX * min(-d, 1.2)
        top = front.top() - self.PREV_DY * min(-d, 1.2)
        return QRectF(right - w, top, w, h)

    @staticmethod
    def _opacity(d):
        if d >= 0:
            return max(0.0, 1 - 0.42 * d) if d <= 2.4 else 0.0
        return max(0.0, 1 - 0.5 * -d) if -d <= 1.5 else 0.0

    def _visible(self):
        """[(卡片序号, d)]，从最远的画到最近的。"""
        items = []
        for index in range(len(self._cards)):
            d = index - self._pos
            if self._opacity(d) > 0.01:
                items.append((index, d))
        return sorted(items, key=lambda item: (-abs(item[1]), item[1] < 0))

    def _new_chat_rect(self):
        metrics = QFontMetrics(self._small)
        text = i18n.tr("assistant_new_chat")
        w = metrics.horizontalAdvance(text) + 18
        m = self.MARGIN
        return QRectF(m + self.CARD_W - w, m + 2, w, self.HEADER - 6)

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        if not self._cards or self._shown <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self._shown)
        self._paint_header(p)
        for index, d in self._visible():
            self._paint_card(p, index, d)

    def _paint_header(self, p):
        m = self.MARGIN
        p.setFont(self._small)
        p.setPen(QColor("#8A94A6"))
        counter = f"{self._target + 1} / {len(self._cards)}"
        p.drawText(QRectF(m + 4, m, 120, self.HEADER - 2), Qt.AlignmentFlag.AlignVCenter, counter)
        if self._hover_header > 0:
            rect = self._new_chat_rect()
            p.save()
            p.setOpacity(self._shown * self._hover_header)
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0, QColor("#E3F0FF"))
            gradient.setColorAt(1, QColor("#ECE8FF"))
            p.setPen(QPen(QColor("#C9D8F5"), 1))
            p.setBrush(QBrush(gradient))
            p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            p.setPen(QColor("#4C6FD8"))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, i18n.tr("assistant_new_chat"))
            p.restore()

    def _paint_card(self, p, index, d):
        card = self.card(index)
        rect = self._card_rect(d)
        if index == self._pressed and abs(d) < 0.5:
            s = self.PRESS_SCALE * self._press_t
            rect = rect.adjusted(rect.width() * s / 2, rect.height() * s / 2 + 1.5 * self._press_t,
                                 -rect.width() * s / 2, -rect.height() * s / 2 + 1.5 * self._press_t)
        alpha = self._opacity(d)
        radius = 12
        p.save()
        # 阴影：几层越外越淡的圆角矩形，往下偏一点
        for i in range(4, 0, -1):
            shadow = rect.adjusted(-i, -i + 2, i, i + 2)
            path = QPainterPath()
            path.addRoundedRect(shadow, radius + i, radius + i)
            p.fillPath(path, QColor(30, 40, 70, int(9 * alpha * (5 - i) / 4)))
        body = QPainterPath()
        body.addRoundedRect(rect, radius, radius)
        far = min(1.0, abs(d))   # 越远越灰
        base = QColor("#F4F6FA") if card.kind == "divider" else QColor("#FFFFFF")
        p.fillPath(body, _with_alpha(_blend(base, QColor("#E9ECF2"), far * 0.7), 0.97 * alpha))
        p.setPen(QPen(_with_alpha(QColor("#DDE3EE"), alpha), 1))
        p.drawPath(body)

        text_alpha = max(0.0, 1 - abs(d) * 1.6)   # 只有当前这张（和翻动中的邻居）写字
        if text_alpha > 0:
            p.setOpacity(self._shown * text_alpha)
            if card.kind == "divider":
                self._paint_divider(p, rect, card)
            else:
                self._paint_turn(p, rect, card)
        p.restore()

    def _paint_divider(self, p, rect, card):
        p.setFont(self._small)
        text = i18n.tr("assistant_divider", f"{card.time:%H:%M}")
        metrics = QFontMetrics(self._small)
        if card.question:   # 在哪个工作文件夹里
            text = metrics.elidedText(f"{text} · {card.question}", Qt.TextElideMode.ElideMiddle,
                                      int(rect.width() - 2 * self.PAD - 40))
        tw = metrics.horizontalAdvance(text)
        cy = rect.center().y()
        p.setPen(QPen(QColor("#C3CBDA"), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(rect.left() + self.PAD, cy), QPointF(rect.center().x() - tw / 2 - 10, cy))
        p.drawLine(QPointF(rect.center().x() + tw / 2 + 10, cy), QPointF(rect.right() - self.PAD, cy))
        p.setPen(QColor("#8A94A6"))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_turn(self, p, rect, card):
        x, w = rect.left() + self.PAD, rect.width() - 2 * self.PAD
        # 左边一道蓝紫渐变的细条：助手的卡片（和邮件卡片区分开）
        bar = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        bar.setColorAt(0, QColor("#4FA8FF"))
        bar.setColorAt(1, QColor("#8E7CFF"))
        p.fillRect(QRectF(rect.left() + 1, rect.top() + 12, 3, rect.height() - 24), QBrush(bar))

        y = rect.top() + self.PAD
        q = QFontMetrics(self._q_font)
        p.setFont(self._q_font)
        p.setPen(QColor("#374151"))
        question = q.elidedText(i18n.tr("assistant_card_you", card.question.replace("\n", " ")),
                                Qt.TextElideMode.ElideRight, int(w))
        p.drawText(QRectF(x, y, w, q.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, question)
        y += q.height() + 6

        a = QFontMetrics(self._a_font)
        body_h = a.lineSpacing() * self.ANSWER_LINES
        p.setFont(self._a_font)
        if card.summary:
            p.setPen(QColor("#1F2937"))
            text = elide_lines(card.summary, self._a_font, int(w), self.ANSWER_LINES)
        else:
            p.setPen(QColor("#C62828") if card.error else QColor("#9AA3B2"))
            text = elide_lines(card.error or i18n.tr("assistant_card_empty"), self._a_font, int(w), self.ANSWER_LINES)
        p.drawText(QRectF(x, y, w, body_h), Qt.TextFlag.TextWordWrap, text)
        y += body_h + 8

        s = QFontMetrics(self._small)
        p.setFont(self._small)
        parts = [f"{card.time:%H:%M}"] + ([card.cost] if card.cost else [])
        if card.files:
            parts.append(i18n.tr("assistant_card_files", len(card.files)))
        p.setPen(QColor("#8A94A6"))
        p.drawText(QRectF(x, y, w, s.height()), Qt.AlignmentFlag.AlignLeft, " · ".join(parts))
        status = i18n.tr("assistant_card_stopped") if card.stopped else (
            i18n.tr("assistant_card_error") if card.error else i18n.tr("assistant_card_undone") if card.undone else "")
        if status:
            p.setPen(QColor("#C62828") if card.error and not card.stopped else QColor("#B7791F"))
            p.drawText(QRectF(x, y, w, s.height()), Qt.AlignmentFlag.AlignRight, status)

    # ---------- 鼠标 ----------

    def _card_at(self, pos):
        """鼠标下面是第几张（只认看得见的，近的优先）。"""
        for index, d in reversed(self._visible()):
            if self._card_rect(d).contains(QPointF(pos)):
                return index
        return None

    def mouseMoveEvent(self, event):
        self._set_hover(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._card_at(event.position()) is not None
                       or self._new_chat_rect().contains(event.position()) else Qt.CursorShape.ArrowCursor)

    def enterEvent(self, event):
        self._set_hover(True)

    def leaveEvent(self, event):
        self._set_hover(False)

    def _set_hover(self, hovering):
        if hovering != self._hovering:
            self._hovering = hovering
            self._animate(self._header_anim, self._hover_header, 1.0 if hovering else 0.0)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._hover_header > 0.5 and self._new_chat_rect().contains(event.position()):
            self.new_conversation_requested.emit()
            return
        self._pressed = self._card_at(event.position())
        if self._pressed == self._target:
            self._animate(self._press, self._press_t, 1.0)

    def mouseReleaseEvent(self, event):
        pressed, self._pressed = self._pressed, None
        self._animate(self._press, self._press_t, 0.0)
        if pressed is None or self._card_at(event.position()) != pressed:
            return   # 按下后拖到外面松开 = 取消
        if pressed == self._target:
            self.card_clicked.emit(self.card(pressed))
        else:
            self.go_to(pressed)

    def wheelEvent(self, event):
        self._wheel += event.angleDelta().y()
        while abs(self._wheel) >= 120:   # 触摸板一点点滚，攒够一格再翻
            step = 1 if self._wheel < 0 else -1
            self._wheel += 120 * step
            self.go_to(self._target + step)

    def hideEvent(self, event):
        self._hovering = False
        self._hover_header = 0.0
        super().hideEvent(event)


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _blend(a, b, t):
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )
