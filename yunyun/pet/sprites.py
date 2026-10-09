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
# 助手模式的形象（2026-10-09 用户要的）：文件名和上面一样，开着助手模式时用；这里没有的组用平时那套的
ASSISTANT_DIR = ASSET_DIR / "assistant"


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


_REQUIRED = ("idle", "working_down", "excited", "thinking", "sleeping")
# 可选的组：没有就用哪一组的图（None = 空着）。busy / waiting 是助手模式（阶段 4）加的，talking 是 2026-10-09 加的
_STAND_INS = {"busy": "thinking", "waiting": "thinking", "talking": "excited", "lifted": None}


def load_sprites(folder=ASSET_DIR, max_size=None, base=None):
    """读取全部图片。max_size：比它大的图缩小到不超过 max_size × max_size（物理像素），None = 保持原样。
    base：另一套已经读好的图（Sprites）——folder 里没有的组用它的（助手模式的形象，见 ASSISTANT_DIR）。

    注意：QPixmap 必须在创建 QApplication 之后才能用。
    """
    loaded = {}

    def own(name):
        """folder 里的这一组（没有是 []）。读过的不再读：替代的组和原来那组是同一个列表。"""
        if name not in loaded:
            loaded[name] = _load_series(folder, name, max_size)
        return loaded[name]

    def group(name):
        """这一组：folder 里的 → folder 里的替代组（比如"在干活"用"思考"）→ base 的；必需的都没有就报错。"""
        frames = own(name) or (own(_STAND_INS[name]) if _STAND_INS.get(name) else [])
        if frames:
            return frames
        if base is not None:
            return getattr(base, name)
        if name in _REQUIRED:
            raise FileNotFoundError(f"缺少图片：{folder / (name + '_1.png')}")
        return []

    groups = {name: group(name) for name in (*_REQUIRED, *_STAND_INS)}
    up, tray = folder / "working_up.png", folder / "tray.png"
    return Sprites(
        **groups,
        working_up=base.working_up if base is not None and not up.exists() else _load(up, max_size),
        # 托盘图标一直用平时那套的
        tray=base.tray if base is not None else _load(tray, max_size) if tray.exists() else groups["idle"][0],
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


def _load_series(folder, prefix, max_size=None):
    """读取 prefix_1.png、prefix_2.png …，按编号（数字）排序。没有就返回空列表（folder 不存在也是）。"""
    def number(path):
        suffix = path.stem[len(prefix) + 1:]  # "idle_12" → "12"
        return int(suffix) if suffix.isdigit() else None

    # 按数字排序，而不是按字符串排序（否则 "10" 会排在 "2" 前面）
    paths = sorted(
        (p for p in folder.glob(f"{prefix}_*.png") if number(p) is not None),
        key=number,
    )
    return [_load(p, max_size) for p in paths]   # 一张张解码、马上缩小，内存里不会同时有很多张大图
