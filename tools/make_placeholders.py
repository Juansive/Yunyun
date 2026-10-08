"""生成芸芸的占位图。

等画师的正式素材到了，直接用同名文件覆盖 assets/pet/ 里的图片即可，代码不用改。
素材要求见 assets/pet/README.md。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python tools\\make_placeholders.py                 # 只补上还没有的图（不覆盖已有的）
    .\\.venv\\Scripts\\python tools\\make_placeholders.py busy waiting    # 只生成这几种（文件名开头）
    .\\.venv\\Scripts\\python tools\\make_placeholders.py --force ...     # 覆盖已有的（小心：会冲掉画师的图）
"""
import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPolygonF

OUT = Path(__file__).resolve().parents[1] / "assets" / "pet"
S = 512  # 画布边长

BODY = QColor("#7EC8E3")
HAND = QColor("#5BB0D0")
INK = QColor("#2B2B2B")
BLUSH = QColor(255, 140, 160, 150)
KEYBOARD = QColor("#D9DEE3")


def new_canvas():
    img = QImage(S, S, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    return img, p


def pen(width, color=INK):
    pn = QPen(color, width)
    pn.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pn


def draw_body(p, dy=0):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BODY)
    p.drawEllipse(QRectF(96, 110 + dy, 320, 300))


def draw_hand(p, x, y):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(HAND)
    p.drawEllipse(QPointF(x, y), 30, 26)


def draw_open_eyes(p, dy=0):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    p.drawEllipse(QPointF(200, 235 + dy), 15, 18)
    p.drawEllipse(QPointF(312, 235 + dy), 15, 18)


def draw_closed_eyes(p):
    """闭眼：向下弯的弧线 ︶"""
    p.setPen(pen(8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(178, 222, 44, 26), 0, -180 * 16)
    p.drawArc(QRectF(290, 222, 44, 26), 0, -180 * 16)


def draw_happy_eyes(p, dy):
    """开心眯眼：向上弯的弧线 ^ ^"""
    p.setPen(pen(9))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(178, 222 + dy, 44, 34), 0, 180 * 16)
    p.drawArc(QRectF(290, 222 + dy, 44, 34), 0, 180 * 16)


def draw_small_mouth(p, dy=0):
    p.setPen(pen(6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(240, 262 + dy, 32, 20), 0, -180 * 16)


def draw_blush(p, dy=0):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BLUSH)
    p.drawEllipse(QPointF(160, 285 + dy), 24, 13)
    p.drawEllipse(QPointF(352, 285 + dy), 24, 13)


def draw_keyboard(p):
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(KEYBOARD)
    p.drawRoundedRect(QRectF(76, 382, 360, 96), 18, 18)
    p.setBrush(QColor("#FFFFFF"))
    for row in range(3):
        for col in range(10):
            p.drawRoundedRect(QRectF(96 + col * 33, 396 + row * 25, 26, 18), 4, 4)


def draw_tap_lines(p, x):
    """敲击时手旁边的小短线，表示"啪嗒"一下"""
    p.setPen(pen(5))
    for dx, dy1, dy2 in ((-44, -6, -26), (0, -40, -58), (44, -6, -26)):
        p.drawLine(QPointF(x + dx * 0.9, 372 + dy1), QPointF(x + dx * 1.3, 372 + dy2))


def draw_envelope(p, x, y):
    rect = QRectF(x, y, 120, 82)
    p.setPen(pen(6))
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(rect, 8, 8)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(x + 6, y + 8), QPointF(x + 60, y + 48), QPointF(x + 114, y + 8)]))


def draw_sparkle(p, x, y, r):
    p.setPen(pen(6, QColor("#F5B700")))
    p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
    p.drawLine(QPointF(x, y - r), QPointF(x, y + r))


def draw_z(p, x, y, size):
    """用线条画一个 Z（不依赖字体）"""
    p.setPen(pen(max(4, size // 7)))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolyline(QPolygonF([
        QPointF(x, y), QPointF(x + size, y), QPointF(x, y + size), QPointF(x + size, y + size),
    ]))


def draw_exclamation(p, x, y, height):
    """用线条画一个感叹号"""
    p.setPen(pen(12))
    p.drawLine(QPointF(x, y), QPointF(x, y + height * 0.65))
    p.drawPoint(QPointF(x, y + height))


# ---------- 各状态的画面 ----------

def idle():
    img, p = new_canvas()
    draw_body(p)
    draw_hand(p, 120, 360)
    draw_hand(p, 392, 360)
    draw_closed_eyes(p)
    draw_small_mouth(p)
    draw_z(p, 392, 100, 28)
    draw_z(p, 432, 40, 42)
    p.end()
    return img


def working(down=None):
    """down=None 抬手；"left"/"right" 哪只手敲下"""
    img, p = new_canvas()
    draw_body(p)
    draw_open_eyes(p)
    draw_small_mouth(p)
    draw_keyboard(p)
    left_y = 410 if down == "left" else 345
    right_y = 410 if down == "right" else 345
    draw_hand(p, 180, left_y)
    draw_hand(p, 332, right_y)
    if down == "left":
        draw_tap_lines(p, 180)
    elif down == "right":
        draw_tap_lines(p, 332)
    p.end()
    return img


def excited(frame):
    dy = 0 if frame == 1 else -18  # 第 2 帧整体往上跳一点
    img, p = new_canvas()
    draw_body(p, dy)
    draw_hand(p, 104, 250 + dy)    # 双手举起
    draw_hand(p, 408, 250 + dy)
    draw_happy_eyes(p, dy)
    draw_blush(p, dy)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#E0566F"))
    p.drawChord(QRectF(232, 258 + dy, 48, 44), 0, -180 * 16)  # 张嘴笑
    draw_envelope(p, 360, 20 + (0 if frame == 1 else 10))
    draw_exclamation(p, 80, 50 + dy, 70)
    draw_exclamation(p, 116, 70 + dy, 52)
    if frame == 2:
        draw_sparkle(p, 340, 40, 14)
        draw_sparkle(p, 470, 140, 10)
    p.end()
    return img


def thinking(frame):
    """思考：眼睛往上看、一只手托着下巴，头顶的"…"一个点一个点冒出来（frame = 1/2/3）"""
    img, p = new_canvas()
    draw_body(p)
    draw_hand(p, 120, 360)
    draw_hand(p, 300, 318)                      # 托下巴的手
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    p.drawEllipse(QPointF(208, 222), 13, 16)    # 眼珠往右上看
    p.drawEllipse(QPointF(320, 222), 13, 16)
    p.setPen(pen(6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(244, 278), QPointF(270, 274))  # 抿着嘴
    # 头顶右上方的思考小泡泡 + 点点
    p.setPen(pen(5, QColor("#9AA5B1")))
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QPointF(372, 112), 9, 9)
    p.drawEllipse(QPointF(396, 80), 14, 14)
    p.drawRoundedRect(QRectF(368, 14, 128, 56), 28, 28)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    for i in range(frame):
        p.drawEllipse(QPointF(400 + i * 32, 42), 8, 8)
    p.end()
    return img


def sleeping(frame):
    """睡觉：戴睡帽、闭眼，身体随呼吸微微起伏，Z 一大一小交替（frame = 1/2）"""
    dy = 0 if frame == 1 else 6                 # 第 2 帧"呼气"，身体低一点
    img, p = new_canvas()
    draw_body(p, dy)
    draw_hand(p, 150, 380 + dy)                 # 双手放松地垂着
    draw_hand(p, 362, 380 + dy)
    p.setPen(pen(8))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(180, 238 + dy), QPointF(220, 238 + dy))   # 闭眼：两条横线
    p.drawLine(QPointF(292, 238 + dy), QPointF(332, 238 + dy))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#9FB4C8"))
    p.drawEllipse(QPointF(256, 286 + dy), 9, 7)                   # 小嘴微张
    # 睡帽：一个歪向右边的三角形 + 白色毛球
    p.setBrush(QColor("#6C7EE1"))
    p.drawPolygon(QPolygonF([QPointF(150, 150 + dy), QPointF(330, 118 + dy), QPointF(420, 40 + dy)]))
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QPointF(424, 38 + dy), 18, 18)
    if frame == 1:
        draw_z(p, 80, 120, 26)
    else:
        draw_z(p, 60, 90, 22)
        draw_z(p, 30, 40, 36)
    p.end()
    return img


def draw_gear(p, x, y, angle):
    """齿轮（转动的角度 angle，度）：表示"在干活"。"""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#8A96A3"))
    for i in range(8):
        a = math.radians(angle + i * 45)
        p.drawEllipse(QPointF(x + 30 * math.cos(a), y + 30 * math.sin(a)), 9, 9)   # 齿
    p.drawEllipse(QPointF(x, y), 26, 26)
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QPointF(x, y), 10, 10)


def draw_question(p, x, y, size):
    """用线条画一个问号（不依赖字体）：上面半圈 + 往下一竖 + 一个点。"""
    p.setPen(pen(max(8, size // 6), QColor("#E07A2E")))
    p.setBrush(Qt.BrushStyle.NoBrush)
    r = size * 0.32
    p.drawArc(QRectF(x - r, y, 2 * r, 2 * r), 180 * 16, -270 * 16)   # 从左边顺时针绕到下面
    p.drawLine(QPointF(x, y + 2 * r), QPointF(x, y + size * 0.78))
    p.drawPoint(QPointF(x, y + size))


def busy(frame):
    """在干活（助手读文件、跑命令）：低头看着手里的文件，头顶的齿轮在转（frame = 1/2/3）。"""
    img, p = new_canvas()
    draw_body(p)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(INK)
    p.drawEllipse(QPointF(204, 248), 13, 14)    # 眼睛往下看
    p.drawEllipse(QPointF(308, 248), 13, 14)
    p.setPen(pen(6))
    p.drawLine(QPointF(242, 284), QPointF(270, 284))   # 认真地抿着嘴
    p.setPen(pen(5))                            # 手里的一张纸（带几行字）
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(176, 318, 160, 110), 10, 10)
    p.setPen(pen(5, QColor("#9AA5B1")))
    for i, width in enumerate((110, 90, 120)):
        p.drawLine(QPointF(196, 344 + i * 26), QPointF(196 + width, 344 + i * 26))
    draw_hand(p, 170, 380)
    draw_hand(p, 342, 380)
    draw_gear(p, 440, 70, frame * 15)
    p.end()
    return img


def waiting(frame):
    """等你确认：看着你、举起一只手招呼，头顶冒个问号（frame = 1/2 手的高度、问号上下跳一下）。"""
    img, p = new_canvas()
    draw_body(p)
    draw_hand(p, 120, 360)
    draw_hand(p, 410, 200 if frame == 1 else 176)   # 举手招呼
    draw_open_eyes(p)
    p.setPen(pen(6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(256, 276), 10, 12)    # o 形小嘴："嗯？"
    draw_blush(p)
    draw_question(p, 440, 20 if frame == 1 else 30, 90)
    p.end()
    return img


def lifted(frame):
    """被拎起来：后颈的皮被捏住，身体垂下来变长，手垂着、脚悬空乱蹬，一脸懵（frame = 1/2 脚的位置不同）。

    拖动时会以头顶正中为支点左右摆动，所以身体画得比其他状态窄，左右多留一些透明边。
    """
    img, p = new_canvas()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BODY)
    p.drawPolygon(QPolygonF([QPointF(232, 124), QPointF(256, 40), QPointF(280, 124)]))   # 被捏起来的后颈
    p.drawEllipse(QRectF(146, 100, 220, 300))                                             # 身体：窄一些、长一些
    draw_hand(p, 158, 300)                      # 手无力地垂在两边
    draw_hand(p, 354, 300)
    kick = 0 if frame == 1 else 16              # 两只脚交替蹬一下
    p.setBrush(HAND)
    p.drawEllipse(QPointF(214, 412 + kick), 26, 22)
    p.drawEllipse(QPointF(298, 428 - kick), 26, 22)
    draw_open_eyes(p, -15)                      # 睁大眼睛
    p.setPen(pen(6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(256, 270), 11, 13)    # o 形小嘴：有点懵
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(BLUSH)
    p.drawEllipse(QPointF(190, 270), 20, 11)
    p.drawEllipse(QPointF(322, 270), 20, 11)
    p.setBrush(QColor("#A8D8F8"))               # 一滴汗
    p.drawPolygon(QPolygonF([QPointF(372, 132), QPointF(362, 156), QPointF(382, 156)]))
    p.drawEllipse(QPointF(372, 160), 10, 10)
    p.end()
    return img


def main():
    app = QGuiApplication(sys.argv)  # 使用 QPainter 等图形功能前需要先有 QGuiApplication
    OUT.mkdir(parents=True, exist_ok=True)
    images = {
        "idle_1.png": idle(),
        "working_up.png": working(),
        "working_down_1.png": working("left"),
        "working_down_2.png": working("right"),
        "excited_1.png": excited(1),
        "excited_2.png": excited(2),
        "thinking_1.png": thinking(1),
        "thinking_2.png": thinking(2),
        "thinking_3.png": thinking(3),
        "sleeping_1.png": sleeping(1),
        "sleeping_2.png": sleeping(2),
        "lifted_1.png": lifted(1),
        "lifted_2.png": lifted(2),
        "busy_1.png": busy(1),
        "busy_2.png": busy(2),
        "busy_3.png": busy(3),
        "waiting_1.png": waiting(1),
        "waiting_2.png": waiting(2),
    }
    args = [a for a in sys.argv[1:] if a != "--force"]
    force = "--force" in sys.argv[1:]
    for name, img in images.items():
        if args and not any(name.startswith(prefix) for prefix in args):
            continue
        path = OUT / name
        if path.exists() and not force:
            print("已有，跳过（要覆盖加 --force）", path)
            continue
        img.save(str(path))
        print("已生成", path)


if __name__ == "__main__":
    main()
