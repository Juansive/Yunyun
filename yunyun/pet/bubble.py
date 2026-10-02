"""气泡：显示在芸芸身边，文字自动换行，几秒后自动消失。

- Bubble：单个气泡窗口，只负责"画自己"和"滑到指定位置"。
- BubbleStack：管理同时显示的多个气泡，决定整组放在芸芸哪一边，
  再把它们一个挨一个排好，保证互不重叠、不出屏幕。

摆放规则：整组优先放在芸芸头顶；头顶放不下就去左/右侧；再不行去下方。
最新的气泡离芸芸最近，只有它带指向芸芸的小尾巴；旧的气泡被往外推。
换位置时不是瞬移，而是平滑地"滑"过去。

每个气泡都是独立的顶层窗口（而不是芸芸窗口的一部分），
这样它的大小可以随文字变化，不用把芸芸的窗口撑大。

气泡是芸芸的"从属窗口"（创建时把芸芸窗口传作 parent，并带 Tool 标志）。
Windows 会让从属窗口始终紧贴在主人正上方：芸芸被别的窗口挡住时气泡也一起被挡住，
芸芸被提到前面、设为置顶时，气泡也跟着一起。所以气泡自己不设"始终置顶"。
"""
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from yunyun.pet import winapi


class Bubble(QWidget):
    MAX_TEXT_WIDTH = 220   # 文字区域最大宽度，超过就自动换行
    PADDING = 12           # 文字到气泡边框的内边距
    TAIL = 10              # 小尾巴（指向芸芸的三角）的长度
    RADIUS = 12            # 圆角半径
    SMOOTHING = 0.3        # 每帧向目标位置靠近的比例（0~1）：越大越跟手，越小越"飘"
    FRAME_MS = 16          # 动画每帧间隔，约 60 帧/秒

    expired = Signal(object)  # 显示时间到了；参数是气泡自己，方便 BubbleStack 知道是哪个

    def __init__(self, text, owner):
        # owner：芸芸的窗口。有 parent 又带 Tool 标志的窗口，在 Windows 上就是 parent 的从属窗口
        super().__init__(owner)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # 弹出时不抢走键盘焦点
        self.setFont(QFont("Microsoft YaHei", 10))

        self._text = text
        # 先量一下：在最大宽度内自动换行时，这段文字需要多大的区域
        self._text_rect = self.fontMetrics().boundingRect(
            QRect(0, 0, self.MAX_TEXT_WIDTH, 10000),
            Qt.TextFlag.TextWordWrap,
            text,
        )
        # 窗口四周都留出尾巴的空间，这样尾巴朝哪个方向都画得下，窗口大小也不用变
        t = self.TAIL
        self.resize(
            self._text_rect.width() + 2 * self.PADDING + 2 * t,
            self._text_rect.height() + 2 * self.PADDING + 2 * t,
        )

        self._anchor = QRect()     # 芸芸身体在屏幕上的区域（画尾巴时要知道往哪指）
        self._show_tail = False
        self._pos = QPointF()      # 窗口当前位置（浮点数，动画才能走得细腻）
        self._target = QPointF()   # 窗口想去的位置

        # 单次定时器：时间到了就隐藏气泡
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_timeout)

        # 动画定时器：每帧让气泡向目标位置挪一点
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(self.FRAME_MS)
        self._anim_timer.timeout.connect(self._step)

    @property
    def text(self):
        return self._text

    def body_size(self):
        """气泡主体（不含四周尾巴留白）的大小。"""
        t = self.TAIL
        return QSize(self.width() - 2 * t, self.height() - 2 * t)

    def start_countdown(self, duration_ms):
        """（重新）开始计时，duration_ms 毫秒后消失。"""
        self._hide_timer.start(duration_ms)

    def place(self, body_pos, anchor, show_tail, animate):
        """把气泡主体的左上角放到 body_pos（屏幕坐标）。

        animate=True 时平滑滑过去，False 时直接瞬移（新气泡第一次出现时用）。
        """
        self._anchor = QRect(anchor)
        self._show_tail = show_tail
        t = self.TAIL
        self._target = QPointF(body_pos.x() - t, body_pos.y() - t)  # 主体左上角 → 窗口左上角
        if animate:
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._jump_to_target()
        self.update()

    def _on_timeout(self):
        self.hide()
        self.expired.emit(self)

    def hideEvent(self, event):
        self._anim_timer.stop()  # 看不见时就别再跑动画了，省 CPU
        super().hideEvent(event)

    # ---------- 动画 ----------

    def _jump_to_target(self):
        self._anim_timer.stop()
        self._pos = QPointF(self._target)
        self.move(self._pos.toPoint())
        self.update()

    def _step(self):
        """动画的一帧：每次走剩余距离的一部分（指数平滑），所以先快后慢，停得很柔和。"""
        delta = self._target - self._pos
        if abs(delta.x()) < 0.5 and abs(delta.y()) < 0.5:
            self._jump_to_target()       # 已经够近了：对齐到目标并停止动画
            return
        self._pos += delta * self.SMOOTHING
        self.move(self._pos.toPoint())
        self.update()                    # 位置变了，尾巴的朝向可能也要变，请求重画

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 气泡主体：圆角矩形（偏移 0.5 像素让 1px 描边画在像素中间，更清晰）
        t = self.TAIL
        body = QRectF(t + 0.5, t + 0.5, self.width() - 2 * t - 1, self.height() - 2 * t - 1)
        path = QPainterPath()
        path.addRoundedRect(body, self.RADIUS, self.RADIUS)

        tail = self._tail_path(body)
        if tail is not None:
            path = path.united(tail)  # 合并成一个形状，描边才不会在接缝处断开

        painter.setPen(QPen(QColor("#B0B0B0"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(path)

        painter.setPen(QColor("#333333"))
        text_area = QRectF(
            body.left() + self.PADDING, body.top() + self.PADDING,
            self._text_rect.width(), self._text_rect.height(),
        )
        painter.drawText(text_area, Qt.TextFlag.TextWordWrap, self._text)

    def _tail_path(self, body):
        """根据气泡此刻相对芸芸的方位，画一个指向芸芸的小三角；正在滑动且有重叠时不画。"""
        if not self._show_tail:
            return None

        # 把芸芸的屏幕坐标换算成气泡窗口里的坐标
        a = QRectF(self._anchor).translated(-self.x(), -self.y())
        c = a.center()
        t = self.TAIL
        inset = self.RADIUS + t  # 尾巴不能长在圆角上
        o = 1                    # 三角底边往主体里多伸 1 像素，合并后不留缝

        def along(value, lo, hi):
            """把尾巴位置限制在 [lo, hi]；边太短放不下时就放正中间。"""
            return (lo + hi) / 2 if lo > hi else max(lo, min(value, hi))

        if body.bottom() <= a.top():       # 气泡在芸芸上方 → 尾巴朝下
            x = along(c.x(), body.left() + inset, body.right() - inset)
            pts = [(x - t, body.bottom() - o), (x, body.bottom() + t), (x + t, body.bottom() - o)]
        elif body.top() >= a.bottom():     # 在下方 → 尾巴朝上
            x = along(c.x(), body.left() + inset, body.right() - inset)
            pts = [(x - t, body.top() + o), (x, body.top() - t), (x + t, body.top() + o)]
        elif body.right() <= a.left():     # 在左侧 → 尾巴朝右
            y = along(c.y(), body.top() + inset, body.bottom() - inset)
            pts = [(body.right() - o, y - t), (body.right() + t, y), (body.right() - o, y + t)]
        elif body.left() >= a.right():     # 在右侧 → 尾巴朝左
            y = along(c.y(), body.top() + inset, body.bottom() - inset)
            pts = [(body.left() + o, y - t), (body.left() - t, y), (body.left() + o, y + t)]
        else:
            return None

        tail = QPainterPath()
        tail.moveTo(*pts[0])
        for p in pts[1:]:
            tail.lineTo(*p)
        tail.closeSubpath()
        return tail


class BubbleStack(QObject):
    GAP = 4                # 尾巴尖到芸芸身体的距离
    SPACING = 6            # 气泡之间的间距
    MAX_BUBBLES = 3        # 最多同时显示几个，再多就把最旧的挤掉
    HYSTERESIS = 24        # 换方向的"迟滞"距离：要多出这么多空间才换回去，防止在临界处来回抖

    def __init__(self, owner):
        """owner：芸芸的窗口，气泡都作为它的从属窗口创建。"""
        super().__init__(owner)
        self._owner = owner
        self._bubbles = []         # 当前显示的气泡，从旧到新
        self._anchor = QRect()     # 芸芸身体在屏幕上的区域
        self._placement = None     # 整组的方向："above" / "below" / "left" / "right"

    def add(self, text, anchor, duration_ms=4000):
        """新增一个气泡。"""
        self._anchor = QRect(anchor)

        # 同样的话不重复说：已经在显示了，就只重新计时
        for bubble in self._bubbles:
            if bubble.text == text:
                bubble.start_countdown(duration_ms)
                return

        if not self._bubbles:
            self._placement = None  # 全新的一组：重新挑方向

        bubble = Bubble(text, self._owner)
        bubble.expired.connect(self._remove)
        self._bubbles.append(bubble)
        if len(self._bubbles) > self.MAX_BUBBLES:
            self._discard(self._bubbles[0])

        self._layout(new=bubble)
        # 显示从属窗口会把芸芸（主人）也拉到最前面，用 keep_z_order 保持芸芸原来的层级。也不要 raise_()。
        with winapi.keep_z_order(int(self._owner.winId())):
            bubble.show()
        bubble.start_countdown(duration_ms)

    def follow(self, anchor):
        """芸芸移动了：整组重新排位，平滑地跟过去。"""
        self._anchor = QRect(anchor)
        self._layout()

    def _remove(self, bubble):
        self._discard(bubble)
        self._layout()  # 剩下的气泡滑过来补位

    def _discard(self, bubble):
        if bubble in self._bubbles:
            self._bubbles.remove(bubble)
        bubble.hide()
        bubble.deleteLater()  # 让 Qt 在合适的时候销毁这个窗口，释放内存

    # ---------- 排版 ----------

    def _layout(self, new=None):
        """把所有气泡当作一整块，先给整块找位置，再在块里从上到下排好每个气泡。"""
        if not self._bubbles:
            return

        sizes = [b.body_size() for b in self._bubbles]
        group_w = max(s.width() for s in sizes)
        group_h = sum(s.height() for s in sizes) + self.SPACING * (len(sizes) - 1)
        group_x, group_y = self._place_group(group_w, group_h)

        # 从上到下的顺序：在芸芸下方时，最新的放最上面（离芸芸最近）；其余情况最新的放最下面
        newest = self._bubbles[-1]
        top_to_bottom = self._bubbles[::-1] if self._placement == "below" else self._bubbles

        y = group_y
        for bubble in top_to_bottom:
            size = bubble.body_size()
            if self._placement == "right":      # 在右侧：左对齐（靠近芸芸那边）
                x = group_x
            elif self._placement == "left":     # 在左侧：右对齐
                x = group_x + group_w - size.width()
            else:                               # 上方/下方：居中
                x = group_x + (group_w - size.width()) / 2
            bubble.place(
                QPointF(x, y), self._anchor,
                show_tail=bubble is newest,     # 只有最新的气泡带尾巴
                animate=bubble is not new,      # 新气泡直接出现，旧气泡滑过去让位
            )
            y += size.height() + self.SPACING

    def _place_group(self, bw, bh):
        """给一块 bw × bh 的区域找位置，返回它左上角的屏幕坐标 (x, y)。"""
        a = self._anchor
        screen = QGuiApplication.screenAt(a.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()  # 屏幕可用区域，不含任务栏

        # 注意：QRect.right() 等于 left + width - 1（历史原因），所以这里自己算右/下边界
        s_left, s_top = area.x(), area.y()
        s_right, s_bottom = area.x() + area.width(), area.y() + area.height()
        a_left, a_top = a.x(), a.y()
        a_right, a_bottom = a.x() + a.width(), a.y() + a.height()
        cx, cy = a.center().x(), a.center().y()

        d = self.GAP + Bubble.TAIL  # 主体到芸芸的距离（中间是尾巴）

        # 四个候选：左上角坐标 (x, y)，以及该方向剩余的空间（>= 0 表示放得下）
        candidates = {
            "above": (cx - bw / 2, a_top - d - bh, a_top - d - bh - s_top),
            "below": (cx - bw / 2, a_bottom + d, s_bottom - (a_bottom + d + bh)),
            "right": (a_right + d, cy - bh / 2, s_right - (a_right + d + bw)),
            "left":  (a_left - d - bw, cy - bh / 2, a_left - d - bw - s_left),
        }
        room = {name: c[2] for name, c in candidates.items()}

        # 左右两侧：如果已经在某一侧，就优先留在那一侧（避免在屏幕中线附近左右乱跳）；
        # 否则选空间更大的那一侧
        sides = ["right", "left"]
        if self._placement in sides:
            sides.sort(key=lambda name: name != self._placement)
        else:
            sides.sort(key=lambda name: room[name], reverse=True)
        order = ["above", *sides, "below"]

        # 按优先级挑第一个放得下的。不是当前方向的，需要多出 HYSTERESIS 的空间才换过去
        chosen = max(room, key=room.get)  # 兜底：都放不下时，选空间最大的
        for name in order:
            need = 0 if self._placement in (None, name) else self.HYSTERESIS
            if room[name] >= need:
                chosen = name
                break
        self._placement = chosen

        # 最后再夹一次，保证整组绝不出屏
        x, y, _ = candidates[chosen]
        x = max(s_left, min(x, s_right - bw))
        y = max(s_top, min(y, s_bottom - bh))
        return x, y
