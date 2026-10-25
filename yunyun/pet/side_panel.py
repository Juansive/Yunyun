"""助手模式里"从旁边滑出来的白色面板"的公共部分：阅读框（assistant_reader）、确认卡片（assistant_confirm）、
历史对话面板（assistant_history_panel）都用它。

- 放在 anchor（芸芸 + 输入框 + 卡片堆占的区域）旁边，哪边空间大放哪边（bubble.place_block）；
- 从 anchor 那边滑出、淡入，收起时反过来；
- 焦点去了别的程序 → dismissed（总管决定要不要理：现在卡片常亮，不理）；
- 按住空白处（四周留白、各行之间）拖 → drag_started / dragged(移动了多少) / drag_finished，由总管挪整组（卡片堆 + 它）；
- 白底圆角 + 柔和的阴影，边框颜色可以换（border_color）。

子类在 _make_content() 的布局里放内容，大小用 _set_size()（之后调 _place / _apply_open），出现时调 _show_animated()。
"""
import logging

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from yunyun.pet.bubble import animate, place_block

log = logging.getLogger(__name__)

SCROLLBAR_STYLE = """
QScrollBar:vertical { width: 6px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #CBD3E1; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""
TEXT_STYLE = "QTextBrowser { background: transparent; color: #1F2937; }" + SCROLLBAR_STYLE   # 回答、对话全文
BUTTON_STYLE = """
QPushButton { background: #EEF3FC; color: #4C6FD8; border: 1px solid #D5E0F5; border-radius: 9px;
              padding: 1px 9px; font-size: 9pt; }
QPushButton:hover { background: #E1EBFD; }
"""
_CLOSE_STYLE = """
QPushButton { background: transparent; color: #8A94A6; border: none; border-radius: 12px; font-size: 13pt; }
QPushButton:hover { background: #EEF1F6; color: #374151; }
"""


def make_button(style, slot, text="", parent=None):
    """按钮：样式、手形光标、点了调 slot（None：自己再连）。"""
    button = QPushButton(text, parent)
    button.setStyleSheet(style)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot is not None:
        button.clicked.connect(slot)
    return button


def text_view(style, margin=None, web_links=False):
    """只读的富文本框：没有边框、没有横向滚动条（太宽的自动换行）。
    web_links：里面的链接点了用浏览器打开——只开网页（open_web_link），给显示 AI 回答的框用。"""
    view = QTextBrowser()
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setStyleSheet(style)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if margin is not None:
        view.document().setDocumentMargin(margin)
    if web_links:
        view.setOpenLinks(False)   # 不在框里跳转，也不交给 Qt 直接打开
        view.anchorClicked.connect(open_web_link)
    return view


def open_web_link(url):
    """AI 回答里的链接：只用浏览器打开 http / https。file:///、\\\\电脑\\共享、别的协议点了会直接运行程序
    （回答可能被看过的网页带偏，2026-10-25 安全检查），一律不开。返回开没开。"""
    if url.scheme().lower() not in ("http", "https") or not url.host():
        log.info("没打开回答里的链接：协议是 %s", url.scheme() or "（空）")
        return False
    return QDesktopServices.openUrl(url)


def draw_card(p, rect, radius, border):
    """白底圆角 + 柔和的阴影（往下偏一点）；四周要留出约 6 像素给阴影。侧面板、右键菜单共用。"""
    for i in range(6, 0, -1):
        shadow = QPainterPath()
        shadow.addRoundedRect(rect.adjusted(-i, -i + 3, i, i + 3), radius + i, radius + i)
        p.fillPath(shadow, QColor(30, 40, 70, int(8 * (7 - i) / 6)))
    body = QPainterPath()
    body.addRoundedRect(rect, radius, radius)
    p.fillPath(body, QColor(255, 255, 255, 252))
    p.setPen(QColor(border))
    p.drawPath(body)


def divider():
    """一条浅灰的横线（标题和正文之间）。"""
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: #E6EAF1;")
    return line


class SidePanel(QWidget):
    dismissed = Signal()   # 焦点去了别的程序
    closed_by_user = Signal()   # 右上角 × / Esc：只关这个面板（焦点回到输入框）
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
        self._open = QVariantAnimation(self, duration=self.OPEN_MS, easingCurve=QEasingCurve.Type.OutCubic,
                                       valueChanged=self._on_open, finished=self._on_open_finished)

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

    # ---------- 内容和大小（子类用） ----------

    def _make_content(self, spacing):
        """内容区（四周留出阴影和内边距）：返回它的竖排布局。"""
        self._content = QWidget(self)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(spacing)
        return layout

    def _close_button(self):
        """右上角的 ×：点了 → closed_by_user。"""
        button = make_button(_CLOSE_STYLE, self._close_by_user, "×")
        button.setFixedSize(24, 24)
        return button

    def _close_by_user(self):
        self.hide_animated()
        self.closed_by_user.emit()

    def _height_limit(self, max_h):
        """面板最多多高：max_h，也不超过所在的屏幕。"""
        screen = QGuiApplication.screenAt(self._anchor.center()) or QGuiApplication.primaryScreen()
        return min(max_h, screen.availableGeometry().height() - 40)

    def _set_size(self, width, height):
        """窗口大小（含四周留白）；内容区跟着变。"""
        self.resize(width, height)
        inset = self.MARGIN + self.PAD
        self._content.setGeometry(inset, inset, width - 2 * inset, height - 2 * inset)

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
        draw_card(p, QRectF(self.rect()).adjusted(self.MARGIN, self.MARGIN, -self.MARGIN, -self.MARGIN), 14,
                  self.border_color)
