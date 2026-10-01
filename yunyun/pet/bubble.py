"""气泡组件：显示在芸芸头顶，文字自动换行，几秒后自动消失。

气泡是一个独立的顶层窗口（而不是芸芸窗口的一部分），
这样它的大小可以随文字变化，不用把芸芸的窗口撑大。
"""
from PySide6.QtCore import QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class Bubble(QWidget):
    MAX_TEXT_WIDTH = 220   # 文字区域最大宽度，超过就自动换行
    PADDING = 12           # 文字到气泡边框的内边距
    TAIL = 10              # 小尾巴（指向芸芸的三角）的高度
    RADIUS = 12            # 圆角半径
    GAP = 4                # 气泡尾巴尖到芸芸头顶的距离

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # 弹出时不抢走键盘焦点
        self.setFont(QFont("Microsoft YaHei", 10))

        self._text = ""
        self._text_rect = QRect()
        self._tail_x = 0  # 尾巴尖在气泡内的横坐标

        # 单次定时器：时间到了就隐藏气泡
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text, anchor, duration_ms=4000):
        """在 anchor（芸芸的屏幕区域，QRect）上方显示 text，duration_ms 毫秒后消失。"""
        self._text = text

        # 先量一下：在最大宽度内自动换行时，这段文字需要多大的区域
        self._text_rect = self.fontMetrics().boundingRect(
            QRect(0, 0, self.MAX_TEXT_WIDTH, 10000),
            Qt.TextFlag.TextWordWrap,
            text,
        )
        self.resize(
            self._text_rect.width() + 2 * self.PADDING,
            self._text_rect.height() + 2 * self.PADDING + self.TAIL,
        )

        self.follow(anchor)
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)  # 每次显示都重新计时

    def follow(self, anchor):
        """把气泡摆到 anchor 正上方，并保证左右不超出屏幕。"""
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        x = anchor.center().x() - self.width() // 2
        x = max(area.left(), min(x, area.right() - self.width()))  # 夹在屏幕左右边界之间
        y = anchor.top() - self.height() - self.GAP
        self.move(x, y)

        # 气泡被推到屏幕边上时，尾巴仍然要对准芸芸
        self._tail_x = anchor.center().x() - x
        self.update()  # 请求重绘（Qt 稍后会调用 paintEvent）

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 气泡主体：圆角矩形（偏移 0.5 像素让 1px 描边画在像素中间，更清晰）
        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - self.TAIL - 1)
        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)

        # 小尾巴：一个朝下的三角形，横向位置限制在圆角之间
        lo = self.RADIUS + self.TAIL
        hi = self.width() - self.RADIUS - self.TAIL
        tx = max(lo, min(self._tail_x, hi))
        tail = QPainterPath()
        tail.moveTo(tx - self.TAIL, body.bottom() - 1)
        tail.lineTo(tx, self.height() - 1)
        tail.lineTo(tx + self.TAIL, body.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)  # 合并成一个形状，描边才不会在接缝处断开

        painter.setPen(QPen(QColor("#B0B0B0"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(path)

        painter.setPen(QColor("#333333"))
        text_area = QRectF(self.PADDING, self.PADDING, self._text_rect.width(), self._text_rect.height())
        painter.drawText(text_area, Qt.TextFlag.TextWordWrap, self._text)
