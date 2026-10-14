"""自己画边框的窗口（设置窗口用，2026-10-15）。

样子参考 uiverse.io（MIT）的卡片：大圆角、蓝紫渐变的细描边、带一点紫色的柔和阴影、
右上角几圈很淡的同心圆。那边是 CSS，这里照着样子用 QPainter 重画，没有搬代码。

没有系统边框，所以这些都自己做：
- 右上角最小化 / 最大化（还原）/ 关闭三个按钮；
- 按住顶上一条拖动（startSystemMove：系统自己的拖动，能拖到屏幕边上分屏），双击最大化 / 还原；
- 按住窗口边缘改大小（startSystemResize）；
- 任务栏按钮（winapi.make_app_window：从属窗口默认没有，最小化后就找不回来了）。
最大化不用系统的（无边框窗口系统最大化会盖住任务栏）：自己铺满"可用区域"，这时不画圆角和阴影。
"""
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QDialog

from yunyun import i18n
from yunyun.pet import winapi
from yunyun.pet.bubble import animate, blend
from yunyun.pet.menu import DANGER, ICON_FONTS
from yunyun.pet.theme import ACCENT_END, ACCENT_START, WINDOW_BG

MINIMIZE, MAXIMIZE, RESTORE, CLOSE = 0xE921, 0xE922, 0xE923, 0xE8BB   # 图标字体里窗口按钮的字形


class FramedDialog(QDialog):
    SHADOW = 14    # 四周留给阴影的透明边
    RADIUS = 16
    EDGE = 6       # 离卡片边多近算"拖边改大小"
    DRAG_H = 46    # 顶上这么高的一条能拖动窗口
    PAD = 8        # 卡片里内容离边的距离

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("framed")   # theme.SETTINGS_STYLE 里让它的底透明（底色自己画）
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._normal = None   # 最大化前的位置和大小（None = 没最大化）
        self._minimize = _TitleButton(MINIMIZE, self, clicked=self.showMinimized)
        self._maximize = _TitleButton(MAXIMIZE, self, clicked=self.toggle_maximized)
        self._close = _TitleButton(CLOSE, self, danger=True, clicked=self.close)
        self._retranslate_buttons()
        if QGuiApplication.platformName() == "windows":
            winapi.make_app_window(int(self.winId()))

    # ---------- 对外 ----------

    @property
    def maximized(self):
        return self._normal is not None

    def toggle_maximized(self):
        if self._normal is not None:
            normal, self._normal = self._normal, None
            self.setGeometry(normal)
        else:
            self._normal = self.geometry()
            screen = self.screen() or QGuiApplication.primaryScreen()
            self.setGeometry(screen.availableGeometry())
        self._maximize.code = RESTORE if self.maximized else MAXIMIZE
        self._retranslate_buttons()
        self._apply_margins()

    def content_margins(self):
        """子类摆内容用的边距：卡片边 + 一点留白（右上角的按钮另外浮在上面）。"""
        m = self._margin() + self.PAD
        return m, m, m, m

    # ---------- 样子 ----------

    def _margin(self):
        return 0 if self.maximized else self.SHADOW

    def _card(self):
        m = self._margin()
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _apply_margins(self):
        if self.layout() is not None:
            self.layout().setContentsMargins(*self.content_margins())
        self._place_buttons()
        self.update()

    def _retranslate_buttons(self):
        self._minimize.setToolTip(i18n.tr("window_minimize"))
        self._maximize.setToolTip(i18n.tr("window_restore" if self.maximized else "window_maximize"))
        self._close.setToolTip(i18n.tr("window_close"))

    def _place_buttons(self):
        card = self._card().toRect()
        x = card.right() - 6
        for button in (self._close, self._maximize, self._minimize):
            x -= button.width()
            button.move(x, card.top() + 6)
            button.raise_()

    def resizeEvent(self, event):
        self._place_buttons()
        super().resizeEvent(event)

    def showEvent(self, event):
        self._apply_margins()
        super().showEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card, radius = self._card(), 0 if self.maximized else self.RADIUS
        if not self.maximized:   # 带一点紫色的柔和阴影：越往外越淡
            for i in range(self.SHADOW, 0, -2):
                shadow = QPainterPath()
                shadow.addRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), radius + i, radius + i)
                p.fillPath(shadow, QColor(70, 60, 170, round(14 * (1 - i / self.SHADOW) ** 2) + 1))
        body = QPainterPath()
        body.addRoundedRect(card, radius, radius)
        p.fillPath(body, QColor(WINDOW_BG))
        # 右上角几圈很淡的同心圆（装饰，画在内容后面）
        p.save()
        p.setClipPath(body)
        center = QPoint(round(card.right()) - 30, round(card.top()) + 20)
        for n, r in enumerate((70, 115, 160, 205)):
            p.setPen(QPen(QColor(123, 92, 255, 26 - n * 5), 1.2))
            p.drawEllipse(center, r, r)
        p.restore()
        if not self.maximized:   # 蓝紫渐变的细描边
            gradient = QLinearGradient(card.topLeft(), card.bottomRight())
            gradient.setColorAt(0, _alpha(ACCENT_START, 150))
            gradient.setColorAt(0.5, _alpha("#B9B4F5", 110))
            gradient.setColorAt(1, _alpha(ACCENT_END, 150))
            p.setPen(QPen(QBrush(gradient), 1.3))
            p.drawRoundedRect(card.adjusted(0.65, 0.65, -0.65, -0.65), radius, radius)

    # ---------- 拖动 / 改大小 ----------

    def _edges(self, pos):
        """pos 在卡片哪条边上（用来改大小）；最大化时不能改。"""
        if self.maximized:
            return Qt.Edge(0)
        card, edges = self._card(), Qt.Edge(0)
        x, y = pos.x(), pos.y()
        if card.left() <= x < card.left() + self.EDGE:
            edges |= Qt.Edge.LeftEdge
        if card.right() - self.EDGE < x <= card.right():
            edges |= Qt.Edge.RightEdge
        if card.top() <= y < card.top() + self.EDGE:
            edges |= Qt.Edge.TopEdge
        if card.bottom() - self.EDGE < y <= card.bottom():
            edges |= Qt.Edge.BottomEdge
        return edges

    def _in_drag_bar(self, pos):
        card = self._card()
        return card.contains(pos) and pos.y() < card.top() + self.DRAG_H

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.NoButton:
            edges = self._edges(event.position())
            cursors = {Qt.Edge.LeftEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeFDiagCursor,
                       Qt.Edge.RightEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeFDiagCursor,
                       Qt.Edge.RightEdge | Qt.Edge.TopEdge: Qt.CursorShape.SizeBDiagCursor,
                       Qt.Edge.LeftEdge | Qt.Edge.BottomEdge: Qt.CursorShape.SizeBDiagCursor}
            if edges in cursors:
                self.setCursor(cursors[edges])
            elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif edges:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.unsetCursor()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle() is not None:
            pos = event.position()
            edges = self._edges(pos)
            if edges:
                self.windowHandle().startSystemResize(edges)
                return
            if self._in_drag_bar(pos):
                if self.maximized:   # 最大化时拖：先还原，让鼠标还在标题栏上差不多的位置
                    ratio = pos.x() / max(1, self.width())
                    self.toggle_maximized()
                    grab = event.globalPosition().toPoint()
                    self.move(grab - QPoint(round(self.width() * ratio), round(pos.y()) + self.SHADOW))
                self.windowHandle().startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._in_drag_bar(event.position()):
            self.toggle_maximized()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        self.unsetCursor()
        super().leaveEvent(event)


class _TitleButton(QAbstractButton):
    """右上角的窗口按钮：鼠标经过时圆角底色淡入（关闭是红底白字）。"""
    SIZE = QSize(34, 28)

    def __init__(self, code, parent, danger=False, clicked=None):
        super().__init__(parent)
        self.code, self._danger = code, danger
        self.setFixedSize(self.SIZE)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = QVariantAnimation(self, duration=140, valueChanged=self.update)
        if clicked is not None:
            self.clicked.connect(clicked)

    def enterEvent(self, event):
        animate(self._hover, self._level(), 1)
        super().enterEvent(event)

    def leaveEvent(self, event):
        animate(self._hover, self._level(), 0)
        super().leaveEvent(event)

    def _level(self):
        value = self._hover.currentValue()
        return 0.0 if value is None else value

    def paintEvent(self, event):
        level = self._level()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if level > 0:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_alpha(DANGER if self._danger else "#5B6CFF", round((255 if self._danger else 34) * level)))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 7, 7)
        font = QFont()
        font.setFamilies(ICON_FONTS)
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(blend("#4A5060", "#FFFFFF", level) if self._danger else QColor("#4A5060"))
        p.drawText(QRect(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, chr(self.code))


def _alpha(color, alpha):
    color = QColor(color)
    color.setAlpha(alpha)
    return color
