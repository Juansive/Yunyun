"""生成芸芸的占位图。

等画师的正式素材到了，直接用同名文件覆盖 assets/pet/ 里的图片即可，代码不用改。
素材要求见 assets/pet/README.md。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python tools\\make_placeholders.py
"""
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
    }
    for name, img in images.items():
        img.save(str(OUT / name))
        print("已生成", OUT / name)


if __name__ == "__main__":
    main()
