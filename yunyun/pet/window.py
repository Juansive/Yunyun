"""芸芸的桌宠窗口：透明背景、无边框、始终置顶、不占任务栏。

M1 阶段还没有正式素材，先画一个纯色圆形当占位。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class PetWindow(QWidget):
    SIZE = 160          # 窗口边长（像素）
    MARGIN = 10         # 圆形距离窗口边缘的留白
    COLOR = "#7EC8E3"   # 占位圆形的颜色

    def __init__(self):
        super().__init__()

        # 窗口标志（flags）可以用 | 组合在一起
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint      # 去掉标题栏和边框
            | Qt.WindowType.WindowStaysOnTopHint   # 始终置顶
            | Qt.WindowType.Tool                   # 工具窗口：不在任务栏显示
        )
        # 让窗口背景透明，只有我们画出来的部分可见
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.resize(self.SIZE, self.SIZE)
        self._move_to_bottom_right()

    def _move_to_bottom_right(self):
        """把芸芸放在屏幕右下角（避开任务栏）。"""
        area = self.screen().availableGeometry()  # 可用区域，不含任务栏
        self.move(area.right() - self.SIZE - 40, area.bottom() - self.SIZE - 40)

    def paintEvent(self, event):
        """Qt 每次需要重绘窗口时都会调用这个方法。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)  # 抗锯齿，边缘更平滑
        painter.setPen(Qt.PenStyle.NoPen)                         # 不画描边
        painter.setBrush(QColor(self.COLOR))                      # 填充颜色
        d = self.SIZE - 2 * self.MARGIN
        painter.drawEllipse(self.MARGIN, self.MARGIN, d, d)

    def mousePressEvent(self, event):
        # 临时：右键直接退出。之后会换成正式的右键菜单（设置 / 暂停 / 退出）。
        if event.button() == Qt.MouseButton.RightButton:
            QApplication.instance().quit()
