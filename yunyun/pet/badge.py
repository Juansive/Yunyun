"""小红点：显示待处理的新邮件数。

芸芸用两个小红点窗口（同一个类，数字和操作都一样）：
- 跟随用：芸芸的"从属窗口"，在芸芸身体右上角，和芸芸同一层（芸芸被挡住时它也被挡住）；
- 角落用：独立的置顶窗口，芸芸被挡住时来了新邮件，就出现在任务栏时钟上方。
  它不属于芸芸，所以显示、置顶都不会把芸芸带到前面。芸芸露出来后，它飞回芸芸身边，
  落地时换成跟随用的那个（位置完全重合，看不出切换）。

为什么不用一个窗口切换"主人"：Windows 规定从属窗口必须在主人上面，
所以把从属窗口设成置顶时，芸芸也会被一起拉到最前面；而手动解除主人关系，Qt 又会自动挂回去。

操作：
- 左键单击 → clicked（处理邮件）
- 按住拖走、松手 → "啵"地消失 → dismissed（这批邮件不处理，计数清零）；没拖够远就弹回原位
- 右键 → hide_requested（暂时隐藏）
"""
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


def tray_corner(geometry, available):
    """任务栏时钟（托盘）在屏幕的哪个角。返回 (在右边?, 在下边?, 任务栏是否自动隐藏)。

    geometry 是整个屏幕，available 是去掉任务栏后的可用区域，比较两者就知道任务栏贴在哪条边：
    - 任务栏在下 / 上：时钟在右端 → 右下角 / 右上角
    - 任务栏在左 / 右（竖着放）：时钟在下端 → 左下角 / 右下角
    - 两者一样大：任务栏设成了自动隐藏，按最常见的"底部"处理
    """
    g_right, g_bottom = geometry.x() + geometry.width(), geometry.y() + geometry.height()
    a_right, a_bottom = available.x() + available.width(), available.y() + available.height()
    if a_bottom < g_bottom:
        return True, True, False      # 任务栏在下
    if available.y() > geometry.y():
        return True, False, False     # 任务栏在上
    if available.x() > geometry.x():
        return False, True, False     # 任务栏在左
    if a_right < g_right:
        return True, True, False      # 任务栏在右
    return True, True, True           # 自动隐藏


def corner_point(width, height, offset=0):
    """一块 width × height 的东西放在任务栏时钟那个角（主屏幕）时的左上角；offset：再往屏幕里面让出多少
    （角落小条让出小红点的位置）。角落小红点、角落小条共用。"""
    screen = QGuiApplication.primaryScreen()  # 任务栏的时钟在主屏幕上
    area = screen.availableGeometry()
    right, bottom, autohide = tray_corner(screen.geometry(), area)
    margin = Badge.CORNER_MARGIN
    x = area.x() + area.width() - margin - width if right else area.x() + margin
    if bottom:
        extra = Badge.AUTOHIDE_MARGIN if autohide else 0
        y = area.y() + area.height() - margin - extra - offset - height
    else:
        y = area.y() + margin + offset
    return QPoint(x, y)


class Badge(QWidget):
    HEIGHT = 18             # 红点高度（逻辑像素）：小小一个
    PAD = 5                 # 窗口四周留白，给"啵"的放大动画留空间
    INSET = 12              # 红点中心离芸芸身体右上角往里缩多少
    DISMISS_DISTANCE = 60   # 拖出多远松手才算"拖走"
    CORNER_MARGIN = 12      # 角落：离屏幕可用区域边缘多远
    AUTOHIDE_MARGIN = 48    # 任务栏自动隐藏时多让出一点，别挡住弹出来的任务栏
    COLOR = "#E53935"

    clicked = Signal()
    dismissed = Signal()
    hide_requested = Signal()

    def __init__(self, owner=None, topmost=False):
        """owner：芸芸窗口（跟随用）或 None（角落用）；topmost：是否始终在所有窗口上面。"""
        super().__init__(owner)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._font = QFont("Segoe UI", 8, QFont.Weight.Bold)

        self._text = ""
        self._anchor = None        # 最近一次芸芸身体的区域（跟随用）
        self._press_pos = None     # 鼠标按下时的屏幕坐标
        self._home = QPoint()      # 拖动开始前红点窗口的位置
        self._dragging = False
        self._pop = 0.0            # "啵"动画进度 0~1
        self._loading = False      # 正在处理邮件：数字换成转圈
        self._spin = 0.0           # 转圈的角度（度）

        # 转一圈 0.9 秒，loopCount=-1：无限循环
        self._spin_anim = QVariantAnimation(self, startValue=0.0, endValue=360.0, duration=900, loopCount=-1,
                                            valueChanged=self._on_spin)
        self._pop_anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=200,
                                           valueChanged=self._on_pop_progress, finished=self._on_pop_finished)
        # 动画直接改窗口的 pos 属性；弹回时稍微过冲一点（OutBack），像橡皮筋
        self._snap_anim = QPropertyAnimation(self, b"pos", self, duration=180, easingCurve=QEasingCurve.Type.OutBack)
        self._fly_done = None   # 从角落飞回芸芸身边
        self._fly_anim = QPropertyAnimation(self, b"pos", self, duration=450, easingCurve=QEasingCurve.Type.InOutCubic,
                                            finished=self._on_fly_finished)
        # 在角落出现时淡入
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self, duration=220, startValue=0.0, endValue=1.0)

        self.set_count(0)

    @property
    def busy(self):
        """正在被拖动或正在播放动画：这时不要去挪它、藏它。"""
        running = QPropertyAnimation.State.Running
        return (self._dragging or self._pop_anim.state() == running
                or self._snap_anim.state() == running or self._fly_anim.state() == running)

    @property
    def loading(self):
        return self._loading

    def set_count(self, count):
        self._text = "99+" if count > 99 else str(count)
        self._resize_for_content()

    def set_loading(self, loading):
        """处理邮件期间：数字换成转圈，并且不响应单击 / 拖动（免得重复处理或误清除）。"""
        if loading == self._loading:
            return
        self._loading = loading
        if loading:
            self._spin_anim.start()
        else:
            self._spin_anim.stop()
        self._resize_for_content()

    def _resize_for_content(self):
        # 转圈时是个小圆；数字：一位数是圆形，多位数变成胶囊形
        width = self.HEIGHT if self._loading else max(self.HEIGHT, self._text_width() + 10)
        self.resize(width + 2 * self.PAD, self.HEIGHT + 2 * self.PAD)
        if self._anchor is not None:
            self.follow(self._anchor)
        self.update()

    # ---------- 位置 ----------

    def follow(self, body_rect):
        """放到芸芸身体（body_rect，屏幕坐标）的右上角，并保证红点不出屏幕。"""
        self._anchor = body_rect
        if not self.busy:
            self.move(self.pet_position(body_rect))

    def pet_position(self, body_rect):
        """在芸芸右上角时，小红点窗口应该在的位置。"""
        center = QPoint(body_rect.right() - self.INSET, body_rect.top() + self.INSET)
        screen = QGuiApplication.screenAt(center) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        pad = self.PAD
        x = center.x() - self.width() // 2
        y = center.y() - self.height() // 2
        # 夹的是红点本身（不含透明留白），所以上下左右都扣掉 pad
        x = max(area.left() - pad, min(x, area.left() + area.width() - self.width() + pad))
        y = max(area.top() - pad, min(y, area.top() + area.height() - self.height() + pad))
        return QPoint(x, y)

    def corner_position(self):
        """在角落时，小红点窗口应该在的位置：任务栏时钟所在的那个角（主屏幕）。放的是红点本身，不含透明留白。"""
        pad = self.PAD
        return corner_point(self.width() - 2 * pad, self.height() - 2 * pad) - QPoint(pad, pad)

    # ---------- 动画 ----------

    def fade_in(self):
        self._fade_anim.start()

    def fly_to(self, target, on_finished=None):
        """平滑飞到 target（窗口左上角的屏幕坐标），到了以后调用 on_finished。"""
        self._fly_done = on_finished
        self._start(self._fly_anim, target)

    def _start(self, animation, target):
        """（改 pos 的动画）从现在的位置移到 target。"""
        animation.setStartValue(self.pos())
        animation.setEndValue(target)
        animation.start()

    def _on_fly_finished(self):
        callback, self._fly_done = self._fly_done, None
        if callback is not None:
            callback()

    # ---------- 绘制 ----------

    def _text_width(self):
        return QFontMetrics(self._font).horizontalAdvance(self._text)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1.0 - self._pop)                  # "啵"：越来越透明
        scale = 1.0 + 0.4 * self._pop                         # 同时放大一点
        c = QRectF(self.rect()).center()
        painter.translate(c)
        painter.scale(scale, scale)
        painter.translate(-c)

        body = QRectF(self.rect()).adjusted(self.PAD, self.PAD, -self.PAD, -self.PAD)
        r = body.height() / 2
        painter.setPen(QPen(QColor("white"), 1.5))           # 白边：在深色背景上也看得清
        painter.setBrush(QColor(self.COLOR))
        painter.drawRoundedRect(body, r, r)

        if self._loading:
            # 转圈：一段 3/4 圆的白色弧线，起点随 _spin 转动
            arc = body.adjusted(4.5, 4.5, -4.5, -4.5)
            pen = QPen(QColor("white"), 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Qt 的角度单位是 1/16 度；负数 = 顺时针
            painter.drawArc(arc, int(-self._spin * 16), 270 * 16)
            return
        painter.setPen(QColor("white"))
        painter.setFont(self._font)
        painter.drawText(body, Qt.AlignmentFlag.AlignCenter, self._text)

    # ---------- 鼠标 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.busy and not self._loading:
            self._press_pos = event.globalPosition().toPoint()
            self._home = self.pos()

    def mouseMoveEvent(self, event):
        if self._press_pos is None:
            return
        delta = event.globalPosition().toPoint() - self._press_pos
        if not self._dragging and delta.manhattanLength() >= QApplication.startDragDistance():
            self._dragging = True  # 移动超过系统拖动阈值才算拖，防止单击时手抖
        if self._dragging:
            self.move(self._home + delta)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press_pos is None:
            return
        self._press_pos = None
        if not self._dragging:
            self.clicked.emit()
            return
        self._dragging = False
        if (self.pos() - self._home).manhattanLength() >= self.DISMISS_DISTANCE:
            self._pop_anim.start()         # 拖得够远：啵！
        else:
            self._start(self._snap_anim, self._home)   # 不够远：弹回去

    def contextMenuEvent(self, event):
        self.hide_requested.emit()

    # ---------- "啵"动画回调 ----------

    def _on_spin(self, value):
        self._spin = value
        self.update()

    def _on_pop_progress(self, value):
        self._pop = value
        self.update()

    def _on_pop_finished(self):
        self.hide()
        self._pop = 0.0
        self.move(self._home)  # 回到原位
        self.dismissed.emit()
