"""芸芸的桌宠窗口：透明背景、无边框、不占任务栏，可选"始终在上"。

窗口只负责"显示"和"鼠标交互"：
- 显示哪张图，由 set_state()（切换状态）和 tap()（敲一下键盘）决定；
- 被单击时发出 clicked 信号，至于单击要干什么，由外部（main.py）决定；
- 右键菜单：设置… / 全部标为已看 / 暂停·开始 / 退出（要快速双击才退出），文字跟随所选语言；
  所有设置都在设置窗口（pet/settings_window.py）里；
- 没开"始终在上"时，被别的窗口挡住会自动暂停，露出来半秒后自动恢复。
"""
import logging
import math
import threading

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRect, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from yunyun import i18n, logs, shortcut
from yunyun.ai import settings as ai_settings
from yunyun.pet import winapi
from yunyun.pet.badge import Badge
from yunyun.pet.bubble import BubbleStack
from yunyun.pet.card_deck import CardDeck, MailCard
from yunyun.pet.menu import KeepOpenMenu
from yunyun.pet.state import PetState
from yunyun.pet.sway import Sway

_ALWAYS_ON_TOP_KEY = "window/always_on_top"
_POSITION_KEY = "window/position"
_AI_OFFERED_KEY = "onboarding/ai_offered"  # 是否已经引导过"设置 AI"（只引导一次）

log = logging.getLogger(__name__)


class PetWindow(QWidget):
    SIZE = 160              # 窗口边长（逻辑像素）
    MARGIN = 10             # 芸芸身体距离窗口边缘大约多远（气泡对齐、遮挡检测都用它）
    TAP_MS = 90             # 工作状态：敲下的画面停留多久后抬手（毫秒）
    LOOP_MS = 400           # 待机/激动有多张图时，每张停留多久（毫秒）
    COVER_CHECK_MS = 500    # 多久检查一次有没有被挡住（毫秒）
    COVERED_RATIO = 0.9     # 被挡住多少比例才算"被挡住"（0~1）
    RESUME_DELAY_MS = 500   # 露出来后，先定格多久再恢复（毫秒）
    SHORT_SAY_MS = 2000     # 简短提示（比如"现在没有新邮件"）显示多久（毫秒）
    SAVE_POS_DELAY_MS = 500 # 拖动停下多久后保存位置（毫秒）
    SWAY_TICK_MS = 16       # 被拎着摆动时多久算一帧（约每秒 60 帧，只在拎着的时候运行）

    clicked = Signal()            # 芸芸被单击（不是拖动）
    language_changed = Signal()   # 用户切换了语言（托盘、设置窗口要更新文字）
    background_task_finished = Signal()  # 后台任务（创建快捷方式、开机启动）做完了：设置窗口据此刷新勾选状态
    mail_accounts_changed = Signal()  # 用户添加 / 删除了邮箱账号
    mail_badge_clicked = Signal()     # 用户左键点了小红点：要处理邮件
    dismiss_mail_requested = Signal() # 用户拖走了小红点 / 点了"全部标为已看"：这批邮件不处理
    _task_done = Signal(object, object, object)  # 后台线程 → 主线程：(成功时说的话, 失败时的文字 key, 错误信息或 None)

    def __init__(self, sprites):
        super().__init__()

        self._always_on_top = QSettings().value(_ALWAYS_ON_TOP_KEY, False, type=bool)

        # 窗口标志（flags）可以用 | 组合在一起
        self.setWindowFlags(self._window_flags())
        # 让窗口背景透明，只有我们画出来的部分可见
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.resize(self.SIZE, self.SIZE)

        # 记住位置：拖动停下半秒后才保存（不用每移动一个像素就写一次）
        self._save_pos_timer = QTimer(self)
        self._save_pos_timer.setSingleShot(True)
        self._save_pos_timer.setInterval(self.SAVE_POS_DELAY_MS)
        self._save_pos_timer.timeout.connect(self.save_position)
        self._restore_position()

        self._drag_offset = None  # 拖动时：鼠标相对窗口左上角的偏移；不拖动时为 None
        self._press_pos = None    # 左键按下时鼠标的屏幕坐标，用来区分"单击"和"拖动"
        self.setCursor(Qt.CursorShape.OpenHandCursor)  # 鼠标悬停时显示"张开的手"，提示可以拖

        self._bubbles = BubbleStack(self)
        self._settings_window = None  # 设置窗口（用到时才创建，关掉就销毁）
        self._history = None          # 邮件提醒的历史记录（main 里设置）
        self._quiet_hours = None      # 免打扰时段（main 里设置）

        # ---- 小红点 ----
        self._mail_count = 0
        self._mail_processing = False     # 正在处理邮件：小红点变成转圈
        self._badge_hidden = False  # 用户右键隐藏了小红点（只在本次运行有效，重启后恢复）
        self._badge_wants_corner = False  # 芸芸被挡住时来了新邮件：小红点该去任务栏时钟上方
        self._badge = Badge(self)                            # 跟随用：芸芸的从属窗口
        self._corner_badge = Badge(None, topmost=True)       # 角落用：独立的置顶窗口
        for badge in (self._badge, self._corner_badge):
            badge.clicked.connect(self._on_badge_clicked)
            badge.dismissed.connect(self._on_badge_dismissed)
            badge.hide_requested.connect(self._hide_badge)

        # ---- 卡片堆：点小红点后，处理好的邮件一张张出现在这里 ----
        self._deck = CardDeck(self)
        self._deck.closed.connect(self._on_deck_closed)
        self._deck_accepting = False  # 这一轮处理的结果还要不要放进卡片堆（用户中途收起就不要了）

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
        self._held_keys = 0       # 工作：还按着没松开的键有几个（按住不放时手一直落着）
        self._dragging = False    # 正在拖动（拖过了系统的拖动阈值才算，手抖一下不算）
        self._lifted = False      # 正在显示"被拎起来"的画面（拖动中，并且有这组图）
        self._frame_before_lift = None  # 暂停时被拎起来：放下后要回到原来定格的那一帧

        self._loop_timer = QTimer(self)
        self._loop_timer.setInterval(self.LOOP_MS)
        self._loop_timer.timeout.connect(self._next_loop_frame)

        self._tap_timer = QTimer(self)
        self._tap_timer.setSingleShot(True)
        self._tap_timer.setInterval(self.TAP_MS)
        self._tap_timer.timeout.connect(self._hands_up)

        # 被拎着时左右摆动（像拎着小猫走）：角度由 Sway 的小物理模型算，这里只负责定时和重绘
        self._sway = Sway()
        self._sway_clock = QElapsedTimer()   # 量两帧之间实际隔了多久（定时器不是绝对准时的）
        self._sway_timer = QTimer(self)
        self._sway_timer.setInterval(self.SWAY_TICK_MS)
        self._sway_timer.timeout.connect(self._sway_tick)

        self._task_done.connect(self._on_task_done)
        self.set_state(PetState.IDLE)

    @classmethod
    def sprite_pixels(cls):
        """图片加载时缩小到多大（物理像素），给 load_sprites(max_size=...) 用。

        要够"缩放比例最高的那块屏幕"用，并且至少按 200% 算——这样以后插上 4K 屏，芸芸也是清晰的。
        """
        ratio = max([2.0] + [screen.devicePixelRatio() for screen in QGuiApplication.screens()])
        return math.ceil(cls.SIZE * ratio)

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
        self._held_keys = 0  # 换了状态：之前"按着的键"不再影响手的姿势
        if self._frozen or self._lifted:
            return  # 暂停中 / 被拎着：只记下最新状态，画面不动（恢复 / 放下时再显示）
        self._loop_timer.stop()
        self._tap_timer.stop()

        if state is PetState.WORKING:
            self._show(self._sprites.working_up)
            return

        self._play_loop({
            PetState.IDLE: self._sprites.idle,
            PetState.EXCITED: self._sprites.excited,
            PetState.THINKING: self._sprites.thinking,
            PetState.SLEEPING: self._sprites.sleeping,
        }[state])

    def tap(self):
        """按下一个键：显示"敲下"的画面。手至少落下 TAP_MS 毫秒；键还按着就一直落着，松开才抬手。"""
        if self._frozen or self._lifted or self._state is not PetState.WORKING:
            return
        downs = self._sprites.working_down
        self._show(downs[self._down_index % len(downs)])  # 多张"敲下"图轮流用（比如左右手交替）
        self._down_index += 1
        self._held_keys += 1
        self._tap_timer.start()  # 打字很快时，每次按键都重新计时，手会一直"敲"着

    def release(self):
        """松开一个键。所有键都松开了，并且手已经落够 TAP_MS 了，就抬手。"""
        if self._held_keys == 0:
            return  # 这个键是在别的状态下按的（或者暂停时），和现在的手没关系
        self._held_keys -= 1
        if self._held_keys == 0 and not self._tap_timer.isActive():
            self._hands_up()

    def _hands_up(self):
        if self._held_keys > 0:
            return  # 还有键按着不放：手保持落下（就像真的一直按着）
        if self._state is PetState.WORKING and not self._frozen and not self._lifted:
            self._show(self._sprites.working_up)

    def _play_loop(self, frames):
        """从第一张开始显示一组图；多于一张就循环播放。"""
        self._loop_frames = frames
        self._loop_index = 0
        self._show(frames[0])
        if len(frames) > 1:       # 只有一张图就不用循环
            self._loop_timer.start()

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
        elif self._lifted:
            self._play_loop(self._sprites.lifted)  # 拎着的时候恢复了：接着播放"被拎起来"
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

        # 小红点的角落模式：芸芸露出来了，就飞回芸芸身边；
        # 还被挡着，就顺便检查一下用户是不是进入 / 退出了全屏
        if self._badge_wants_corner:
            if not self._occluded:
                self._badge_wants_corner = False
                if self._corner_badge.isVisible() and not self._corner_badge.busy:
                    # 角落的小红点飞到芸芸右上角，落地后换成跟随用的那个（位置完全重合）
                    target = self._badge.pet_position(self._body_rect())
                    self._corner_badge.fly_to(target, on_finished=self._update_badge)
                    return
            self._update_badge()

    def _auto_resume(self):
        self._auto_paused = False
        self._update_frozen()

    # ---------- 气泡 ----------

    def say(self, text, duration_ms=4000):
        """让芸芸弹出气泡说一句话，duration_ms 毫秒后消失（已有气泡时会再加一个，互不重叠）。"""
        self._bubbles.add(text, self._body_rect(), duration_ms)

    def tell_no_new_mail(self):
        """告诉用户现在没有新邮件（显示一小会儿）。"""
        self.say(i18n.tr("no_new_mail"), self.SHORT_SAY_MS)

    def _body_rect(self):
        """芸芸身体在屏幕上的大致区域，不含透明留白。"""
        m = self.MARGIN
        return self.geometry().adjusted(m, m, -m, -m)

    def moveEvent(self, event):
        # 窗口每次移动后 Qt 都会调用它：拖动时让气泡、小红点、卡片堆跟着走
        self._bubbles.follow(self._body_rect())
        self._badge.follow(self._body_rect())
        self._deck.follow(self._body_rect())
        if self.isVisible():
            self._save_pos_timer.start()  # 每次移动都重新计时：停下半秒后才真正保存

    # ---------- 记住位置 ----------

    def save_position(self):
        self._save_pos_timer.stop()
        QSettings().setValue(_POSITION_KEY, self.pos())

    def _restore_position(self):
        """回到上次的位置；上次的位置现在不在任何屏幕上（比如拔了外接显示器），就回右下角。"""
        saved = QSettings().value(_POSITION_KEY)
        if isinstance(saved, QPoint):
            m = self.MARGIN
            body = QRect(saved, self.size()).adjusted(m, m, -m, -m)
            if QGuiApplication.screenAt(body.center()) is not None:
                self.move(saved)
                return
        self._move_to_bottom_right()

    # ---------- 卡片堆 ----------

    def begin_mail_batch(self):
        """开始新的一轮处理：清空卡片堆，接收这一轮的结果。"""
        self._deck.reset()
        self._deck_accepting = True

    @property
    def deck_accepting(self):
        """这一轮的结果还要不要放进卡片堆。用户中途收起了卡片堆，就是 False（剩下的邮件下次再看）。"""
        return self._deck_accepting

    def set_history(self, history):
        self._history = history

    def set_quiet_hours(self, quiet):
        self._quiet_hours = quiet

    @property
    def quiet_hours(self):
        return self._quiet_hours

    @property
    def history(self):
        return self._history

    def show_recent_mail(self):
        """右键菜单"刚才的邮件"：把最近一批卡片重新展开成卡片堆，和当时看到的一样。"""
        if self._history is None or self._mail_processing:
            return
        entries = self._history.latest_batch()
        if not entries:
            return
        self._deck.reset()
        self._deck_accepting = False  # 这是回看历史，不是正在处理的一轮
        for e in entries:
            self._deck.add_card(MailCard(e.bubble, e.subject, e.sender, e.date, e.attachments), self._body_rect())

    def add_mail_card(self, card):
        self._deck.add_card(card, self._body_rect())

    def set_mail_note(self, text):
        """卡片堆底部的小字提示（比如"还没设置 AI"）。"""
        self._deck.set_note(text)

    def _on_deck_closed(self):
        if self._mail_processing:
            self._deck_accepting = False  # 还在处理就被收起了：后面的结果不放进来，也不算处理过

    # ---------- 小红点 ----------

    def set_mail_count(self, count):
        """更新待处理邮件数（由邮件服务通知）。"""
        self._mail_count = count
        self._badge.set_count(count)
        self._corner_badge.set_count(count)
        self._update_badge()

    def set_mail_processing(self, processing):
        """开始 / 结束处理邮件：小红点在数字和转圈之间切换。"""
        self._mail_processing = processing
        self._badge.set_loading(processing)
        self._corner_badge.set_loading(processing)
        self._update_badge()

    def on_new_mail(self):
        """有新邮件到达（由 main 调用）。芸芸被挡住时，小红点单独去任务栏时钟上方等你，芸芸不出来。"""
        self._check_covered()  # 定时检查最多有 0.5 秒的延迟，这里马上重新确认一次
        if self._occluded and not self._badge_hidden:
            self._badge_wants_corner = True
        self._update_badge()

    def _update_badge(self):
        """根据邮件数、是否被隐藏、是否在角落模式，决定显示哪个小红点。"""
        if self._badge.busy or self._corner_badge.busy:
            return  # 正在拖动 / 播放动画，别打扰它（动画结束后会再调用这里）
        # 处理中即使邮件数已经减到 0，也要一直显示转圈，直到处理完
        has_something = self._mail_count > 0 or self._mail_processing
        show = has_something and not self._badge_hidden and self.isVisible()
        if not show:
            self._badge_wants_corner = False
            self._badge.hide()
            self._corner_badge.hide()
        elif self._badge_wants_corner:
            self._badge.hide()  # 跟随的那个反正被挡着，先藏起来
            if winapi.is_user_busy():
                self._corner_badge.hide()   # 用户在全屏：只计数，不弹出；退出全屏后再出现
            else:
                appearing = not self._corner_badge.isVisible()
                self._corner_badge.move(self._corner_badge.corner_position())
                self._corner_badge.show()   # 独立窗口：显示、置顶都不会把芸芸带出来
                if appearing:
                    self._corner_badge.fade_in()
        else:
            self._corner_badge.hide()
            self._badge.follow(self._body_rect())
            # 显示从属窗口会把芸芸也拉到最前面（芸芸被挡住时就会自己"跳"出来），
            # 所以用 keep_z_order 包起来，显示完把芸芸放回原位。也不要调用 raise_()。
            with winapi.keep_z_order(int(self.winId())):
                self._badge.show()

    def _on_badge_clicked(self):
        # 卡片堆开着时点小红点 = 收起卡片堆。
        # （点小红点会让卡片堆先失去焦点、自己收起，所以"刚刚收起"的也算）
        if self._deck.isVisible() or self._deck.recently_closed():
            self._deck.close_deck()
            return
        if self._corner_badge.isVisible():
            self.bring_to_front()  # 在角落点它 = 想看邮件了：先把芸芸叫出来，结果才看得到
        self.mail_badge_clicked.emit()

    def _hide_badge(self):
        self._badge_hidden = True
        self._update_badge()
        self.say(i18n.tr("badge_hidden_hint"))

    def _restore_badge(self):
        if self._badge_hidden:
            self._badge_hidden = False
            self._update_badge()
            if self._mail_count == 0:
                # 隐藏期间邮件已经被处理掉了（比如在手机上读了）：说一声，免得以为小红点"没回来"
                self.tell_no_new_mail()

    def _on_badge_dismissed(self):
        self._dismiss_mail()

    def _dismiss_mail(self):
        count = self._mail_count
        self.dismiss_mail_requested.emit()  # 邮件服务清零后会调用 set_mail_count(0)
        if count:
            self.say(i18n.tr("mail_dismissed", count))

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
        painter = QPainter(self)
        if self._lifted and self._sway.angle:
            # 绕"后颈"（头顶正中）转一个角度：先把原点挪到支点，转，再挪回来
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)   # 转动后边缘不出锯齿
            pivot = QPointF(self.width() / 2, self.MARGIN)
            painter.translate(pivot)
            painter.rotate(self._sway.angle)
            painter.translate(-pivot)
        painter.drawPixmap(QPointF(x, y), pixmap)

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
            if self._dragging:
                self._end_drag()  # 上次拖动的"松开"消息丢了（比如拖到一半切换了窗口）：先当作已经放下
            # 记下"鼠标点在窗口里的哪个位置"，拖动时保持这个相对位置不变，
            # 否则窗口左上角会突然跳到鼠标下面。
            self._press_pos = event.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        # buttons()（复数）返回"此刻按住了哪些键"；只有按住左键时才拖动
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_offset is not None:
            pos = event.globalPosition().toPoint()
            # startDragDistance() 是系统的"拖动阈值"（Windows 上一般约 10 像素），用它可以容忍手抖。
            # manhattanLength() = |dx| + |dy|，比算直线距离便宜。
            if not self._dragging and (pos - self._press_pos).manhattanLength() >= QApplication.startDragDistance():
                self._begin_drag()
            self.move(pos - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            # 没拖过阈值、松开时离按下的位置也很近 → 算单击；否则是一次拖动
            moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
            if not self._dragging and moved < QApplication.startDragDistance():
                self.clicked.emit()
            if self._dragging:
                self._end_drag()

            self._drag_offset = None
            self._press_pos = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _begin_drag(self):
        """开始拖动：有"被拎起来"的图就换上（像拎小猫的后颈），没有就保持原来的画面。"""
        self._dragging = True
        if not self._sprites.lifted:
            return
        self._lifted = True
        self._frame_before_lift = self._current
        self._loop_timer.stop()
        self._tap_timer.stop()
        if self._frozen:
            self._show(self._sprites.lifted[0])   # 暂停中：换成拎起来的画面，但不播放动画
        else:
            self._play_loop(self._sprites.lifted)
        self._sway.reset(self.x())
        self._sway_clock.start()
        self._sway_timer.start()

    def _end_drag(self):
        """放下：回到当前的状态（拖动期间状态可能变过，比如来了新邮件）。"""
        self._dragging = False
        if not self._lifted:
            return
        self._lifted = False
        self._sway_timer.stop()
        self._sway.reset()                         # 放下就站直
        self._loop_timer.stop()
        if self._frozen:
            self._show(self._frame_before_lift)    # 暂停中：回到原来定格的那一帧
        else:
            self.set_state(self._state)
        self._frame_before_lift = None
        self.update()

    def _sway_tick(self):
        """拎着的时候每一帧调用：按拖动的速度算出摆动的角度，角度变了才重绘。"""
        if not self._left_button_down():
            self._end_drag()   # "松开"的消息丢了（比如拖到一半切换了窗口）：当作已经放下
            return
        before = self._sway.angle
        self._sway.update(self.x(), self._sway_clock.restart() / 1000)  # restart() 返回经过的毫秒数
        if abs(self._sway.angle - before) > 0.01:
            self.update()

    def _left_button_down(self):
        return bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    # ---------- 右键菜单 ----------

    def contextMenuEvent(self, event):
        """右键时 Qt 会调用它：弹出菜单，直到菜单关闭才返回。"""
        self._restore_badge()  # 右键芸芸 = 把隐藏的小红点找回来
        self._build_menu().exec(event.globalPos())

    def _build_menu(self):
        """每次右键都重新创建菜单，这样菜单的语言、"暂停/开始"、勾选状态总是最新的。"""
        tr = i18n.tr
        menu = KeepOpenMenu(self)
        # "设置…"：所有设置都在设置窗口里（"…"表示点了会打开一个窗口，这是桌面软件的惯例）
        menu.addAction(tr("menu_settings"), self.open_settings)
        # 刚才的邮件：把最近一批卡片重新展开（没有历史、或正在处理邮件时变灰）
        recent = menu.addAction(tr("menu_recent_mail"), self.show_recent_mail)
        recent.setEnabled(self._history is not None and not self._history.is_empty() and not self._mail_processing)

        dismiss = menu.addAction(tr("menu_dismiss_mail"), self._dismiss_mail)
        dismiss.setEnabled(self._mail_count > 0)  # 没有待处理邮件时变灰
        menu.addAction(tr("menu_resume") if self._user_paused else tr("menu_pause"), self._toggle_pause)
        menu.addSeparator()

        exit_action = menu.addAction(tr("menu_quit"))
        exit_action.triggered.connect(lambda: self.request_exit(menu))
        menu.keep_open_action = exit_action
        return menu

    def open_settings(self, page="general"):
        """打开设置窗口并翻到某一页；已经打开了就翻页并提到前面。"""
        if self._settings_window is None:
            # 用到时才导入：设置窗口和它的各个页面要占两三 MB 内存，平时用不着
            from yunyun.pet.settings_window import SettingsWindow
            # parent 设为芸芸：窗口和芸芸同一层（芸芸置顶时它也在最上面）
            self._settings_window = SettingsWindow(self)
            self._settings_window.accounts_changed.connect(self.mail_accounts_changed)
            self._settings_window.account_added.connect(self._on_account_added)
            self._settings_window.finished.connect(self._on_settings_closed)
            self._settings_window.show_page(page)
            self._settings_window.show_centered()
        else:
            self._settings_window.show_page(page)
            self._settings_window.raise_()
            self._settings_window.activateWindow()

    def open_mail_accounts(self):
        self.open_settings("mail")

    def open_ai_settings(self):
        self.open_settings("ai")

    @property
    def settings_window(self):
        return self._settings_window

    def _on_settings_closed(self):
        self._settings_window.deleteLater()
        self._settings_window = None

    def _on_account_added(self):
        QTimer.singleShot(600, self._maybe_offer_ai_setup)  # 先让用户看清"添加成功"，再翻页

    def _maybe_offer_ai_setup(self):
        """首次引导：刚添加了邮箱、还没设置 AI、以前也没引导过 → 翻到 AI 页（只引导这一次）。"""
        settings = QSettings()
        if settings.value(_AI_OFFERED_KEY, False, type=bool) or ai_settings.load_settings() is not None:
            return
        settings.setValue(_AI_OFFERED_KEY, True)
        self.say(i18n.tr("onboarding_ai"), 8000)  # 话比较长，多显示一会儿
        self.open_ai_settings()

    def create_desktop_shortcut(self):
        """在桌面创建快捷方式。"""
        icon, description = self._sprites.tray, i18n.tr("shortcut_description")
        self._run_in_background(lambda: shortcut.create_desktop_shortcut(icon, description),
                                i18n.tr("shortcut_created"), "shortcut_failed")

    def set_autostart(self, enabled):
        """开机时启动：在"启动"文件夹里放 / 删芸芸的快捷方式。"""
        icon, description = self._sprites.tray, i18n.tr("shortcut_description")
        self._run_in_background(lambda: shortcut.set_autostart(enabled, icon, description),
                                i18n.tr("autostart_on" if enabled else "autostart_off"), "autostart_failed")

    def _run_in_background(self, job, ok_text, fail_key):
        """在后台线程里做一件可能要一两秒的事（比如调用 PowerShell），做完让芸芸说一句。菜单不会卡住。"""
        def worker():
            try:
                job()
                error = None
            except Exception as e:  # 包括 ShortcutError、超时、没有 PowerShell 等
                log.warning("%s 失败：%s", fail_key, e)
                error = str(e)
            try:
                self._task_done.emit(ok_text, fail_key, error)
            except RuntimeError:
                pass  # 程序正在退出

        threading.Thread(target=worker, daemon=True).start()

    def _on_task_done(self, ok_text, fail_key, error):
        self.say(ok_text if error is None else i18n.tr(fail_key, error[:60]))
        self.background_task_finished.emit()

    def open_log_folder(self):
        folder = logs.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))  # 用资源管理器打开

    def change_language(self, code):
        if code == i18n.current_language():
            return
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
