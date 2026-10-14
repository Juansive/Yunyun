"""设置窗口的样子（2026-10-14）：主题色和右键菜单"✦ 助手模式"一样是蓝紫渐变。

样式参考 uiverse.io（MIT）的开关 / 按钮 / 输入框。那边是 HTML / CSS，Qt 用不了，
这里只是照着样子用 QSS 和自己画重写，没有搬代码。
"""
import time

from PySide6.QtCore import QEasingCurve, QObject, QRectF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette
from PySide6.QtWidgets import QCheckBox, QListWidget, QStyledItemDelegate

from yunyun.paths import resource_path
from yunyun.pet.bubble import animate, blend
from yunyun.pet.side_panel import SCROLLBAR_STYLE

ACCENT_START, ACCENT_END = "#2F8CFF", "#7B5CFF"   # 蓝 → 紫
ACCENT = "#5B6CFF"                                  # 两色中间：输入框聚焦、滑块
BORDER = "#D5DAE6"
WINDOW_BG = "#F6F7FB"                               # 设置窗口（和里面弹出的对话框）的底色


def _gradient(start=ACCENT_START, end=ACCENT_END):
    return f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {start}, stop:1 {end})"


def icon_url(name):
    return resource_path("assets", "ui", name).as_posix()


SETTINGS_STYLE = f"""
QDialog {{ background: {WINDOW_BG}; }}
QDialog#framed {{ background: transparent; }}
QFrame#sidebarPanel {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #E6ECFF, stop:1 #EFE8FF);
                       border-radius: 14px; }}
QLabel#appName {{ font-size: 13pt; font-weight: bold; color: #3C3F9A; margin-top: 4px; }}
QLabel#version {{ font-size: 8pt; color: #9AA0BC; }}
QLabel#pageTitle {{ font-size: 16pt; font-weight: bold; color: #1F2330; }}
QFrame#titleLine {{ background: {_gradient()}; border-radius: 2px; }}
QListWidget#sidebar {{ border: none; background: transparent; font-size: 11pt; outline: 0; }}
QListWidget#sidebar::item {{ padding: 9px 12px; margin: 1px 0; }}

QPushButton {{ background: white; color: #2A3040; border: 1px solid {BORDER}; border-radius: 8px; padding: 5px 14px; }}
QPushButton:hover {{ background: #F4F6FF; border-color: #9AA8F8; }}
QPushButton:pressed {{ background: #E7EBFF; }}
QPushButton:disabled {{ background: #F1F2F6; color: #A8AEBC; border-color: #E3E6EE; }}
QPushButton[primary="true"] {{ background: {_gradient()}; color: white; border: none; padding: 6px 18px; font-weight: bold; }}
QPushButton[primary="true"]:hover {{ background: {_gradient("#4A9BFF", "#8C72FF")}; }}
QPushButton[primary="true"]:pressed {{ background: {_gradient("#257BE6", "#6A4DE6")}; }}
QPushButton[primary="true"]:disabled {{ background: #C5CBE0; }}

QLineEdit, QComboBox, QAbstractSpinBox {{ background: white; color: #1F2330; border: 1px solid {BORDER}; border-radius: 8px;
                                          padding: 4px 8px; selection-background-color: #8C9BFF; }}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover {{ border-color: #AEB8F0; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ background: #F1F2F6; color: #A8AEBC; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{ image: url({icon_url("chevron-down.svg")}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {BORDER}; outline: 0; padding: 4px;
                               selection-background-color: #ECEEFF; selection-color: #1F2330; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ border: none; background: transparent; width: 18px; }}
QAbstractSpinBox::up-arrow {{ image: url({icon_url("chevron-up.svg")}); width: 9px; height: 9px; }}
QAbstractSpinBox::down-arrow {{ image: url({icon_url("chevron-down.svg")}); width: 9px; height: 9px; }}

QSlider::groove:horizontal {{ height: 4px; background: #DDE1EB; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {_gradient()}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; border: 2px solid {ACCENT}; width: 12px; height: 12px;
                              margin: -6px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ border-color: {ACCENT_START}; }}

QProgressBar {{ background: #E4E7F0; border: none; border-radius: 5px; text-align: center; color: #3A4050; }}
QProgressBar::chunk {{ background: {_gradient()}; border-radius: 5px; }}

QGroupBox {{ border: 1px solid #E1E4EE; border-radius: 10px; margin-top: 12px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #4A55C8; font-weight: bold; }}

QListWidget, QTableView, QTreeView, QPlainTextEdit {{ background: white; border: 1px solid #E1E4EE; border-radius: 8px; }}
QListWidget::item:selected, QTableView::item:selected {{ background: #ECEEFF; color: #1F2330; }}
QHeaderView::section {{ background: #F3F4F9; color: #4A5060; border: none; border-bottom: 1px solid #E1E4EE; padding: 4px 8px; }}
""" + SCROLLBAR_STYLE


class GlowTrail(QObject):
    """鼠标经过的底色：很快"弹"出来（从小一圈稍微放大到位），离开后慢慢淡掉——
    快速划过几项时一个接一个亮起又暗下，像在琴键上刮奏。设置窗口的侧栏、右键菜单共用。

    key 是哪一项（行号、QAction 都行，None = 鼠标不在任何一项上）。所有项共用一个定时器，
    都到位了就关掉，平时不重画。
    """
    RISE_S, FALL_S = 0.12, 0.35   # 亮起 / 淡掉要多久

    def __init__(self, target):
        super().__init__(target)
        self._target = target   # 要重画的控件
        self.levels = {}        # key → 亮度 0～1
        self.hover = None
        self._last = 0.0
        self._timer = QTimer(self, interval=16, timeout=self._step)
        self._pop = QEasingCurve(QEasingCurve.Type.OutBack)

    @property
    def active(self):
        return self._timer.isActive()

    def set_hover(self, key):
        if key != self.hover:
            self.hover = key
            if not self._timer.isActive():
                self._last = time.monotonic()
                self._timer.start()

    def _step(self):
        now = time.monotonic()
        dt, self._last = now - self._last, now
        if self.hover is not None:
            self.levels.setdefault(self.hover, 0.0)
        for key in list(self.levels):
            if key == self.hover:
                self.levels[key] = min(1.0, self.levels[key] + dt / self.RISE_S)
            else:
                self.levels[key] -= dt / self.FALL_S
                if self.levels[key] <= 0:
                    del self.levels[key]
        if all(key == self.hover and level >= 1 for key, level in self.levels.items()):
            self._timer.stop()   # 都到位了：不再重画
        self._target.update()

    def paint(self, p, rect_of, color_of, radius=8):
        """画所有亮着的底色：rect_of(key) → QRectF（None = 现在看不见），color_of(key) → 亮到底时的颜色。"""
        p.setPen(Qt.PenStyle.NoPen)
        for key, level in self.levels.items():
            rect = rect_of(key)
            if rect is None:
                continue
            grow = 0.9 + 0.1 * self._pop.valueForProgress(level)   # 从小一圈弹到正好（略微超过一点再回来）
            dx, dy = rect.width() * (1 - grow) / 2, rect.height() * (1 - grow) / 2
            color = QColor(color_of(key))
            color.setAlphaF(color.alphaF() * level)
            p.setBrush(color)
            p.drawRoundedRect(rect.adjusted(dx, dy, -dx, -dy), radius, radius)


class Sidebar(QListWidget):
    """设置窗口左边的页面列表（用法和 QListWidget 一样）。

    - 选中的渐变块从原来那项滑到新的一项，文字被盖住多少就白多少；
    - 鼠标经过的白底弹出来、慢慢淡掉（GlowTrail）。
    """
    GLOW = QColor(255, 255, 255, 210)   # 左栏是浅蓝紫底：白色最显眼

    def __init__(self):
        super().__init__(objectName="sidebar")
        self.setMouseTracking(True)
        self.setItemDelegate(_SidebarText(self))
        self._row = -1   # 渐变块停在哪一项
        self.trail = GlowTrail(self.viewport())
        self._slide = QVariantAnimation(self, duration=260, easingCurve=QEasingCurve.Type.OutCubic,
                                        valueChanged=self.viewport().update)
        self.currentRowChanged.connect(self._slide_to)

    def _row_rect(self, row):
        item = self.item(row) if row is not None and row >= 0 else None
        rect = QRectF(self.visualItemRect(item)) if item is not None else QRectF()
        return None if rect.isEmpty() else rect.adjusted(0, 1, 0, -1)

    def pill_rect(self):
        if self._slide.state() == QVariantAnimation.State.Running:
            return self._slide.currentValue()
        return self._row_rect(self._row)

    def _slide_to(self, row):
        start, end = self.pill_rect(), self._row_rect(row)
        self._row = row
        self._slide.stop()
        if start is not None and end is not None and self.isVisible():   # 刚建好时直接到位
            self._slide.setStartValue(start)
            self._slide.setEndValue(end)
            self._slide.start()
        self.viewport().update()

    def mouseMoveEvent(self, event):
        row = self.indexAt(event.position().toPoint()).row()
        self.trail.set_hover(row if row >= 0 else None)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.trail.set_hover(None)
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.trail.paint(p, self._row_rect, lambda row: self.GLOW)
        pill = self.pill_rect()
        if pill is not None:
            gradient = QLinearGradient(pill.topLeft(), pill.topRight())
            gradient.setColorAt(0, QColor(ACCENT_START))
            gradient.setColorAt(1, QColor(ACCENT_END))
            p.setBrush(gradient)
            p.drawRoundedRect(pill, 8, 8)
        p.end()
        super().paintEvent(event)   # 再画文字（_SidebarText）


class _SidebarText(QStyledItemDelegate):
    """只画文字（底色由 Sidebar 画）：被渐变块盖住多少，文字就白多少。"""

    def paint(self, painter, option, index):
        rect, pill = QRectF(option.rect), self.parent().pill_rect()
        cover = 0.0
        if pill is not None:
            cover = max(0.0, min(pill.bottom(), rect.bottom()) - max(pill.top(), rect.top())) / rect.height()
        painter.save()
        painter.setFont(option.font)
        painter.setPen(blend("#3A4050", "#FFFFFF", min(1.0, cover)))
        painter.drawText(option.rect.adjusted(12, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         index.data())
        painter.restore()


def primary(button):
    """主按钮（"测试并保存"之类）：蓝紫渐变底、白字。"""
    button.setProperty("primary", True)
    return button


class Toggle(QCheckBox):
    """会滑动的开关，代替勾选框（用法和 QCheckBox 一样）。

    用户点的时候滑过去；程序里 setChecked（常常 blockSignals）直接到位，不播动画。
    """
    TRACK_W, TRACK_H, GAP = 34, 18, 8

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._slide = QVariantAnimation(self, duration=180, easingCurve=QEasingCurve.Type.OutCubic,
                                        valueChanged=self.update)

    def nextCheckState(self):
        super().nextCheckState()
        on = self.isChecked()
        animate(self._slide, not on, on)

    def sizeHint(self):
        text = self.fontMetrics().horizontalAdvance(self.text())
        width = self.TRACK_W + (self.GAP + text if text else 0) + 2
        return QSize(width, max(self.TRACK_H, self.fontMetrics().height()) + 4)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)   # 文字上也能点

    def paintEvent(self, event):
        running = self._slide.state() == QVariantAnimation.State.Running
        on = self._slide.currentValue() if running else float(self.isChecked())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        if not self.isEnabled():
            p.setOpacity(0.45)
        track = QRectF(1, (self.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        radius = self.TRACK_H / 2
        hover = self.underMouse() and self.isEnabled()
        p.setBrush(QColor("#B9BFCD" if hover else "#CBD0DC"))
        p.drawRoundedRect(track, radius, radius)
        if on > 0:   # 打开的颜色淡入，盖在灰色上
            gradient = QLinearGradient(track.topLeft(), track.topRight())
            gradient.setColorAt(0, blend(ACCENT_START, "#FFFFFF", 0.12 if hover else 0))
            gradient.setColorAt(1, blend(ACCENT_END, "#FFFFFF", 0.12 if hover else 0))
            p.save()
            p.setOpacity(p.opacity() * on)
            p.setBrush(gradient)
            p.drawRoundedRect(track, radius, radius)
            p.restore()
        knob = self.TRACK_H - 4
        x = track.left() + 2 + on * (self.TRACK_W - self.TRACK_H)
        p.setBrush(QColor(0, 0, 0, 40))   # 一点点阴影
        p.drawEllipse(QRectF(x, track.top() + 3, knob, knob))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, track.top() + 2, knob, knob))
        if self.text():
            p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            text_rect = self.rect().adjusted(int(track.right()) + self.GAP, 0, 0, 0)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
