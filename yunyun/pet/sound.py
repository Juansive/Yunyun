"""芸芸的小音效（2026-10-16：双击 / 一直点时每弹一下"啵"一声）。

用 Windows 自带的 winsound 异步播程序自带的 wav（assets/sounds/，tools/make_sounds.py 合成的）：不加任何库，
节能版也能响，体积只多几 KB。新的一声会打断还没放完的上一声（一直点的时候正好）。
_play_file 是真的出声的地方：测试里换成假的（conftest），绝不真的出声。
"""
import logging

from yunyun import paths

log = logging.getLogger(__name__)


def play(name):
    """放 assets/sounds/<name>.wav（马上返回，不等放完）；文件没有 / 放不了只记日志。"""
    path = paths.resource_path("assets", "sounds", f"{name}.wav")
    if not path.is_file():
        log.warning("没有音效文件：%s", path.name)
        return False
    try:
        _play_file(str(path))
    except (RuntimeError, OSError) as e:   # 没有声卡、被别的程序占着
        log.info("音效没放出来：%s", e)
        return False
    return True


def _play_file(path):
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
