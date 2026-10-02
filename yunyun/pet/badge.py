"""小红点：显示待处理的新邮件数，放在芸芸身体的右上角。

- 左键单击 → clicked（处理邮件）
- 按住拖走、松手 → "啵"地消失 → dismissed（这批邮件不处理，计数清零）；没拖够远就弹回原位
- 右键 → hide_requested（暂时隐藏）

和气泡一样，它是芸芸的"从属窗口"，和芸芸在同一层。
"""
from PySide6.QtCore import (
    QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class Badge(QWidget):
    HEIGHT = 18             # 红点高度（逻辑像素）：小小一个
    PAD = 5                 # 窗口四周留白，给"啵"的放大动画留空间
    INSET = 12              # 红点中心离芸芸身体右上角往里缩多少
    DISMISS_DISTANCE = 60   # 拖出多远松手才算"拖走"
    COLOR = "#E53935"

    clicked = Signal()
    dismissed = Signal()
    hide_requested = Signal()

    def __init__(self, owner):
        super().__init__(owner)  # owner = 芸芸窗口：成为它的从属窗口
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._font = QFont("Segoe UI", 8, QFont.Weight.Bold)

        self._text = ""
        self._anchor = None        # 最近一次芸芸身体的区域，用来算红点该在哪
        self._press_pos = None     # 鼠标按下时的屏幕坐标
        self._home = QPoint()      # 拖动开始前红点窗口的位置
        self._dragging = False
        self._pop = 0.0            # "啵"动画进度 0~1

        self._pop_anim = QVariantAnimation(self)
        self._pop_anim.setStartValue(0.0)
        self._pop_anim.setEndValue(1.0)
        self._pop_anim.setDuration(200)
        self._pop_anim.valueChanged.connect(self._on_pop_progress)
        self._pop_anim.finished.connect(self._on_pop_finished)

        self._snap_anim = QPropertyAnimation(self, b"pos", self)  # 动画直接改窗口的 pos 属性
        self._snap_anim.setDuration(180)
        self._snap_anim.setEasingCurve(QEasingCurve.Type.OutBack)  # 弹回时稍微过冲一点，像橡皮筋

        self.set_count(0)

    @property
    def busy(self):
        """正在被拖动或正在播放动画：这时不要去挪它。"""
        return self._dragging or self._pop_anim.state() == QVariantAnimation.State.Running \
            or self._snap_anim.state() == QPropertyAnimation.State.Running

    def set_count(self, count):
        self._text = "99+" if count > 99 else str(count)
        width = max(self.HEIGHT, self._text_width() + 10)  # 一位数是圆形，多位数变成胶囊形
        self.resize(width + 2 * self.PAD, self.HEIGHT + 2 * self.PAD)
        if self._anchor is not None:
            self.follow(self._anchor)
        self.update()

    def follow(self, body_rect):
        """放到芸芸身体（body_rect，屏幕坐标）的右上角，并保证红点不出屏幕。"""
        self._anchor = body_rect
        if self.busy:
            return
        center = QPoint(body_rect.right() - self.INSET, body_rect.top() + self.INSET)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pad = self.PAD
        x = center.x() - self.width() // 2
        y = center.y() - self.height() // 2
        # 夹的是红点本身（不含透明留白），所以上下左右都扣掉 pad
        x = max(area.left() - pad, min(x, area.left() + area.width() - self.width() + pad))
        y = max(area.top() - pad, min(y, area.top() + area.height() - self.height() + pad))
        self.move(x, y)

    # ---------- 绘制 ----------

    def _text_width(self):
        return QFontMetrics(self._font).horizontalAdvance(self._text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1.0 - self._pop)                  # "啵"：越来越透明
        scale = 1.0 + 0.4 * self._pop                         # 同时放大一点
        c = QRectF(self.rect()).center()
        painter.translate(c)
        painter.scale(scale, scale)
        painter.translate(-c)

        body = QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        r = body.height() / 2
        painter.setPen(QPen(QColor("white"), 1.5))           # 白边：在深色背景上也看得清
        painter.setBrush(QColor(self.COLOR))
        painter.drawRoundedRect(body, r, r)
        painter.setPen(QColor("white"))
        painter.setFont(self._font)
        painter.drawText(body, Qt.AlignmentFlag.AlignCenter, self._text)

    # ---------- 鼠标 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.busy:
            self._press_pos = event.globalPosition().toPoint()
            self._home = self.pos()

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if not self._dragging and delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True  # 移动超过系统拖动阈值才算拖，防止单击时手抖
        if self._dragging:
            self.move(self._home + delta)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        self._press_pos = None
        if not self._dragging:
            self.clicked.emit()
            return
        self._dragging = False
        if (self.pos() - self._home).manhattanLength() >= self.DISMISS_DISTANCE:
            self._pop_anim.start()         # 拖得够远：啵！
        else:
            self._snap_anim.setStartValue(self.pos())
            self._snap_anim.setEndValue(self._home)
            self._snap_anim.start()        # 不够远：弹回去

    def contextMenuEvent(self, event):
        self.hide_requested.emit()

    # ---------- 动画回调 ----------

    def _on_pop_progress(self, value):
        self._pop = value
        self.update()

    def _on_pop_finished(self):
        self.hide()
        self._pop = 0.0
        self.move(self._home)  # 回到原位（下次出现时在芸芸身边）
        self.dismissed.emit()
