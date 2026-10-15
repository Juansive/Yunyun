"""始终在听（阶段 7.3）开着时，芸芸身体左上角的小麦克风标记（2026-10-11 用户要的：一眼看得出芸芸在听）。
"接着说"的窗口开着时（7.9），外面一圈绿条随剩下的时间慢慢变短（2026-10-14 用户定的）。

芸芸的从属窗口（跟着走、和芸芸同一层），只是个标记：鼠标穿过它点到后面（不挡芸芸、不挡别的程序）。
小红点在右上角，它在左上角，不会叠在一起。显示时要包 winapi.keep_z_order（见 PetWindow.show_mic_mark）。
"""
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class MicMark(QWidget):
    SIZE = 22       # 圆圈直径（逻辑像素）
    RING = 5        # 外面那圈倒数的绿条离圆圈多远（也是窗口四周多留的边）
    INSET = 12      # 圆心离芸芸身体左上角往里缩多少
    COLOR = "#43A047"   # 绿色：在听（和"在录音"的红色区分开，不吓人）

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput)
        self.setFixedSize(self.SIZE + 2 + 2 * self.RING, self.SIZE + 2 + 2 * self.RING)
        self.left = 0.0   # 倒数还剩多少（1 → 0；0 = 没在倒数，不画绿条）
        self._countdown = QVariantAnimation(self, startValue=1.0, endValue=0.0, valueChanged=self._tick)

    def follow(self, body_rect):
        center = QPoint(body_rect.left() + self.INSET, body_rect.top() + self.INSET)
        self.move(center - QPoint(self.width() // 2, self.height() // 2))

    def countdown(self, seconds):
        """开始倒数 seconds 秒（绿条从满圈慢慢变短）；0 = 停下、绿条收起；负的 = 满圈停着不动（你在说话）。"""
        self._countdown.stop()
        self._tick(1.0 if seconds != 0 else 0.0)
        if seconds > 0:
            self._countdown.setDuration(int(seconds * 1000))
            self._countdown.start()

    def _tick(self, value):
        self.left = value
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s, r = self.SIZE, self.RING
        if self.left > 0:
            ring = QRectF(2, 2, s + 2 * r - 2, s + 2 * r - 2)
            pen = QPen(QColor(255, 255, 255, 170), 3.5)   # 白色底衬：什么背景上都看得清
            p.setPen(pen)
            p.drawEllipse(ring)
            pen = QPen(QColor(self.COLOR), 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(ring, 90 * 16, int(self.left * 360 * 16))   # 从 12 点钟开始，剩下多少画多少
        p.translate(r, r)
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
