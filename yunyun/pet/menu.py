"""右键菜单的公共部分（芸芸本体菜单和托盘菜单共用）。

样子（2026-10-14）：白色圆角卡片 + 柔和阴影 + 浅紫描边，打开时淡入；每项前面一个小图标
（Windows 自带的图标字体画的，不用图片），鼠标经过的底色弹出来、划过有拖尾（theme.GlowTrail）；
"退出"经过时是浅红底、红图标。
"""
from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QWidget, QWidgetAction

from yunyun.pet.bubble import animate
from yunyun.pet.side_panel import draw_card
from yunyun.pet.theme import ACCENT, ACCENT_END, ACCENT_START, GlowTrail, icon_url

ICON_FONTS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]   # Windows 自带的图标字体（11 / 10，字形编号一样）


class Icon:
    """菜单里用到的图标（图标字体里的编号）。"""
    SETTINGS, MAIL, ACCEPT, PAUSE, PLAY, POWER = 0xE713, 0xE715, 0xE8FB, 0xE769, 0xE768, 0xE7E8
    HISTORY, SPEAK, STOP, MUTE, HIDE, PLACE, CLOSE, DOWNLOAD = 0xE81C, 0xE767, 0xE71A, 0xE74F, 0xED1A, 0xE81D, 0xE711, 0xE896


DANGER = "#E5484D"
SHADOW = 6   # 卡片四周留给阴影的透明边

MENU_STYLE = f"""
QMenu {{ background: transparent; border: none; padding: {SHADOW + 5}px {SHADOW + 5}px {SHADOW + 8}px; font-size: 10pt; }}
QMenu::item {{ padding: 7px 28px 7px 8px; margin: 1px 0; color: #2A3040; background: transparent; }}
QMenu::item:selected {{ background: transparent; color: #1F2330; }}
QMenu::item:disabled {{ color: #B0B5C3; }}
QMenu::icon {{ padding-left: 10px; }}
QMenu::separator {{ height: 1px; background: #E8E9F3; margin: 5px 12px; }}
QMenu::indicator {{ width: 14px; height: 14px; padding-left: 10px; }}
QMenu::indicator:checked {{ image: url({icon_url("check.svg")}); }}
"""


@lru_cache(maxsize=64)
def glyph_icon(code, hover=ACCENT, size=16):
    """菜单项前面的小图标：平时灰色，鼠标经过时变成 hover 的颜色。"""
    icon = QIcon()
    for mode, color in ((QIcon.Mode.Normal, "#6B7280"), (QIcon.Mode.Active, hover), (QIcon.Mode.Selected, hover)):
        icon.addPixmap(_glyph(code, color, size), mode)
    return icon


def _glyph(code, color, size):
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen is not None else 1.0
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    font = QFont()
    font.setFamilies(ICON_FONTS)
    font.setPixelSize(size - 2)
    p = QPainter(pixmap)
    p.setFont(font)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, chr(code))
    p.end()
    return pixmap


class FancyAction(QWidgetAction):
    """菜单里特别显眼的一项（"✦ 助手模式"）：蓝紫渐变的粗体字。

    QMenu 不能单独给某一项换颜色，所以用 QWidgetAction 放一个自己画的小部件。
    """

    def __init__(self, text, parent):
        super().__init__(parent)
        self.setText(text)

    def createWidget(self, parent):
        return _FancyItem(self, parent)


STAR_X = 13   # "✦"的中心：和别的项的图标对齐


class _FancyItem(QWidget):
    PAD_LEFT = 29    # 和普通菜单项的文字对齐（左边是图标栏）
    PAD_RIGHT = 28

    def __init__(self, action, parent):
        super().__init__(parent)
        self._action = action
        font = self.font()
        font.setBold(True)
        self.setFont(font)

    def sizeHint(self):
        metrics = self.fontMetrics()
        return QSize(self.PAD_LEFT + metrics.horizontalAdvance(self._action.text()) + self.PAD_RIGHT,
                     metrics.height() + 16)

    def _set_hover(self, on):
        menu = self.parentWidget()
        if isinstance(menu, KeepOpenMenu):   # 底色由菜单画（和别的项一样弹出来、有拖尾）
            menu.trail.set_hover(self._action if on else None)

    def enterEvent(self, event):
        self._set_hover(True)

    def leaveEvent(self, event):
        self._set_hover(False)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            menu = self.parentWidget()
            if isinstance(menu, QMenu):
                menu.close()
            self._action.trigger()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        star, _, text = self._action.text().partition(" ")   # "✦ 助手模式"：星星放在图标那一列，字和别的项对齐
        if not text:
            star, text = "", star
        text_rect = self.rect().adjusted(self.PAD_LEFT, 0, 0, 0)
        gradient = QLinearGradient(STAR_X - 8, 0, text_rect.left() + self.fontMetrics().horizontalAdvance(text), 0)
        gradient.setColorAt(0, QColor(ACCENT_START))
        gradient.setColorAt(1, QColor(ACCENT_END))
        p.setPen(QPen(QBrush(gradient), 1))
        if star:
            font = self.font()
            font.setFamilies(["Segoe UI Symbol", *font.families()])
            p.save()
            p.setFont(font)
            p.drawText(QRectF(STAR_X - 10, 0, 20, self.height()), Qt.AlignmentFlag.AlignCenter, star)
            p.restore()
        p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


class KeepOpenMenu(QMenu):
    """芸芸的右键菜单 / 托盘菜单：圆角卡片的样子（见上面）。

    点击 keep_open_action（比如"退出"）时菜单不关闭，方便快速点第二下。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keep_open_action = None
        # 圆角和阴影自己画：窗口要无边框、透明，也不要系统那个方方正正的阴影
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(MENU_STYLE)
        self.trail = GlowTrail(self)
        self.hovered.connect(lambda action: self.trail.set_hover(
            action if action.isEnabled() and not action.isSeparator() else None))
        self._fade_in = QVariantAnimation(self, duration=120, valueChanged=self.setWindowOpacity)

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        if action is not None and action is self.keep_open_action:
            action.trigger()  # 只触发动作，不走 QMenu 默认的"触发后关闭菜单"流程
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self.trail.set_hover(None)
        super().leaveEvent(event)

    def showEvent(self, event):
        self.setWindowOpacity(0.0)
        animate(self._fade_in, 0, 1)
        super().showEvent(event)

    def _glow_rect(self, action):
        if action not in self.actions() or not action.isVisible():
            return None
        return QRectF(self.actionGeometry(action)).adjusted(4, 0, -4, 0)

    def _glow_color(self, action):
        return "#FFE4E6" if action is self.keep_open_action else "#ECEEFF"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_card(p, QRectF(self.rect()).adjusted(SHADOW, SHADOW - 2, -SHADOW, -SHADOW - 2), 12, "#E2E0F7")
        self.trail.paint(p, self._glow_rect, self._glow_color)
        p.end()
        super().paintEvent(event)   # 再画图标和文字（背景是透明的）
