"""助手模式的输入框：芸芸脚下一个圆角输入条，四周一圈淡蓝色渐变光晕。

    ( 芸芸 )
 ╭─────────────────────────────╮
 │ 问芸芸点什么……           (↑) │   ← 光晕：平时静止；芸芸思考时沿边框缓缓流动
 ╰─────────────────────────────╯

- 出现：从一个小圆点横向"长"成输入条（带一点弹性），文字和按钮随后淡入；收起是反过来，越缩越快。
- 动画全部自己画：窗口一开始就是最终大小（四周留出光晕的透明边），动画里只改画的形状，不改窗口大小，所以不抖。
  只在动画进行 / 思考时刷新画面，平时不占 CPU。
- Enter 发送，Shift+Enter 换行，最多长到 MAX_LINES 行；回答时按钮变成"停止"，可以先打字但发不出去。
- Esc 或者点到别的程序 → 收起（collapsed 信号）；点芸芸自己的窗口不收起。
- 是芸芸的从属窗口（parent = 芸芸）：芸芸被挡住时它也被挡住。
"""
import math

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QApplication, QFrame, QGraphicsOpacityEffect, QTextEdit, QWidget

from yunyun import i18n
from yunyun.pet.bubble import place_block

# 光晕的颜色：天蓝 → 淡紫 → 青（思考时三种颜色绕着边框转）
GLOW_COLORS = (QColor("#5AB8FF"), QColor("#8E9BFF"), QColor("#63E1F5"))


def _mix(color, alpha):
    c = QColor(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


class _Edit(QTextEdit):
    """透明的多行输入：Enter 发送，Shift+Enter 换行，Esc 收起。"""

    submit = Signal()
    escape = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QTextEdit { background: transparent; color: #1F2937; }")
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        super().keyPressEvent(event)


class _SendButton(QAbstractButton):
    """圆形按钮：平时是"发送"（向上的箭头），回答时是"停止"（小方块）。"""

    SIZE = 30

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_mode = False

    def sizeHint(self):
        return QSize(self.SIZE, self.SIZE)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isDown():
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)   # 按下时微微缩小
        gradient = QLinearGradient(r.topLeft(), r.bottomRight())
        light = 18 if self.underMouse() else 0
        gradient.setColorAt(0, QColor("#4FA8FF").lighter(100 + light))
        gradient.setColorAt(1, QColor("#7C8CFF").lighter(100 + light))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawEllipse(r)

        c = r.center()
        p.setBrush(QColor("white"))
        if self.stop_mode:
            p.drawRoundedRect(QRectF(c.x() - 5, c.y() - 5, 10, 10), 2.5, 2.5)
        else:
            pen = QPen(QColor("white"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(c.x(), c.y() + 6), QPointF(c.x(), c.y() - 6))
            path = QPainterPath(QPointF(c.x() - 5, c.y() - 1))
            path.lineTo(c.x(), c.y() - 6)
            path.lineTo(c.x() + 5, c.y() - 1)
            p.drawPath(path)


class AssistantInput(QWidget):
    submitted = Signal(str)       # 按了 Enter / 发送（文字由接收方决定要不要清空：发出去了才清）
    stop_requested = Signal()     # 回答时按了"停止"
    collapsed = Signal()          # 收起动画结束、窗口已经隐藏

    WIDTH = 380        # 输入条宽度
    GLOW = 26          # 光晕向外延伸多远（窗口四周留出这么多透明边）
    DOT = 46           # 一行时输入条的高度，也是展开前"小圆点"的直径
    PAD_X = 18         # 文字到左边框
    MAX_LINES = 5
    GAP = 12           # 输入条到芸芸身体的距离
    OPEN_MS = 520
    CLOSE_MS = 260
    FLOW_MS = 2800     # 思考时光晕转一圈要多久
    GLOW_LAYERS = 10

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Microsoft YaHei", 10))

        self._edit = _Edit(self)
        self._edit.setFont(self.font())
        self._edit.setPlaceholderText(i18n.tr("assistant_input_placeholder"))
        self._edit.submit.connect(self._on_submit)
        self._edit.escape.connect(self.collapse)
        self._edit.textChanged.connect(self._fit)
        self._button = _SendButton(self)
        self._button.clicked.connect(self._on_button)
        self._button.setToolTip(i18n.tr("assistant_send"))

        self._t = 0.0                  # 展开进度：0 = 小圆点（看不见），1 = 完全展开
        self._closing = False
        self._busy = False
        self._thinking = False
        self._phase = 0.0              # 思考时光晕转到的角度
        self._pill_h = self.DOT
        self._anchor = QRect()
        self._placement = None
        self._open_curve = QEasingCurve(QEasingCurve.Type.OutBack)
        self._open_curve.setOvershoot(1.05)          # 弹性：稍微超出一点再回来（太大会显得晃）
        self._close_curve = QEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim = QVariantAnimation(self)         # 线性的 0→1，曲线在 _set_t 里自己套（宽度和透明度用不同的曲线）
        self._anim.valueChanged.connect(self._set_t)
        self._anim.finished.connect(self._on_anim_finished)
        self._flow = QVariantAnimation(self, startValue=0.0, endValue=360.0, duration=self.FLOW_MS, loopCount=-1)
        self._flow.valueChanged.connect(self._on_flow)

        self._effects = []
        self._fit()
        self._apply_t()

    # ---------- 对外 ----------

    @property
    def is_open(self):
        return self.isVisible() and not self._closing

    @property
    def text(self):
        return self._edit.toPlainText()

    def set_text(self, text):
        self._edit.setPlainText(text)

    def clear_text(self):
        self._edit.clear()

    def open(self, anchor):
        """在芸芸（anchor：身体的屏幕区域）脚下展开，并拿到键盘焦点。已经开着就只拿焦点。"""
        self._closing = False
        self.follow(anchor)
        if not self.isVisible():
            self._t = 0.0
            self._apply_t()
            self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()
        self._animate_to(1.0, self.OPEN_MS * (1.0 - self._t))

    def collapse(self):
        """收起（动画结束后隐藏、发 collapsed）。输入框里打了一半的字留着，下次展开还在。"""
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self._animate_to(0.0, self.CLOSE_MS * self._t)

    def close_now(self):
        """立刻隐藏，不要动画（退出助手模式时用）。"""
        self._anim.stop()
        self._closing = False
        self._t = 0.0
        self.hide()

    def follow(self, anchor):
        """芸芸移动了：跟过去（优先放在脚下，脚下放不下就放头顶，再不行放左右）。"""
        self._anchor = QRect(anchor)
        x, y, self._placement = place_block(
            self._anchor, self.WIDTH, self._pill_h, gap=self.GAP, current=self._placement, prefer_below=True)
        self.move(int(x) - self.GLOW, int(y) - self.GLOW)

    def set_busy(self, busy):
        """芸芸在回答：按钮变成"停止"，Enter 发不出去。"""
        self._busy = busy
        self._button.stop_mode = busy
        self._button.setToolTip(i18n.tr("assistant_stop" if busy else "assistant_send"))
        self._button.update()

    def set_thinking(self, thinking):
        """思考时光晕绕着边框流动。"""
        self._thinking = thinking
        if thinking and self.isVisible():
            self._flow.start()
        elif not thinking:
            self._flow.stop()
        self.update()

    def retranslate(self):
        self._edit.setPlaceholderText(i18n.tr("assistant_input_placeholder"))
        self.set_busy(self._busy)

    # ---------- 事件 ----------

    def _on_submit(self):
        if not self._busy and self.text.strip():
            self.submitted.emit(self.text)

    def _on_button(self):
        if self._busy:
            self.stop_requested.emit()
        else:
            self._on_submit()
        self._edit.setFocus()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.is_open:
            QTimer.singleShot(150, self._check_focus_lost)   # 等焦点落定：可能只是换到了芸芸自己的另一个窗口
        super().changeEvent(event)

    def _check_focus_lost(self):
        if self.is_open and QApplication.activeWindow() is None:   # 焦点去了别的程序
            self.collapse()

    def showEvent(self, event):
        super().showEvent(event)
        if self._thinking:
            self._flow.start()

    def hideEvent(self, event):
        self._flow.stop()
        super().hideEvent(event)

    # ---------- 尺寸 ----------

    def _fit(self):
        """按文字行数定输入条高度（1～MAX_LINES 行），窗口跟着变高。"""
        line = self._edit.fontMetrics().lineSpacing()
        self._edit.document().setTextWidth(self._edit_width())
        text_h = max(line, min(int(self._edit.document().size().height()), line * self.MAX_LINES))
        pad_y = (self.DOT - line) // 2
        pill_h = max(self.DOT, text_h + 2 * pad_y)
        g = self.GLOW
        self._edit.setGeometry(g + self.PAD_X, g + pad_y, self._edit_width(), text_h)
        b = _SendButton.SIZE
        self._button.move(g + self.WIDTH - (self.DOT + b) // 2, g + pill_h - (self.DOT + b) // 2)
        if pill_h != self._pill_h or self.size() != QSize(self.WIDTH + 2 * g, pill_h + 2 * g):
            self._pill_h = pill_h
            self.resize(self.WIDTH + 2 * g, pill_h + 2 * g)
            if not self._anchor.isNull():
                self.follow(self._anchor)
        self._edit.ensureCursorVisible()

    def _edit_width(self):
        return self.WIDTH - self.PAD_X - (self.DOT + _SendButton.SIZE) // 2 - 8

    # ---------- 动画 ----------

    def _animate_to(self, target, duration):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self._anim.setDuration(max(1, int(duration)))
        self._anim.start()

    def _set_t(self, value):
        self._t = float(value)
        self._apply_t()

    def _on_anim_finished(self):
        if self._closing:
            self._closing = False
            self.hide()
            self.collapsed.emit()

    def _on_flow(self, value):
        self._phase = float(value)
        self.update()

    def _content_opacity(self):
        """文字和按钮：输入条长到一半多才开始淡入（收起时先淡出）。"""
        return max(0.0, min(1.0, (self._t - 0.5) / 0.4))

    def _apply_t(self):
        opacity = self._content_opacity()
        visible = opacity > 0
        for widget in (self._edit, self._button):
            widget.setVisible(visible)
            if 0 < opacity < 1:
                effect = widget.graphicsEffect()
                if not isinstance(effect, QGraphicsOpacityEffect):
                    effect = QGraphicsOpacityEffect(widget)
                    widget.setGraphicsEffect(effect)
                effect.setOpacity(opacity)
            elif widget.graphicsEffect() is not None:
                widget.setGraphicsEffect(None)   # 动画结束就拿掉：平时不多一层绘制
        self.update()

    def _pill_rect(self):
        """按展开进度算输入条此刻的形状：宽度从小圆点长到 WIDTH，中心不动。"""
        curve = self._close_curve if self._closing else self._open_curve
        grow = curve.valueForProgress(self._t)
        width = self.DOT + (self.WIDTH - self.DOT) * grow
        width = max(self.DOT * 0.6, min(width, self.WIDTH + self.GLOW))   # 弹性超出时别画出窗口
        cx = self.GLOW + self.WIDTH / 2
        return QRectF(cx - width / 2, self.GLOW, width, self._pill_h)

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        alpha = min(1.0, self._t * 3)   # 整体先淡入（前三分之一的时间里）
        if alpha <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(alpha)
        pill = self._pill_rect()
        radius = min(pill.height() / 2, self.DOT / 2)
        self._paint_glow(p, pill, radius)

        body = QPainterPath()
        body.addRoundedRect(pill, radius, radius)
        p.fillPath(body, QColor(255, 255, 255, 248))
        p.setPen(QPen(self._glow_brush(pill, 150 if self._thinking else 90), 1.2))
        p.drawPath(body)

    def _paint_glow(self, p, pill, radius):
        """柔和的光晕：一圈圈往外扩、越外越淡的圆角矩形叠在一起（Qt 没有现成的模糊，叠几层效果差不多）。"""
        strength = 1.0
        if self._thinking:
            strength = 1.25 + 0.2 * math.sin(math.radians(self._phase * 2))   # 思考时更亮，还轻轻地一明一暗
        n = self.GLOW_LAYERS
        for i in range(n, 0, -1):
            spread = (self.GLOW - 4) * i / n
            alpha = 24 * strength * (1 - i / (n + 1)) ** 2
            rect = pill.adjusted(-spread, -spread, spread, spread)
            path = QPainterPath()
            path.addRoundedRect(rect, radius + spread, radius + spread)
            p.fillPath(path, self._glow_brush(rect, alpha))

    def _glow_brush(self, rect, alpha):
        if self._thinking:
            gradient = QConicalGradient(rect.center(), -self._phase)
            a, b, c = GLOW_COLORS
            for pos, color in ((0, a), (0.33, b), (0.66, c), (1, a)):
                gradient.setColorAt(pos, _mix(color, alpha))
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0, _mix(GLOW_COLORS[0], alpha))
            gradient.setColorAt(1, _mix(GLOW_COLORS[1], alpha))
        return QBrush(gradient)
