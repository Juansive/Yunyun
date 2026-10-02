"""加载芸芸各个状态的图片帧。

图片放在 assets/pet/，文件名是固定的（详细要求见 assets/pet/README.md）：

    idle_1.png  [idle_2.png ...]                  待机（多张时循环播放）
    working_up.png                                工作：抬手
    working_down_1.png  [working_down_2.png ...]  工作：敲下（每敲一次换下一张）
    excited_1.png  [excited_2.png ...]            激动（多张时循环播放）
    thinking_1.png [thinking_2.png ...]           思考（多张时循环播放）
    sleeping_1.png [sleeping_2.png ...]           睡觉（免打扰时段，多张时循环播放）
    [lifted_1.png  lifted_2.png ...]              被拎起来（拖动时；没有就保持原来的画面）
    [tray.png]                                    托盘图标（没有就用 idle_1.png）

方括号里的是可选的：想加帧，按编号往后加文件就行，不用改代码。
"""
from dataclasses import dataclass

from PySide6.QtGui import QPixmap

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


def load_sprites(folder=ASSET_DIR):
    """读取全部图片。注意：QPixmap 必须在创建 QApplication 之后才能用。"""
    idle = _load_series(folder, "idle")
    tray_path = folder / "tray.png"
    return Sprites(
        idle=idle,
        working_up=_load(folder / "working_up.png"),
        working_down=_load_series(folder, "working_down"),
        excited=_load_series(folder, "excited"),
        thinking=_load_series(folder, "thinking"),
        sleeping=_load_series(folder, "sleeping"),
        lifted=_load_series(folder, "lifted", required=False),
        tray=_load(tray_path) if tray_path.exists() else idle[0],
    )


def _load(path):
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        raise FileNotFoundError(f"无法加载图片：{path}")
    return pixmap


def _load_series(folder, prefix, required=True):
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
    return [_load(p) for p in paths]
