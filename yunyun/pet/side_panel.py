"""助手模式里"从旁边滑出来的白色面板"的公共部分：阅读框（assistant_reader）和确认卡片（assistant_confirm）都用它。

- 放在 anchor（芸芸 + 输入框 + 卡片堆占的区域）旁边，哪边空间大放哪边（bubble.place_block）；
- 从 anchor 那边滑出、淡入，收起时反过来；
- 焦点去了别的程序 → dismissed（总管决定要不要理：现在卡片常亮，不理）；
- 按住空白处（四周留白、各行之间）拖 → drag_started / dragged(移动了多少) / drag_finished，由总管挪整组（卡片堆 + 它）；
- 白底圆角 + 柔和的阴影，边框颜色可以换（border_color）。

子类负责内容和大小（resize 之后调 _place / _apply_open），出现时调 _show_animated()。
"""
from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from yunyun.pet.bubble import animate, place_block


class SidePanel(QWidget):
    dismissed = Signal()   # 焦点去了别的程序
    drag_started = Signal()
    dragged = Signal(QPoint)   # 从按下的地方起一共移动了多少（屏幕坐标）
    drag_finished = Signal()

    MARGIN = 12          # 窗口四周留白（给阴影）
    PAD = 18
    GAP = 14
    SLIDE = 22           # 滑出来的距离
    OPEN_MS = 280

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Microsoft YaHei", 10))
        self.border_color = QColor("#DDE3EE")
        self._anchor = QRect()
        self._placement = None
        self._target_pos = QPoint()
        self._shown = 0.0
        self._hiding = False
        self._press_at = None   # 在空白处按下的位置：移动超过阈值就算拖动
        self._dragging = False
        self._open = QVariantAnimation(self, duration=self.OPEN_MS, easingCurve=QEasingCurve.Type.OutCubic)
        self._open.valueChanged.connect(self._on_open)
        self._open.finished.connect(self._on_open_finished)

    # ---------- 对外 ----------

    @property
    def is_open(self):
        return self.isVisible() and not self._hiding

    def hide_animated(self):
        if self.isVisible() and not self._hiding:
            self._hiding = True
            animate(self._open, self._shown, 0.0)

    def close_now(self):
        self._open.stop()
        self._hiding = False
        self.hide()

    def follow(self, anchor):
        self._anchor = QRect(anchor)
        if self.isVisible():
            self._place()
            self._apply_open()

    def shift(self, delta):
        """整体平移 delta（拖动卡片组时：保持和卡片堆的相对位置，不重新选边）。"""
        self._anchor.translate(delta)
        self._target_pos += delta
        if self.isVisible():
            self._apply_open()

    # ---------- 位置和动画 ----------

    def _show_animated(self):
        """内容和大小已经准备好：滑出来（已经开着就只是提到前面）。"""
        self._hiding = False
        self._place()
        if not self.isVisible():
            self._shown = 0.0
            self._apply_open()
            self.show()
        self.raise_()
        animate(self._open, self._shown, 1.0)

    def _place(self):
        m = self.MARGIN
        x, y, self._placement = place_block(
            self._anchor, self.width() - 2 * m, self.height() - 2 * m, gap=self.GAP, current=self._placement,
            prefer="side")
        self._target_pos = QPoint(int(x) - m, int(y) - m)

    def _on_open(self, value):
        self._shown = float(value)
        self._apply_open()

    def _apply_open(self):
        """从 anchor 那边滑出来：placement 是 right 就从左往右滑，left 就从右往左。"""
        direction = -1 if self._placement == "right" else 1 if self._placement == "left" else 0
        dy = 0 if direction else -self.SLIDE   # 放在上 / 下方时竖着滑
        offset = int(self.SLIDE * (1 - self._shown))
        self.move(self._target_pos + QPoint(direction * offset, int(dy * (1 - self._shown))))
        self.setWindowOpacity(self._shown)

    def _on_open_finished(self):
        if self._hiding:
            self._hiding = False
            self.hide()

    # ---------- 拖动（只有点在空白处才会到这里：文字、按钮自己处理鼠标） ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_at = event.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_at is not None and event.buttons() & Qt.MouseButton.LeftButton:
            moved = event.globalPosition().toPoint() - self._press_at
            if not self._dragging and moved.manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.drag_started.emit()
            if self._dragging:
                self.dragged.emit(moved)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_at = None
        if self._dragging:
            self._dragging = False
            self.unsetCursor()
            self.drag_finished.emit()
            return
        super().mouseReleaseEvent(event)

    # ---------- 事件 ----------

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.isVisible():
            QTimer.singleShot(150, self._check_focus_lost)
        super().changeEvent(event)

    def _check_focus_lost(self):
        if self.isVisible() and not self._hiding and QApplication.activeWindow() is None:
            self.dismissed.emit()

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
        p.setPen(self.border_color)
        p.drawPath(body)
