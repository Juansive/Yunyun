"""芸芸的桌宠窗口：透明背景、无边框、不占任务栏，可选"始终在上"。

窗口只负责"显示"和"鼠标交互"：
- 显示哪张图，由 set_state()（切换状态）和 tap()（敲一下键盘）决定；
- 被单击时发出 clicked 信号，至于单击要干什么，由外部（main.py）决定；
- 右键菜单：设置（语言、始终在上）/ 暂停·开始 / 退出（要快速双击才退出），文字跟随所选语言；
- 没开"始终在上"时，被别的窗口挡住会自动暂停，露出来半秒后自动恢复。
"""
from PySide6.QtCore import QPointF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n
from yunyun.pet import winapi
from yunyun.pet.bubble import BubbleStack
from yunyun.pet.menu import KeepOpenMenu
from yunyun.pet.state import PetState

_ALWAYS_ON_TOP_KEY = "window/always_on_top"


class PetWindow(QWidget):
    SIZE = 160              # 窗口边长（逻辑像素）
    MARGIN = 10             # 芸芸身体距离窗口边缘大约多远（气泡对齐、遮挡检测都用它）
    TAP_MS = 90             # 工作状态：敲下的画面停留多久后抬手（毫秒）
    LOOP_MS = 400           # 待机/激动有多张图时，每张停留多久（毫秒）
    COVER_CHECK_MS = 500    # 多久检查一次有没有被挡住（毫秒）
    COVERED_RATIO = 0.9     # 被挡住多少比例才算"被挡住"（0~1）
    RESUME_DELAY_MS = 500   # 露出来后，先定格多久再恢复（毫秒）

    clicked = Signal()            # 芸芸被单击（不是拖动）
    language_changed = Signal()   # 用户在菜单里切换了语言（托盘要更新提示文字）

    def __init__(self, sprites):
        super().__init__()

        self._always_on_top = QSettings().value(_ALWAYS_ON_TOP_KEY, False, type=bool)

        # 窗口标志（flags）可以用 | 组合在一起
        self.setWindowFlags(self._window_flags())
        # 让窗口背景透明，只有我们画出来的部分可见
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.resize(self.SIZE, self.SIZE)
        self._move_to_bottom_right()

        self._drag_offset = None  # 拖动时：鼠标相对窗口左上角的偏移；不拖动时为 None
        self._press_pos = None    # 左键按下时鼠标的屏幕坐标，用来区分"单击"和"拖动"
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # 鼠标悬停时显示"张开的手"，提示可以拖

        self._bubbles = BubbleStack(self)

        # 退出要"快速双击"：第一下启动这个定时器，定时器还在跑时点第二下才真正退出。
        # 间隔用系统的双击间隔（Windows 默认 500 毫秒），和用户平时双击的习惯一致。
        self._exit_timer = QTimer(self)
        self._exit_timer.setSingleShot(True)
        self._exit_timer.setInterval(QApplication.doubleClickInterval())
        self._exit_timer.timeout.connect(self._on_single_exit_click)

        # ---- 暂停：两个"原因"分开记，任意一个成立就定格 ----
        self._user_paused = False   # 用户在菜单里点了"暂停"
        self._auto_paused = False   # 被别的窗口挡住，自动暂停
        self._frozen = False        # 画面此刻是否定格（= 上面两个任意一个为 True）
        self._occluded = False      # 最近一次检测：是不是被挡住了

        self._cover_timer = QTimer(self)
        self._cover_timer.setInterval(self.COVER_CHECK_MS)
        self._cover_timer.timeout.connect(self._check_covered)
        self._cover_timer.start()

        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.setInterval(self.RESUME_DELAY_MS)
        self._resume_timer.timeout.connect(self._auto_resume)

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

    def _window_flags(self):
        flags = (
            Qt.WindowType.FramelessWindowHint      # 去掉标题栏和边框
            | Qt.WindowType.Tool                   # 工具窗口：不在任务栏显示
        )
        if self._always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint  # 始终在其他窗口上方
        return flags

    # ---------- 状态与动画 ----------

    def set_state(self, state):
        """切换到某个状态，显示对应的画面。"""
        self._state = state
        if self._frozen:
            return  # 暂停中：只记下最新状态，画面不动
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
        if self._frozen or self._state is not PetState.WORKING:
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

    # ---------- 暂停 ----------

    @property
    def paused(self):
        """用户是否手动暂停了（菜单上显示"开始"还是"暂停"看这个）。"""
        return self._user_paused

    def set_paused(self, paused):
        """用户手动暂停 / 恢复。"""
        self._user_paused = paused
        self._update_frozen()

    def _update_frozen(self):
        """根据两个暂停原因，决定画面要不要定格。"""
        frozen = self._user_paused or self._auto_paused
        if frozen == self._frozen:
            return
        self._frozen = frozen
        if frozen:
            self._loop_timer.stop()   # 定格：停掉所有动画定时器，不再重绘
            self._tap_timer.stop()
        else:
            self.set_state(self._state)  # 暂停期间状态可能变过，用最新的状态重新显示

    # ---------- 始终在上 & 遮挡检测 ----------

    @property
    def always_on_top(self):
        return self._always_on_top

    def set_always_on_top(self, on):
        self._always_on_top = bool(on)
        QSettings().setValue(_ALWAYS_ON_TOP_KEY, self._always_on_top)

        # 直接 setWindowFlags() 会让窗口先隐藏再重建（闪一下），所以分两步：
        # 1) overrideWindowFlags 只更新 Qt 记录的标志，不重建窗口；
        # 2) 再让 Windows 真正改变置顶状态。
        self.overrideWindowFlags(self._window_flags())
        winapi.set_topmost(int(self.winId()), self._always_on_top)
        self._check_covered()

    def bring_to_front(self):
        """把芸芸提到最前面（托盘双击时用）。如果芸芸跑到屏幕外了，先拉回来。"""
        if QGuiApplication.screenAt(self._body_rect().center()) is None:
            self._move_to_bottom_right()
        self.show()
        winapi.bring_to_front(int(self.winId()))
        self._check_covered()  # 马上检测一次，不用等下一个周期

    def is_visible_to_user(self):
        """用户此刻能不能看到芸芸。"""
        return self.isVisible() and not self._occluded

    def _check_covered(self):
        """定时调用：看看芸芸有没有被别的窗口挡住。"""
        if self._always_on_top or not self.isVisible():
            self._occluded = False
        else:
            inset = round(self.MARGIN * self.devicePixelRatioF())  # Windows API 用物理像素
            self._occluded = winapi.covered_ratio(int(self.winId()), inset) >= self.COVERED_RATIO

        if self._occluded:
            self._resume_timer.stop()   # 露出来又被挡住了：取消"即将恢复"
            if not self._auto_paused:
                self._auto_paused = True
                self._update_frozen()
        elif self._auto_paused and not self._resume_timer.isActive():
            self._resume_timer.start()  # 露出来了：先定格半秒，再恢复

    def _auto_resume(self):
        self._auto_paused = False
        self._update_frozen()

    # ---------- 气泡 ----------

    def say(self, text):
        """让芸芸弹出气泡说一句话（已有气泡时会再加一个，互不重叠）。"""
        self._bubbles.add(text, self._body_rect())

    def _body_rect(self):
        """芸芸身体在屏幕上的大致区域，不含透明留白。"""
        m = self.MARGIN
        return self.geometry().adjusted(m, m, -m, -m)

    def moveEvent(self, event):
        # 窗口每次移动后 Qt 都会调用它：拖动时让气泡跟着走
        self._bubbles.follow(self._body_rect())

    def _move_to_bottom_right(self):
        """把芸芸放在屏幕右下角（避开任务栏）。"""
        area = QGuiApplication.primaryScreen().availableGeometry()  # 可用区域，不含任务栏
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

    # ---------- 右键菜单 ----------

    def contextMenuEvent(self, event):
        """右键时 Qt 会调用它：弹出菜单，直到菜单关闭才返回。"""
        self._build_menu().exec(event.globalPos())

    def _build_menu(self):
        """每次右键都重新创建菜单，这样菜单的语言、"暂停/开始"、勾选状态总是最新的。"""
        tr = i18n.tr
        menu = KeepOpenMenu(self)
        settings_menu = menu.addMenu(tr("menu_settings"))

        # 设置 → 语言 → 中文 / English / 日本語（单选）
        # 语言选项始终用各自的母语显示，切到看不懂的语言时也能找回来
        language_menu = settings_menu.addMenu(tr("menu_language"))
        group = QActionGroup(menu)  # 放进同一个组的可勾选动作，同时只能勾一个
        for code, name in i18n.LANGUAGES.items():
            action = language_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(code == i18n.current_language())
            action.setActionGroup(group)
            # 注意 c=code：用默认参数把"这一轮的 code"固定下来。
            # 如果直接写 lambda: self._change_language(code)，三个菜单项都会用到最后一个 code。
            action.triggered.connect(lambda checked=False, c=code: self._change_language(c))

        # 设置 → 始终在上（可勾选：勾上就是"按下去"的状态）
        on_top = settings_menu.addAction(tr("menu_always_on_top"))
        on_top.setCheckable(True)
        on_top.setChecked(self._always_on_top)
        on_top.triggered.connect(self.set_always_on_top)  # triggered 会带上勾选后的 True/False

        menu.addAction(tr("menu_resume") if self._user_paused else tr("menu_pause"), self._toggle_pause)
        menu.addSeparator()

        exit_action = menu.addAction(tr("menu_quit"))
        exit_action.triggered.connect(lambda: self.request_exit(menu))
        menu.keep_open_action = exit_action
        return menu

    def _change_language(self, code):
        i18n.set_language(code)
        self.language_changed.emit()
        self.say(i18n.tr("language_changed"))

    def _toggle_pause(self):
        self.set_paused(not self._user_paused)

    # ---------- 退出 ----------

    def request_exit(self, menu):
        """菜单里点了一下"退出"（芸芸本体菜单和托盘菜单共用）。"""
        if self._exit_timer.isActive():
            # 双击间隔内点了第二下：真的退出
            self._exit_timer.stop()
            menu.close()
            QApplication.instance().quit()
        else:
            # 第一下：先等等看有没有第二下
            self._exit_timer.start()

    def _on_single_exit_click(self):
        # 只点了一下：用户看得到芸芸才弹提示（被挡住时弹了也看不到）
        if self.is_visible_to_user():
            self.say(i18n.tr("exit_hint"))
