"""芸芸的桌宠窗口：透明背景、无边框、始终置顶、不占任务栏。

窗口只负责"显示"和"鼠标交互"：
- 显示哪张图，由 set_state()（切换状态）和 tap()（敲一下键盘）决定；
- 被单击时发出 clicked 信号，至于单击要干什么，由外部（main.py）决定。
"""
from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QWidget

from yunyun.pet.bubble import Bubble
from yunyun.pet.state import PetState


class PetWindow(QWidget):
    SIZE = 160      # 窗口边长（逻辑像素）
    MARGIN = 10     # 气泡对齐用：芸芸身体距离窗口边缘大约多远
    TAP_MS = 90     # 工作状态：敲下的画面停留多久后抬手（毫秒）
    LOOP_MS = 400   # 待机/激动有多张图时，每张停留多久（毫秒）

    clicked = Signal()  # 芸芸被单击（不是拖动）

    def __init__(self, sprites):
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

        self._drag_offset = None  # 拖动时：鼠标相对窗口左上角的偏移；不拖动时为 None
        self._press_pos = None    # 左键按下时鼠标的屏幕坐标，用来区分"单击"和"拖动"
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # 鼠标悬停时显示"张开的手"，提示可以拖

        self._bubble = Bubble()

        # ---- 动画相关 ----
        self._sprites = sprites
        self._state = None
        self._current = None      # 当前显示的原图（QPixmap）
        self._scaled_cache = {}   # 缩放后的图缓存，避免每次重绘都重新缩放
        self._loop_frames = []    # 待机/激动：循环播放的帧
        self._loop_index = 0
        self._down_index = 0      # 工作：下一次敲击用第几张"敲下"图

        self._loop_timer = QTimer(self)
        self._loop_timer.setInterval(self.LOOP_MS)
        self._loop_timer.timeout.connect(self._next_loop_frame)

        self._tap_timer = QTimer(self)
        self._tap_timer.setSingleShot(True)
        self._tap_timer.setInterval(self.TAP_MS)
        self._tap_timer.timeout.connect(self._hands_up)

        self.set_state(PetState.IDLE)

    # ---------- 状态与动画 ----------

    def set_state(self, state):
        """切换到某个状态，显示对应的画面。"""
        self._state = state
        self._loop_timer.stop()
        self._tap_timer.stop()

        if state is PetState.WORKING:
            self._show(self._sprites.working_up)
            return

        frames = self._sprites.idle if state is PetState.IDLE else self._sprites.excited
        self._loop_frames = frames
        self._loop_index = 0
        self._show(frames[0])
        if len(frames) > 1:       # 只有一张图就不用循环
            self._loop_timer.start()

    def tap(self):
        """敲一下键盘：显示"敲下"的画面，TAP_MS 毫秒后自动抬手。"""
        if self._state is not PetState.WORKING:
            return
        downs = self._sprites.working_down
        self._show(downs[self._down_index % len(downs)])  # 多张"敲下"图轮流用（比如左右手交替）
        self._down_index += 1
        self._tap_timer.start()  # 打字很快时，每次按键都重新计时，手会一直"敲"着

    def _hands_up(self):
        if self._state is PetState.WORKING:
            self._show(self._sprites.working_up)

    def _next_loop_frame(self):
        self._loop_index = (self._loop_index + 1) % len(self._loop_frames)
        self._show(self._loop_frames[self._loop_index])

    def _show(self, pixmap):
        self._current = pixmap
        self.update()

    # ---------- 气泡 ----------

    def say(self, text):
        """让芸芸弹出气泡说一句话。"""
        self._bubble.show_text(text, self._body_rect())

    def _body_rect(self):
        """芸芸身体在屏幕上的大致区域，不含透明留白。"""
        m = self.MARGIN
        return self.geometry().adjusted(m, m, -m, -m)

    def moveEvent(self, event):
        # 窗口每次移动后 Qt 都会调用它：拖动时让气泡跟着走
        if self._bubble.isVisible():
            self._bubble.follow(self._body_rect())

    def _move_to_bottom_right(self):
        """把芸芸放在屏幕右下角（避开任务栏）。"""
        area = self.screen().availableGeometry()  # 可用区域，不含任务栏
        self.move(area.right() - self.SIZE - 40, area.bottom() - self.SIZE - 40)

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        """Qt 每次需要重绘窗口时都会调用这个方法。"""
        if self._current is None:
            return
        pixmap = self._scaled(self._current)
        size = pixmap.deviceIndependentSize()  # 换算回逻辑像素的大小
        x = (self.width() - size.width()) / 2   # 居中
        y = (self.height() - size.height()) / 2
        QPainter(self).drawPixmap(QPointF(x, y), pixmap)

    def _scaled(self, pixmap):
        """把原图缩放到窗口大小。

        高分屏（比如 150% 缩放）上，160 个逻辑像素实际是 240 个物理像素，
        所以按"物理像素"缩放，再告诉 Qt 这张图的像素密度（devicePixelRatio），图才清晰。
        """
        dpr = self.devicePixelRatioF()
        key = (pixmap.cacheKey(), dpr)
        if key not in self._scaled_cache:
            target = QSize(round(self.width() * dpr), round(self.height() * dpr))
            scaled = pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,           # 保持长宽比
                Qt.TransformationMode.SmoothTransformation,   # 平滑缩放，不出锯齿
            )
            scaled.setDevicePixelRatio(dpr)
            self._scaled_cache[key] = scaled
        return self._scaled_cache[key]

    # ---------- 鼠标交互 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # 记下"鼠标点在窗口里的哪个位置"，拖动时保持这个相对位置不变，
            # 否则窗口左上角会突然跳到鼠标下面。
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button() == Qt.MouseButton.RightButton:
            # 临时：右键直接退出。之后会换成正式的右键菜单（设置 / 暂停 / 退出）。
            QApplication.instance().quit()

    def mouseMoveEvent(self, event):
        # buttons()（复数）返回"此刻按住了哪些键"；只有按住左键时才拖动
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            # 松开时鼠标离按下位置很近 → 算单击；否则是一次拖动。
            # startDragDistance() 是系统的"拖动阈值"（Windows 上一般约 10 像素），
            # 用它可以容忍手抖。manhattanLength() = |dx| + |dy|，比算直线距离便宜。
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if moved < QApplication.startDragDistance():
                self.clicked.emit()

            self._drag_offset = None
            self._press_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
