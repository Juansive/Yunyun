"""长按右键唤出的轮盘（2026-10-29 用户要的）。

右键按住芸芸 1 秒（身上一个白色空心小圆圈的线转一圈，画在 window.py）→ 轮盘以鼠标为中心弹出：
中间一个圆，一圈常用的几项从上面开始顺时针一个接一个从小变大弹出来。
还按着右键划到哪一项那项就放大发亮、中间写它的名字，松开右键就打开它；在中间松开 = 取消。
算"划到哪一项"只看方向（像游戏里的武器轮盘）：离中间够远就选方向最近的那项，不用正好压在圆上。

轮盘是独立的置顶窗口（从属窗口显示时会把芸芸一起拉到最前面），不接鼠标、不抢焦点：
右键按下以后鼠标消息一直归芸芸，芸芸把鼠标位置和"松开"交给这里。
"""
import math
from typing import Callable, NamedTuple

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from yunyun.pet.bubble import animate, blend
from yunyun.pet.menu import DANGER, ICON_FONTS
from yunyun.pet.theme import ACCENT, ACCENT_END, ACCENT_START


class WheelItem(NamedTuple):
    icon: object            # 图标字体里的编号（menu.Icon.X），或者直接画的字（"✦"）
    text: str               # 划上去时中间显示的名字
    slot: Callable
    enabled: bool = True    # 变灰的项划上去不算选中（和右键菜单里变灰一样）
    danger: bool = False    # 停止助手：红色


RADIUS = 74        # 各项圆心离中间多远
ITEM_R = 21        # 每一项的圆有多大
HUB_R = 34         # 中间的圆（写名字）
DEAD_ZONE = 24     # 离中间这么近 = 没选（松开就是取消）
HOVER_SCALE = 1.18
STAGGER_MS = 45    # 一项接一项弹出来：每项晚多久
POP_MS = 220       # 每一项从小变大用多久
FADE_MS = 120      # 松开后淡出
PAD = 12           # 窗口四周留给阴影和放大的边


def item_angle(index, count):
    """第 index 项的方向（度，0 = 右边，顺时针为正，第一项在正上方）。"""
    return -90 + 360 * index / count


def pick(offset, count, dead_zone=DEAD_ZONE):
    """鼠标离中间 offset（QPointF）时选中第几项；离中间太近返回 None。纯计算，方便测试。"""
    if count == 0 or math.hypot(offset.x(), offset.y()) < dead_zone:
        return None
    angle = math.degrees(math.atan2(offset.y(), offset.x()))   # 屏幕坐标 y 向下，所以顺时针是正的
    step = 360 / count
    return round(((angle + 90) % 360) / step) % count


class Wheel(QWidget):
    closed = Signal()

    def __init__(self, items, center):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.items = list(items)
        self.hovered = None
        half = RADIUS + ITEM_R * HOVER_SCALE + PAD
        self.resize(round(half * 2), round(half * 2))
        self._place(center)
        self._ease = QEasingCurve(QEasingCurve.Type.OutBack)
        # 一个动画管全部：值是"弹出来过了多少毫秒"，第 i 项从 i × STAGGER_MS 开始弹（中间的圆最先）
        self._pop = QVariantAnimation(self, duration=STAGGER_MS * len(self.items) + POP_MS,
                                      valueChanged=lambda _: self.update())
        self._hover = QVariantAnimation(self, duration=120, valueChanged=lambda _: self.update())
        self._fade = QVariantAnimation(self, duration=FADE_MS, valueChanged=self.setWindowOpacity,
                                       finished=self._finish)
        self._closing = False

    def _place(self, center):
        """以 center 为中心；靠近屏幕边时整个挪进来（中心跟着挪，选方向照样准）。"""
        rect = QRect(QPoint(0, 0), self.size())
        rect.moveCenter(center)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            rect.moveLeft(max(area.left(), min(rect.left(), area.right() - rect.width() + 1)))
            rect.moveTop(max(area.top(), min(rect.top(), area.bottom() - rect.height() + 1)))
        self.move(rect.topLeft())

    def popup(self):
        self.setWindowOpacity(1.0)
        self.show()
        animate(self._pop, 0, self._pop.duration())

    def track(self, global_pos):
        """鼠标移到了 global_pos（屏幕坐标）：换选中的那项。"""
        if self._closing:
            return
        index = pick(QPointF(global_pos - self.geometry().center()), len(self.items))
        if index is not None and not self.items[index].enabled:
            index = None
        if index != self.hovered:
            self.hovered = index
            animate(self._hover, 0, 1)

    def release(self):
        """松开了右键：打开选中的那项（没选就是取消），淡出。"""
        if self._closing:
            return
        chosen = self.items[self.hovered] if self.hovered is not None else None
        self.dismiss()
        if chosen is not None:
            chosen.slot()

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        animate(self._fade, 1, 0)

    def _finish(self):
        self.hide()
        self.closed.emit()
        self.deleteLater()

    # ---------- 画 ----------

    def _progress(self, order):
        """第 order 个（0 = 中间的圆）弹到了几成：0 → 1，OutBack 会稍微冲过头再回来。"""
        stopped = self._pop.state() == QVariantAnimation.State.Stopped
        t = self._pop.duration() if stopped else self._pop.currentTime()
        p = min(max((t - order * STAGGER_MS) / POP_MS, 0.0), 1.0)
        return self._ease.valueForProgress(p) if p > 0 else 0.0

    def _hover_amount(self, index):
        if index != self.hovered:
            return 0.0
        return self._hover.currentValue() if self._hover.state() == QVariantAnimation.State.Running else 1.0

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        hub = self._progress(0)
        if hub > 0:
            self._paint_hub(p, center, HUB_R * hub)
        for i, item in enumerate(self.items):
            grow = self._progress(i + 1)
            if grow <= 0:
                continue
            angle = math.radians(item_angle(i, len(self.items)))
            spot = center + QPointF(math.cos(angle), math.sin(angle)) * RADIUS * min(1.0, 0.55 + 0.45 * grow)
            self._paint_item(p, item, spot, ITEM_R * grow * (1 + (HOVER_SCALE - 1) * self._hover_amount(i)),
                             self._hover_amount(i), min(1.0, grow * 2))
        p.end()

    @staticmethod
    def _disc(p, center, r, fill, border=None):
        """一个白色圆片 + 往下偏一点的柔和阴影（和 side_panel.draw_card 一样的画法）。"""
        for i in range(5, 0, -1):
            shadow = QPainterPath()
            shadow.addEllipse(center + QPointF(0, 2), r + i, r + i)
            p.fillPath(shadow, QColor(30, 40, 70, int(9 * (6 - i) / 5)))
        body = QPainterPath()
        body.addEllipse(center, r, r)
        p.fillPath(body, fill)
        if border is not None:
            p.setPen(QPen(QColor(border), 1))
            p.drawPath(body)

    def _paint_hub(self, p, center, r):
        self._disc(p, center, r, QColor(255, 255, 255, 250), "#E2E0F7")
        if self.hovered is None:   # 没选：中间一个淡淡的空心小圆（长按时那个圆圈），在这里松开 = 取消
            p.setPen(QPen(QColor(ACCENT), 2))
            p.setOpacity(0.35)
            p.drawEllipse(center, r * 0.32, r * 0.32)
            p.setOpacity(1.0)
            return
        item = self.items[self.hovered]
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(DANGER if item.danger else "#2A3040"))
        side = r * 1.55   # 圆里能放下字的方块
        p.drawText(QRectF(center.x() - side / 2, center.y() - side / 2, side, side),
                   Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, item.text)

    def _paint_item(self, p, item, center, r, hover, opacity):
        p.setOpacity(opacity * (1.0 if item.enabled else 0.45))
        self._disc(p, center, r, QColor(255, 255, 255, 252), "#E2E0F7")
        if hover > 0:   # 选中：渐变底色慢慢盖上来，图标变白
            gradient = QLinearGradient(center - QPointF(r, r), center + QPointF(r, r))
            gradient.setColorAt(0, QColor(DANGER).lighter(115) if item.danger else QColor(ACCENT_START))
            gradient.setColorAt(1, QColor(DANGER) if item.danger else QColor(ACCENT_END))
            disc = QPainterPath()
            disc.addEllipse(center, r, r)
            p.save()
            p.setOpacity(p.opacity() * hover)
            p.fillPath(disc, gradient)
            p.restore()
        normal = DANGER if item.danger else (ACCENT if isinstance(item.icon, str) else "#4B5563")
        color = blend(normal, "white", hover)
        font = QFont(self.font())
        if isinstance(item.icon, str):   # "✦"：普通字体画
            font.setFamilies(["Segoe UI Symbol", *font.families()])
            font.setPixelSize(max(1, round(r * 0.9)))
            glyph = item.icon
        else:
            font.setFamilies(ICON_FONTS)
            font.setPixelSize(max(1, round(r * 0.8)))
            glyph = chr(item.icon)
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(center.x() - r, center.y() - r, r * 2, r * 2), Qt.AlignmentFlag.AlignCenter, glyph)
        p.setOpacity(1.0)
