"""助手模式的输入框：芸芸脚下一个圆角输入条，四周一圈淡蓝色渐变光晕。

         ( 芸芸 )
 ╭───────────────────────────────────╮
 │ [▭ project] 问芸芸点什么……     (↑) │   ← 光晕：平时静止；芸芸思考时沿边框缓缓流动
 ╰───────────────────────────────────╯

- 左边是工作文件夹按钮：点一下弹出菜单（最近用过的几个 / 选择文件夹… / 不用文件夹），选了发 workspace_chosen。
- 出现：从一个小圆点横向"长"成输入条（带一点弹性），文字和按钮随后淡入；收起是反过来，越缩越快。
- 动画全部自己画：窗口一开始就是最终大小（四周留出光晕的透明边），动画里只改画的形状，不改窗口大小，所以不抖。
  只在动画进行 / 思考时刷新画面，平时不占 CPU。
- Enter 发送，Shift+Enter 换行，Ctrl+N 新对话，最多长到 MAX_LINES 行；回答时按钮变成"停止"，可以先打字但发不出去。
- 粘贴 / 拖进来（阶段 5.5）：图片（剪贴板里的图、图片文件）在输入条最上面排成一排缩略图（× 去掉，最多 MAX_IMAGES 张），
  发送时一起发（只有图片没有字也能发）；别的文件插入路径。大脑看不了图片时不收（image_refused，总管说一声）。
- Esc 或者点到别的程序 → 收起（collapsed 信号）；点芸芸自己的窗口不收起。打了字 / 带着图片时点到别的程序不收起
  （只有 Esc 收）；空的时候在别的程序里按着左键（正要拖文件过来）先不收，松开再看（2026-10-08 用户要求）。
- 是芸芸的从属窗口（parent = 芸芸）：芸芸被挡住时它也被挡住。
"""
import math
import os

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QActionGroup, QBrush, QColor, QConicalGradient, QFont, QFontMetrics, QImage, QLinearGradient, QPainter,
    QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QFileDialog, QFrame, QGraphicsOpacityEffect, QMenu, QTextEdit, QWidget,
)

from yunyun import i18n
from yunyun.pet import screen_capture, winapi
from yunyun.pet.bubble import place_block

# 光晕的颜色：天蓝 → 淡紫 → 青（思考时三种颜色绕着边框转）
GLOW_COLORS = (QColor("#5AB8FF"), QColor("#8E9BFF"), QColor("#63E1F5"))


def _mix(color, alpha):
    c = QColor(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def folder_name(path):
    """文件夹显示的名字：最后一级（F:\\ 这种盘符根目录就显示整个）。"""
    return os.path.basename(os.path.normpath(path)) or path


def ask_folder(parent, start):
    """弹出系统的"选择文件夹"框；取消就返回空字符串。测试里换成假的。"""
    return QFileDialog.getExistingDirectory(parent, i18n.tr("assistant_folder_dialog"), start)


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _image_from(data):
    """剪贴板 / 拖进来的图片数据（QImage 或 QPixmap）→ QImage；不是图片就是空的 QImage。"""
    if isinstance(data, QImage):
        return data
    if isinstance(data, QPixmap):
        return data.toImage()
    return QImage()


def _quoted(path):
    return f'"{path}"' if " " in path else path


class _Edit(QTextEdit):
    """透明的多行输入：Enter 发送，Shift+Enter 换行，Esc 收起。
    粘贴 / 拖进来（阶段 5.5）：图片（剪贴板里的图、图片文件）发 image_pasted，变成上面的缩略图；
    别的文件插入它的路径；文字照常。"""

    submit = Signal()
    escape = Signal()
    new_chat = Signal()
    image_pasted = Signal(QImage)
    dropped = Signal()   # 有东西拖进来放下了（输入框要把键盘焦点拿回来：拖之前焦点在资源管理器）

    def dropEvent(self, event):
        super().dropEvent(event)
        self.dropped.emit()

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        files = [url.toLocalFile() for url in source.urls() if url.isLocalFile()] if source.hasUrls() else []
        if files:
            others = []
            for path in files:
                # 读图用 QImage（CLAUDE.md：QPixmap(路径) 会多缓存一份）
                image = QImage(path) if os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS else QImage()
                if image.isNull():
                    others.append(os.path.normpath(path))
                else:
                    self.image_pasted.emit(image)
            if others:
                self.insertPlainText(" ".join(_quoted(p) for p in others))
            return
        image = _image_from(source.imageData()) if source.hasImage() else QImage()
        if not image.isNull():
            self.image_pasted.emit(image)
            return
        super().insertFromMimeData(source)

    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet("QTextEdit { background: transparent; color: #1F2937; }")
        self.viewport().setAutoFillBackground(False)
        self.document().setDocumentMargin(0)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submit.emit()
            return
        if key == Qt.Key.Key_Escape:
            self.escape.emit()
            return
        if key == Qt.Key.Key_N and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.new_chat.emit()
            return
        super().keyPressEvent(event)


class _SendButton(QAbstractButton):
    """圆形按钮：平时是"发送"（向上的箭头），回答时是"停止"（小方块）。"""

    SIZE = 30

    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_mode = False

    def sizeHint(self):
        return QSize(self.SIZE, self.SIZE)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.isDown():
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)   # 按下时微微缩小
        gradient = QLinearGradient(r.topLeft(), r.bottomRight())
        light = 18 if self.underMouse() else 0
        gradient.setColorAt(0, QColor("#4FA8FF").lighter(100 + light))
        gradient.setColorAt(1, QColor("#7C8CFF").lighter(100 + light))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(gradient)
        p.drawEllipse(r)

        c = r.center()
        p.setBrush(QColor("white"))
        if self.stop_mode:
            p.drawRoundedRect(QRectF(c.x() - 5, c.y() - 5, 10, 10), 2.5, 2.5)
        else:
            pen = QPen(QColor("white"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(QPointF(c.x(), c.y() + 6), QPointF(c.x(), c.y() - 6))
            path = QPainterPath(QPointF(c.x() - 5, c.y() - 1))
            path.lineTo(c.x(), c.y() - 6)
            path.lineTo(c.x() + 5, c.y() - 1)
            p.drawPath(path)


class _FolderButton(QAbstractButton):
    """输入条左边的工作文件夹按钮：文件夹图标 + 名字（没选时只有一个淡色图标）。"""

    HEIGHT = 30
    MAX_TEXT = 110     # 名字最多占多宽，太长中间用 … 省略
    ICON = 16

    def __init__(self, parent):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(QFont("Microsoft YaHei", 9))
        self.path = ""
        self._label = ""
        self.set_path("")

    def set_path(self, path):
        self.path = path
        self._label = QFontMetrics(self.font()).elidedText(
            folder_name(path), Qt.TextElideMode.ElideMiddle, self.MAX_TEXT) if path else ""
        width = self.ICON + 14 + (QFontMetrics(self.font()).horizontalAdvance(self._label) + 6 if path else 0)
        self.setFixedSize(width, self.HEIGHT)
        self.update()

    def sizeHint(self):
        return self.size()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.underMouse() or self.isDown():
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#E3EAF7") if self.isDown() else QColor("#EEF2FA"))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        color = QColor("#4F7BD9") if self.path else QColor("#9AA5B8")
        x, cy = 7.0, r.center().y()
        icon = QPainterPath()   # 文件夹：左上角一个小"耳朵"，下面是圆角的夹子
        icon.moveTo(x, cy - 5)
        icon.lineTo(x + 5, cy - 5)
        icon.lineTo(x + 7, cy - 3)
        icon.lineTo(x + self.ICON, cy - 3)
        icon.lineTo(x + self.ICON, cy + 5.5)
        icon.lineTo(x, cy + 5.5)
        icon.closeSubpath()
        p.setPen(QPen(color, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(_mix(color, 40 if self.path else 0))
        p.drawPath(icon)
        if self._label:
            p.setPen(QColor("#4B5563"))
            p.drawText(QRectF(x + self.ICON + 5, 0, self.width(), self.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)


class _Thumbs(QWidget):
    """输入条最上面一排小缩略图（阶段 5.5：粘贴 / 拖进来的图片）；点右上角的 × 去掉那一张（发 removed(第几张)）。"""

    removed = Signal(int)
    SIZE = 44        # 一张缩略图多大
    GAP = 8
    CLOSE = 16       # × 的圆多大

    def __init__(self, parent):
        super().__init__(parent)
        self._images = []   # 缩略图（QImage，已经裁成正方形）
        self._hover = -1
        self.setMouseTracking(True)
        self.setFixedHeight(self.SIZE + self.CLOSE // 2)

    def set_images(self, images):
        self._images = list(images)
        self.setFixedWidth(max(1, len(self._images) * (self.SIZE + self.GAP)))
        self.update()

    def _rect(self, i):
        return QRectF(i * (self.SIZE + self.GAP), self.CLOSE / 2, self.SIZE, self.SIZE)

    def _close_rect(self, i):
        r = self._rect(i)
        return QRectF(r.right() - self.CLOSE + 4, r.top() - self.CLOSE / 2 + 2, self.CLOSE, self.CLOSE)

    def mouseMoveEvent(self, event):
        pos = event.position()
        hover = next((i for i in range(len(self._images)) if self._rect(i).adjusted(0, -8, 4, 0).contains(pos)), -1)
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.CursorShape.PointingHandCursor if hover >= 0 else Qt.CursorShape.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        for i in range(len(self._images)):
            if self._close_rect(i).adjusted(-3, -3, 3, 3).contains(event.position()):
                self.removed.emit(i)
                return

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for i, image in enumerate(self._images):
            r = self._rect(i)
            path = QPainterPath()
            path.addRoundedRect(r, 7, 7)
            p.save()
            p.setClipPath(path)
            p.drawImage(r, image)
            p.restore()
            p.setPen(QPen(QColor("#DDE3EE"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)   # 只描边（不然会用上一张 × 的深色把这张盖住）
            p.drawPath(path)
            c = self._close_rect(i)   # ×：一直显示（鼠标移上去更深），用户一眼就知道能去掉
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(55, 65, 81, 230 if i == self._hover else 170))
            p.drawEllipse(c)
            p.setPen(QPen(QColor("white"), 1.6))
            m = 5
            p.drawLine(QPointF(c.left() + m, c.top() + m), QPointF(c.right() - m, c.bottom() - m))
            p.drawLine(QPointF(c.right() - m, c.top() + m), QPointF(c.left() + m, c.bottom() - m))


class AssistantInput(QWidget):
    submitted = Signal(str)       # 按了 Enter / 发送（文字由接收方决定要不要清空：发出去了才清）
    stop_requested = Signal()     # 回答时按了"停止"
    collapse_started = Signal()   # 开始收起（卡片堆跟着淡出）
    collapsed = Signal()          # 收起动画结束、窗口已经隐藏
    new_conversation_requested = Signal()   # Ctrl+N
    workspace_chosen = Signal(str)          # 在文件夹菜单里选了一个文件夹（"" = 不用文件夹）
    image_refused = Signal(str)             # 粘贴的图片没收下："unsupported"（大脑看不了图片）/ "too_many"

    WIDTH = 420        # 输入条宽度
    MAX_IMAGES = 5     # 一问最多带几张图片
    THUMB_PAD = 8      # 缩略图那一排离输入条上边多远
    BUTTON_POLL_MS = 200   # 焦点去了别的程序、左键还按着：隔多久再看一次松开了没有
    GLOW = 26          # 光晕向外延伸多远（窗口四周留出这么多透明边）
    DOT = 46           # 一行时输入条的高度，也是展开前"小圆点"的直径
    PAD_X = 8          # 文件夹按钮到左边框
    GAP_X = 6          # 文件夹按钮到文字
    MAX_LINES = 5
    GAP = 12           # 输入条到芸芸身体的距离
    OPEN_MS = 520
    CLOSE_MS = 260
    FLOW_MS = 2800     # 思考时光晕转一圈要多久
    GLOW_LAYERS = 10

    def __init__(self, owner):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont("Microsoft YaHei", 10))

        self._edit = _Edit(self)
        self._edit.setFont(self.font())
        self._edit.setPlaceholderText(i18n.tr("assistant_input_placeholder"))
        self._edit.submit.connect(self._on_submit)
        self._edit.escape.connect(self.collapse)
        self._edit.new_chat.connect(self.new_conversation_requested)
        self._edit.textChanged.connect(self._fit)
        self._button = _SendButton(self)
        self._button.clicked.connect(self._on_button)
        self._button.setToolTip(i18n.tr("assistant_send"))
        self._folder = _FolderButton(self)
        self._folder.clicked.connect(self._show_folder_menu)
        self.recent_folders = list   # 最近用过的文件夹（函数，由总管设置；默认没有）
        self._picking = False          # 文件夹菜单 / 选择框开着：焦点暂时不在输入框，不要收起
        # 粘贴 / 拖进来的图片（阶段 5.5）：[(缩略图, {"data": base64, "mime": …})]
        self._attachments = []
        self.images_allowed = True     # 现在的大脑能不能看图片（总管按服务商设置；不能就不收，发 image_refused）
        self._thumbs = _Thumbs(self)
        self._thumbs.removed.connect(self._remove_image)
        self._edit.image_pasted.connect(self.add_image)
        self._edit.dropped.connect(self.focus)
        self.setAcceptDrops(True)      # 拖到输入条别的地方（不只是文字那一块）也收

        self._t = 0.0                  # 展开进度：0 = 小圆点（看不见），1 = 完全展开
        self._closing = False
        self._busy = False
        self._thinking = False
        self._phase = 0.0              # 思考时光晕转到的角度
        self._pill_h = self.DOT
        self._anchor = QRect()
        self._placement = None
        self._open_curve = QEasingCurve(QEasingCurve.Type.OutBack)
        self._open_curve.setOvershoot(1.05)          # 弹性：稍微超出一点再回来（太大会显得晃）
        self._close_curve = QEasingCurve(QEasingCurve.Type.OutCubic)

        self._anim = QVariantAnimation(self)         # 线性的 0→1，曲线在 _set_t 里自己套（宽度和透明度用不同的曲线）
        self._anim.valueChanged.connect(self._set_t)
        self._anim.finished.connect(self._on_anim_finished)
        self._flow = QVariantAnimation(self, startValue=0.0, endValue=360.0, duration=self.FLOW_MS, loopCount=-1)
        self._flow.valueChanged.connect(self._on_flow)

        self._effects = []
        self._update_folder_tip()
        self._fit()
        self._apply_t()

    # ---------- 对外 ----------

    @property
    def is_open(self):
        return self.isVisible() and not self._closing

    @property
    def text(self):
        return self._edit.toPlainText()

    def set_text(self, text):
        self._edit.setPlainText(text)

    def clear_text(self):
        self._edit.clear()

    @property
    def images(self):
        """要一起发的图片：[{"data": base64, "mime": …}]。"""
        return [data for _, data in self._attachments]

    def add_image(self, image):
        """收下一张粘贴 / 拖进来的图片：缩小、编码成 JPEG，显示缩略图。收不下就发 image_refused、返回 False。"""
        if not self.images_allowed:
            self.image_refused.emit("unsupported")
            return False
        if len(self._attachments) >= self.MAX_IMAGES:
            self.image_refused.emit("too_many")
            return False
        data, _, _ = screen_capture.encode(image)
        side = min(image.width(), image.height())   # 缩略图：从中间裁成正方形
        thumb = image.copy((image.width() - side) // 2, (image.height() - side) // 2, side, side).scaled(
            _Thumbs.SIZE * 2, _Thumbs.SIZE * 2, Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation)   # 两倍大：高分屏上也清楚
        self._attachments.append((thumb, {"data": data, "mime": "image/jpeg"}))
        self._update_thumbs()
        return True

    def clear_images(self):
        self._attachments = []
        self._update_thumbs()

    def _remove_image(self, index):
        if 0 <= index < len(self._attachments):
            del self._attachments[index]
            self._update_thumbs()
            self._edit.setFocus()

    def _update_thumbs(self):
        self._thumbs.set_images([thumb for thumb, _ in self._attachments])
        self._thumbs.setVisible(bool(self._attachments) and self._content_opacity() > 0)
        self._fit()

    def open(self, anchor):
        """在芸芸（anchor：身体的屏幕区域）脚下展开，并拿到键盘焦点。已经开着就只拿焦点。"""
        self._closing = False
        self.follow(anchor)
        if not self.isVisible():
            self._t = 0.0
            self._apply_t()
            self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus()
        self._animate_to(1.0, self.OPEN_MS * (1.0 - self._t))

    def focus(self):
        """把键盘焦点拿回来（关掉阅读框之后）。"""
        if self.is_open:
            self.raise_()
            self.activateWindow()
            self._edit.setFocus()

    def collapse(self):
        """收起（动画结束后隐藏、发 collapsed）。输入框里打了一半的字留着，下次展开还在。"""
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        self.collapse_started.emit()
        self._animate_to(0.0, self.CLOSE_MS * self._t)

    def close_now(self):
        """立刻隐藏，不要动画（退出助手模式时用）。"""
        self._anim.stop()
        self._closing = False
        self._t = 0.0
        self.hide()

    def follow(self, anchor):
        """芸芸移动了：跟过去（优先放在脚下，脚下放不下就放头顶，再不行放左右）。"""
        self._anchor = QRect(anchor)
        x, y, self._placement = place_block(
            self._anchor, self.WIDTH, self._pill_h, gap=self.GAP, current=self._placement, prefer="below")
        self.move(int(x) - self.GLOW, int(y) - self.GLOW)

    def set_busy(self, busy):
        """芸芸在回答：按钮变成"停止"，Enter 发不出去。"""
        self._busy = busy
        self._button.stop_mode = busy
        self._button.setToolTip(i18n.tr("assistant_stop" if busy else "assistant_send"))
        self._button.update()

    def set_thinking(self, thinking):
        """思考时光晕绕着边框流动。"""
        self._thinking = thinking
        if thinking and self.isVisible():
            self._flow.start()
        elif not thinking:
            self._flow.stop()
        self.update()

    @property
    def workspace(self):
        return self._folder.path

    def set_workspace(self, path):
        """显示当前的工作文件夹（"" = 没选）。"""
        self._folder.set_path(path)
        self._update_folder_tip()
        self._fit()

    def retranslate(self):
        self._edit.setPlaceholderText(i18n.tr("assistant_input_placeholder"))
        self._update_folder_tip()
        self.set_busy(self._busy)

    # ---------- 工作文件夹 ----------

    def _update_folder_tip(self):
        path = self._folder.path
        self._folder.setToolTip(i18n.tr("assistant_folder_tip", path) if path else i18n.tr("assistant_folder_none_tip"))

    def folder_menu(self):
        """文件夹菜单：最近用过的几个（当前的打勾）、选择文件夹…、不用文件夹。选了就发 workspace_chosen。"""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        group = QActionGroup(menu)
        current = os.path.normcase(self._folder.path)
        for path in self.recent_folders():
            parent = os.path.dirname(os.path.normpath(path))
            action = menu.addAction(f"{folder_name(path)}    {parent}" if parent != path else path)
            action.setToolTip(path)
            action.setCheckable(True)
            action.setChecked(os.path.normcase(path) == current)
            action.setActionGroup(group)
            action.triggered.connect(lambda checked=False, p=path: self.workspace_chosen.emit(p))
        if menu.actions():
            menu.addSeparator()
        menu.addAction(i18n.tr("assistant_folder_choose")).triggered.connect(self._pick_folder)
        none = menu.addAction(i18n.tr("assistant_folder_none"))
        none.setCheckable(True)
        none.setChecked(not current)
        none.setActionGroup(group)
        none.triggered.connect(lambda: self.workspace_chosen.emit(""))
        return menu

    def _show_folder_menu(self):
        menu = self.folder_menu()
        self._picking = True
        try:
            menu.exec(self._folder.mapToGlobal(self._folder.rect().bottomLeft()))
        finally:
            self._picking = False
            menu.deleteLater()
        self.focus()

    def _pick_folder(self):
        self._picking = True
        try:
            path = ask_folder(self, self._folder.path or os.path.expanduser("~"))
        finally:
            self._picking = False
        if path:
            self.workspace_chosen.emit(os.path.normpath(path))

    # ---------- 事件 ----------

    def _on_submit(self):
        if not self._busy and (self.text.strip() or self._attachments):   # 只有图片没有字也能发
            self.submitted.emit(self.text)

    def dragEnterEvent(self, event):
        if self._edit.canInsertFromMimeData(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        self._edit.insertFromMimeData(event.mimeData())
        event.acceptProposedAction()
        self.focus()

    def _on_button(self):
        if self._busy:
            self.stop_requested.emit()
        else:
            self._on_submit()
        self._edit.setFocus()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow() and self.is_open:
            QTimer.singleShot(150, self._check_focus_lost)   # 等焦点落定：可能只是换到了芸芸自己的另一个窗口
        super().changeEvent(event)

    def _check_focus_lost(self):
        """焦点去了别的程序：一般就收起。但这几种情况留着（2026-10-08 用户要求：拖文件进来时输入框别收起）：
        - 打了字、带着图片：一直留着，按 Esc 才收；
        - 空的、但你在别的程序里按着左键（可能正要拖文件过来）：等你松开再看——拖进来了就有了图片 / 路径，留着。"""
        if not self.is_open or self._picking or QApplication.activeWindow() is not None:
            return
        if self.text.strip() or self._attachments:
            return
        if winapi.left_button_down():
            QTimer.singleShot(self.BUTTON_POLL_MS, self._check_focus_lost)
            return
        self.collapse()

    def showEvent(self, event):
        super().showEvent(event)
        if self._thinking:
            self._flow.start()

    def hideEvent(self, event):
        self._flow.stop()
        super().hideEvent(event)

    # ---------- 尺寸 ----------

    def _fit(self):
        """按文字行数定输入条高度（1～MAX_LINES 行），窗口跟着变高。"""
        line = self._edit.fontMetrics().lineSpacing()
        self._edit.document().setTextWidth(self._edit_width())
        text_h = max(line, min(int(self._edit.document().size().height()), line * self.MAX_LINES))
        pad_y = (self.DOT - line) // 2
        thumbs_h = self.THUMB_PAD + self._thumbs.height() if self._attachments else 0   # 有图片：最上面多一排缩略图
        pill_h = max(self.DOT, text_h + 2 * pad_y) + thumbs_h
        g = self.GLOW
        self._thumbs.move(g + self.PAD_X + 8, g + self.THUMB_PAD - _Thumbs.CLOSE // 2 + 4)
        self._edit.setGeometry(self._edit_left(), g + thumbs_h + pad_y, self._edit_width(), text_h)
        b = _SendButton.SIZE
        self._button.move(g + self.WIDTH - (self.DOT + b) // 2, g + pill_h - (self.DOT + b) // 2)
        # 文件夹按钮和"发送"一样贴着最后一行（多行时在左下角）
        self._folder.move(g + self.PAD_X, g + pill_h - (self.DOT + _FolderButton.HEIGHT) // 2)
        if pill_h != self._pill_h or self.size() != QSize(self.WIDTH + 2 * g, pill_h + 2 * g):
            self._pill_h = pill_h
            self.resize(self.WIDTH + 2 * g, pill_h + 2 * g)
            if not self._anchor.isNull():
                self.follow(self._anchor)
        self._edit.ensureCursorVisible()

    def _edit_left(self):
        return self.GLOW + self.PAD_X + self._folder.width() + self.GAP_X

    def _edit_width(self):
        return self.WIDTH - (self._edit_left() - self.GLOW) - (self.DOT + _SendButton.SIZE) // 2 - 8

    # ---------- 动画 ----------

    def _animate_to(self, target, duration):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self._anim.setDuration(max(1, int(duration)))
        self._anim.start()

    def _set_t(self, value):
        self._t = float(value)
        self._apply_t()

    def _on_anim_finished(self):
        if self._closing:
            self._closing = False
            self.hide()
            self.collapsed.emit()

    def _on_flow(self, value):
        self._phase = float(value)
        self.update()

    def _content_opacity(self):
        """文字和按钮：输入条长到一半多才开始淡入（收起时先淡出）。"""
        return max(0.0, min(1.0, (self._t - 0.5) / 0.4))

    def _apply_t(self):
        opacity = self._content_opacity()
        visible = opacity > 0
        for widget in (self._edit, self._button, self._folder, self._thumbs):
            widget.setVisible(visible and (widget is not self._thumbs or bool(self._attachments)))
            if 0 < opacity < 1:
                effect = widget.graphicsEffect()
                if not isinstance(effect, QGraphicsOpacityEffect):
                    effect = QGraphicsOpacityEffect(widget)
                    widget.setGraphicsEffect(effect)
                effect.setOpacity(opacity)
            elif widget.graphicsEffect() is not None:
                widget.setGraphicsEffect(None)   # 动画结束就拿掉：平时不多一层绘制
        self.update()

    def _pill_rect(self):
        """按展开进度算输入条此刻的形状：宽度从小圆点长到 WIDTH，中心不动。"""
        curve = self._close_curve if self._closing else self._open_curve
        grow = curve.valueForProgress(self._t)
        width = self.DOT + (self.WIDTH - self.DOT) * grow
        width = max(self.DOT * 0.6, min(width, self.WIDTH + self.GLOW))   # 弹性超出时别画出窗口
        cx = self.GLOW + self.WIDTH / 2
        return QRectF(cx - width / 2, self.GLOW, width, self._pill_h)

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        alpha = min(1.0, self._t * 3)   # 整体先淡入（前三分之一的时间里）
        if alpha <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(alpha)
        pill = self._pill_rect()
        radius = min(pill.height() / 2, self.DOT / 2)
        self._paint_glow(p, pill, radius)

        body = QPainterPath()
        body.addRoundedRect(pill, radius, radius)
        p.fillPath(body, QColor(255, 255, 255, 248))
        p.setPen(QPen(self._glow_brush(pill, 150 if self._thinking else 90), 1.2))
        p.drawPath(body)

    def _paint_glow(self, p, pill, radius):
        """柔和的光晕：一圈圈往外扩、越外越淡的圆角矩形叠在一起（Qt 没有现成的模糊，叠几层效果差不多）。"""
        strength = 1.0
        if self._thinking:
            strength = 1.25 + 0.2 * math.sin(math.radians(self._phase * 2))   # 思考时更亮，还轻轻地一明一暗
        n = self.GLOW_LAYERS
        for i in range(n, 0, -1):
            spread = (self.GLOW - 4) * i / n
            alpha = 24 * strength * (1 - i / (n + 1)) ** 2
            rect = pill.adjusted(-spread, -spread, spread, spread)
            path = QPainterPath()
            path.addRoundedRect(rect, radius + spread, radius + spread)
            p.fillPath(path, self._glow_brush(rect, alpha))

    def _glow_brush(self, rect, alpha):
        if self._thinking:
            gradient = QConicalGradient(rect.center(), -self._phase)
            a, b, c = GLOW_COLORS
            for pos, color in ((0, a), (0.33, b), (0.66, c), (1, a)):
                gradient.setColorAt(pos, _mix(color, alpha))
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0, _mix(GLOW_COLORS[0], alpha))
            gradient.setColorAt(1, _mix(GLOW_COLORS[1], alpha))
        return QBrush(gradient)
