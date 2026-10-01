"""气泡组件：显示在芸芸身边，文字自动换行，几秒后自动消失。

气泡是一个独立的顶层窗口（而不是芸芸窗口的一部分），
这样它的大小可以随文字变化，不用把芸芸的窗口撑大。

摆放规则：优先放在芸芸头顶；头顶放不下就去左/右侧；再不行去下方。
换位置时不是瞬移，而是平滑地"滑"过去；无论如何气泡主体都不会出屏幕。
"""
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class Bubble(QWidget):
    MAX_TEXT_WIDTH = 220   # 文字区域最大宽度，超过就自动换行
    PADDING = 12           # 文字到气泡边框的内边距
    TAIL = 10              # 小尾巴（指向芸芸的三角）的长度
    RADIUS = 12            # 圆角半径
    GAP = 4                # 尾巴尖到芸芸身体的距离
    HYSTERESIS = 24        # 换方向的"迟滞"距离：要多出这么多空间才换回去，防止在临界处来回抖
    SMOOTHING = 0.3        # 每帧向目标位置靠近的比例（0~1）：越大越跟手，越小越"飘"
    FRAME_MS = 16          # 动画每帧间隔，约 60 帧/秒

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # 弹出时不抢走键盘焦点
        self.setFont(QFont("Microsoft YaHei", 10))

        self._text = ""
        self._text_rect = QRect()
        self._anchor = QRect()     # 芸芸身体在屏幕上的区域
        self._placement = None     # 当前方向："above" / "below" / "left" / "right"
        self._pos = QPointF()      # 气泡窗口当前位置（浮点数，动画才能走得细腻）
        self._target = QPointF()   # 气泡窗口想去的位置

        # 单次定时器：时间到了就隐藏气泡
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        # 动画定时器：每帧让气泡向目标位置挪一点
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(self.FRAME_MS)
        self._anim_timer.timeout.connect(self._step)

    # ---------- 对外接口 ----------

    def show_text(self, text, anchor, duration_ms=4000):
        """在 anchor（芸芸身体的屏幕区域，QRect）旁边显示 text，duration_ms 毫秒后消失。"""
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

        self._anchor = QRect(anchor)
        self._placement = None           # 新气泡：重新挑方向
        self._target = self._compute_target()
        self._jump_to_target()           # 第一次出现直接放到位，不用滑
        self.show()
        self.raise_()
        self._hide_timer.start(duration_ms)  # 每次显示都重新计时

    def follow(self, anchor):
        """芸芸移动了：重新计算目标位置，让气泡滑过去。"""
        self._anchor = QRect(anchor)
        self._target = self._compute_target()
        if not self._anim_timer.isActive():
            self._anim_timer.start()

    def hideEvent(self, event):
        self._anim_timer.stop()  # 看不见时就别再跑动画了，省 CPU
        super().hideEvent(event)

    # ---------- 摆放位置 ----------

    def _compute_target(self):
        """算出气泡窗口应该在的位置（左上角，QPointF）。"""
        a = self._anchor
        screen = QGuiApplication.screenAt(a.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()  # 屏幕可用区域，不含任务栏

        # 注意：QRect.right() 等于 left + width - 1（历史原因），所以这里自己算右/下边界
        s_left, s_top = area.x(), area.y()
        s_right, s_bottom = area.x() + area.width(), area.y() + area.height()
        a_left, a_top = a.x(), a.y()
        a_right, a_bottom = a.x() + a.width(), a.y() + a.height()
        cx, cy = a.center().x(), a.center().y()

        t = self.TAIL
        bw = self.width() - 2 * t   # 气泡主体（不含尾巴留白）的宽、高
        bh = self.height() - 2 * t
        d = self.GAP + t            # 主体到芸芸身体的距离（中间是尾巴）

        # 四个候选：主体左上角坐标 (x, y)，以及该方向剩余的空间（>= 0 表示放得下）
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

        # 最后再夹一次，保证气泡主体绝不出屏
        x, y, _ = candidates[chosen]
        x = max(s_left, min(x, s_right - bw))
        y = max(s_top, min(y, s_bottom - bh))
        return QPointF(x - t, y - t)  # 主体左上角 → 窗口左上角（减去四周的尾巴留白）

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
