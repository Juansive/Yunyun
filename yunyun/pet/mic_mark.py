"""始终在听（阶段 7.3）开着时，芸芸身体左上角的小麦克风标记（2026-10-11 用户要的：一眼看得出芸芸在听）。

芸芸的从属窗口（跟着走、和芸芸同一层），只是个标记：鼠标穿过它点到后面（不挡芸芸、不挡别的程序）。
小红点在右上角，它在左上角，不会叠在一起。显示时要包 winapi.keep_z_order（见 PetWindow.show_mic_mark）。
"""
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class MicMark(QWidget):
    SIZE = 22       # 圆圈直径（逻辑像素）
    INSET = 12      # 圆心离芸芸身体左上角往里缩多少
    COLOR = "#43A047"   # 绿色：在听（和"在录音"的红色区分开，不吓人）

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput)
        self.setFixedSize(self.SIZE + 2, self.SIZE + 2)

    def follow(self, body_rect):
        center = QPoint(body_rect.left() + self.INSET, body_rect.top() + self.INSET)
        self.move(center - QPoint(self.width() // 2, self.height() // 2))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self.SIZE
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
        p.setBrush(QColor(self.COLOR))
        p.drawEllipse(QRectF(1, 1, s, s))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(1 + s * 0.38, 1 + s * 0.2, s * 0.24, s * 0.38), s * 0.12, s * 0.12)   # 话筒
        pen = QPen(QColor("white"), s * 0.07)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(1 + s * 0.3, 1 + s * 0.3, s * 0.4, s * 0.36), 0, -180 * 16)             # 托架
        p.drawLine(QPointF(1 + s * 0.5, 1 + s * 0.66), QPointF(1 + s * 0.5, 1 + s * 0.8))         # 支杆
        p.end()
