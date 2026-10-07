"""右键菜单的公共部分（芸芸本体菜单和托盘菜单共用）。"""
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget, QWidgetAction


class FancyAction(QWidgetAction):
    """菜单里特别显眼的一项（"✦ 助手模式"）：蓝紫渐变的粗体字，鼠标移上去有淡蓝底色。

    QMenu 不能单独给某一项换颜色，所以用 QWidgetAction 放一个自己画的小部件。
    """

    def __init__(self, text, parent):
        super().__init__(parent)
        self.setText(text)

    def createWidget(self, parent):
        return _FancyItem(self, parent)


class _FancyItem(QWidget):
    PAD_LEFT = 28    # 和普通菜单项的文字大致对齐（左边是图标栏）
    PAD_RIGHT = 24

    def __init__(self, action, parent):
        super().__init__(parent)
        self._action = action
        self._hover = False
        self.setMouseTracking(True)
        font = self.font()
        font.setBold(True)
        self.setFont(font)

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(self.PAD_LEFT + metrics.horizontalAdvance(self._action.text()) + self.PAD_RIGHT,
                     metrics.height() + 14)

    def enterEvent(self, event):
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            menu = self.parentWidget()
            if isinstance(menu, QMenu):
                menu.close()
            self._action.trigger()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E8F1FF"))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(3, 1, -3, -1), 5, 5)
        text_rect = self.rect().adjusted(self.PAD_LEFT, 0, 0, 0)
        gradient = QLinearGradient(text_rect.left(), 0, text_rect.left() + self.fontMetrics().horizontalAdvance(
            self._action.text()), 0)
        gradient.setColorAt(0, QColor("#2F8CFF"))
        gradient.setColorAt(1, QColor("#7B5CFF"))
        p.setPen(QPen(QBrush(gradient), 1))
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._action.text())


class KeepOpenMenu(QMenu):
    """点击 keep_open_action（比如"退出"）时菜单不关闭，方便快速点第二下。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keep_open_action = None

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        if action is not None and action is self.keep_open_action:
            action.trigger()  # 只触发动作，不走 QMenu 默认的"触发后关闭菜单"流程
            return
        super().mouseReleaseEvent(event)
