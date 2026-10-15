"""助手的角落小条（阶段 4.4）：芸芸被别的窗口挡住时，助手要你确认 / 做完了，就在任务栏时钟上方弹一个圆角小条。

- 和角落的小红点一样是**独立的置顶窗口**（不是芸芸的从属窗口）：显示、置顶都不会把芸芸带到前面；
- 放在小红点上面一点（小红点在最角上），两个同时出现也不挡；
- 两种（2026-10-08 用户定）：等你确认（橙色，一直留到你回答）、做完了（蓝绿色，留到你点它、芸芸露出来或者又问了新问题）；
- 点一下 → clicked（芸芸出来、卡片展开）。确认要看了详情再答，小条上不放"允许"按钮；
- 什么时候显示由助手总管决定（assistant_mode._update_notice）：芸芸被挡住、不在全屏时。
"""
from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from yunyun.pet.badge import Badge, corner_point

COLORS = {"confirm": "#E07A2E", "done": "#2E9E8F", "mouse": "#6E62E5"}
ICONS = {"confirm": "?", "done": "✓", "mouse": "🖱"}   # mouse：芸芸在用你的鼠标（11.6c，不管芸芸挡没挡住都显示）


class AssistantNotice(QWidget):
    HEIGHT = 30          # 小条高度（逻辑像素）
    PAD_X = 12           # 文字左右留白
    MAX_TEXT = 300       # 文字最多多宽，再长就省略
    GAP = 8              # 和角落小红点之间的距离

    clicked = Signal()

    def __init__(self, stack=0):
        """stack：往上叠几层（旁观模式的小条用 1，和助手的同时出现也不挡）。"""
        super().__init__(None)   # 没有主人：独立窗口
        self.stack = stack
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)   # 不抢键盘焦点
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._font = QFont(self.font())
        self._font.setPointSize(9)
        self._font.setBold(True)
        self.kind = ""
        self.text = ""
        self._shown_text = ""
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=220, startValue=0.0, endValue=1.0)

    def show_message(self, kind, text):
        """显示 / 换成这句话（kind："confirm" / "done"）。第一次出现时淡入。"""
        if kind != self.kind or text != self.text or not self.isVisible():
            self.kind, self.text = kind, text
            metrics = QFontMetrics(self._font)
            self._shown_text = metrics.elidedText(f"{ICONS.get(kind, '')}  {text}", Qt.TextElideMode.ElideRight,
                                                  self.MAX_TEXT)
            self.resize(metrics.horizontalAdvance(self._shown_text) + 2 * self.PAD_X + 2, self.HEIGHT)
            self.update()
        self.move(self.corner_position())
        if not self.isVisible():
            self.show()
            self._fade.start()

    def corner_position(self):
        """任务栏时钟那个角（主屏幕），在角落小红点的里面一点（下面的任务栏时：小红点上方）。"""
        badge_room = Badge.HEIGHT + self.GAP + self.stack * (self.HEIGHT + self.GAP)   # 给小红点（和下面几层）留的位置
        return corner_point(self.width(), self.height(), badge_room)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor("white"), 1.5))   # 白边：深色背景上也看得清
        painter.setBrush(QColor(COLORS.get(self.kind, "#555555")))
        painter.drawRoundedRect(body, body.height() / 2, body.height() / 2)
        painter.setPen(QColor("white"))
        painter.setFont(self._font)
        painter.drawText(body, Qt.AlignmentFlag.AlignCenter, self._shown_text)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
