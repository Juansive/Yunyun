"""加载芸芸各个状态的图片帧。

图片放在 assets/pet/，文件名是固定的（详细要求见 assets/pet/README.md）：

    idle_1.png  [idle_2.png ...]                  待机（多张时循环播放）
    working_up.png                                工作：抬手
    working_down_1.png  [working_down_2.png ...]  工作：敲下（每敲一次换下一张）
    excited_1.png  [excited_2.png ...]            激动（多张时循环播放）
    thinking_1.png [thinking_2.png ...]           思考（多张时循环播放）
    sleeping_1.png [sleeping_2.png ...]           睡觉（免打扰时段，多张时循环播放）
    [busy_1.png    busy_2.png ...]                助手在干活（读文件、跑命令；没有就用思考的图）
    [waiting_1.png waiting_2.png ...]             助手等你确认（没有就用思考的图）
    [talking_1.png talking_2.png ...]             说话（头顶有气泡时，嘴一张一合；没有就用激动的图）
    [lifted_1.png  lifted_2.png ...]              被拎起来（拖动时；没有就保持原来的画面）
    [tray.png]                                    托盘图标（没有就用 idle_1.png）

方括号里的是可选的：想加帧，按编号往后加文件就行，不用改代码。

内存：一张图解码后占"宽 × 高 × 4"字节（512×512 就是 1 MB，2048×2048 是 16 MB），
而桌面上芸芸只有 160 个逻辑像素大。所以加载时用 max_size 直接缩小到显示需要的大小，原图不留在内存里。
"""
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from yunyun.paths import resource_path

ASSET_DIR = resource_path("assets", "pet")   # 开发版在项目文件夹里，打包后在 exe 旁边的 _internal 里


@dataclass
class Sprites:
    idle: list[QPixmap]
    working_up: QPixmap
    working_down: list[QPixmap]
    excited: list[QPixmap]
    thinking: list[QPixmap]
    sleeping: list[QPixmap]
    lifted: list[QPixmap]   # 可选：可能是空列表
    tray: QPixmap
    busy: list[QPixmap]     # 可选：没有就是思考的图
    waiting: list[QPixmap]  # 可选：没有就是思考的图
    talking: list[QPixmap]  # 可选：没有就是激动的图


def load_sprites(folder=ASSET_DIR, max_size=None):
    """读取全部图片。max_size：比它大的图缩小到不超过 max_size × max_size（物理像素），None = 保持原样。

    注意：QPixmap 必须在创建 QApplication 之后才能用。
    """
    def load(path):
        return _load(path, max_size)

    def series(prefix, required=True):
        return _load_series(folder, prefix, required, max_size)

    idle = series("idle")
    thinking = series("thinking")
    excited = series("excited")
    tray_path = folder / "tray.png"
    return Sprites(
        idle=idle,
        working_up=load(folder / "working_up.png"),
        working_down=series("working_down"),
        excited=excited,
        thinking=thinking,
        sleeping=series("sleeping"),
        lifted=series("lifted", required=False),
        tray=load(tray_path) if tray_path.exists() else idle[0],
        busy=series("busy", required=False) or thinking,          # 助手模式（阶段 4）加的，画师的图到之前可能没有
        waiting=series("waiting", required=False) or thinking,
        talking=series("talking", required=False) or excited,      # 2026-10-09 加的
    )


def _load(path, max_size=None):
    # 用 QImage 读，而不是 QPixmap(路径)：后者会把原图另外存一份在 Qt 的图片缓存（QPixmapCache，最多 10 MB）里，
    # 缩小了也省不下内存
    image = QImage(str(path))
    if image.isNull():
        raise FileNotFoundError(f"无法加载图片：{path}")
    if max_size is not None and (image.width() > max_size or image.height() > max_size):
        # 等比例平滑缩小；原来的大图没人引用了，Python 会自动释放它的内存
        image = image.scaled(max_size, max_size, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return QPixmap.fromImage(image)


def _load_series(folder, prefix, required=True, max_size=None):
    """读取 prefix_1.png、prefix_2.png …，按编号（数字）排序。可选的一组图没有时返回空列表。"""
    def number(path):
        suffix = path.stem[len(prefix) + 1:]  # "idle_12" → "12"
        return int(suffix) if suffix.isdigit() else None

    # 按数字排序，而不是按字符串排序（否则 "10" 会排在 "2" 前面）
    paths = sorted(
        (p for p in folder.glob(f"{prefix}_*.png") if number(p) is not None),
        key=number,
    )
    if not paths and required:
        raise FileNotFoundError(f"缺少图片：{folder / (prefix + '_1.png')}")
    return [_load(p, max_size) for p in paths]   # 一张张解码、马上缩小，内存里不会同时有很多张大图
