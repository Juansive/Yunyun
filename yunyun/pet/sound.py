"""芸芸的小音效（2026-10-16：双击芸芸时出声；一直点就一直叫）。

自带的只有"哔哔"（assets/sounds/bounce.wav，tools/make_sounds.py 合成的，几十 KB）；另外几段 meme 音效（EXTRA）不进压缩包，
在设置 → 通用里选了才下载（用户要的），存芸芸的数据目录。它们放在 GitHub 仓库的 sounds/ 里：先走 jsDelivr（国内一般连得上），
再试 GitHub 原地址；大小和 SHA-256 写死，下完对一遍（yunyun/fetch.py）。

用 Windows 自带的 winsound 异步播 wav：不加任何库，节能版也能响。新的一声会打断还没放完的上一声（一直点的时候正好）。
_play_file 是真的出声的地方：测试里换成假的（conftest），绝不真的出声。
"""
import logging
import threading

from PySide6.QtCore import QCoreApplication, QObject, Signal

from yunyun import fetch, paths

log = logging.getLogger(__name__)

BUNDLED = "bounce"
EXTRA = {   # 名字 → (字节数, SHA-256)：B 站"250个meme音效合集"切出来的（来源和编号见 sounds/README.md）
    "man": (52_082, "8e7220fb8962de5a3c8c838c99e4d93bae8e00e5ece879f64c61113dd2f777ff"),
    "manbo": (41_498, "5c6abd7eb974daedec6454317f8144504f2100ea0e98cdab1c63435c181a9618"),
    "chicken": (60_020, "b88a1c9daf57a9a2f01b76bd991e819283b6252560ccafbc57dfd28299aa1954"),
    "gugugaga": (101_474, "64c31d8659542c758103296b63c31324c5702be8b254a57e5c74cf51ce7d4982"),
}
NAMES = (BUNDLED, *EXTRA)   # 设置里的顺序
SOURCES = ("https://cdn.jsdelivr.net/gh/Juansive/Yunyun@master/sounds/",
           "https://raw.githubusercontent.com/Juansive/Yunyun/master/sounds/")


def extra_dir():
    """下载的音效放这里（测试里换到临时目录）。"""
    return paths.data_dir() / "sounds"


def path(name):
    if name == BUNDLED:
        return paths.resource_path("assets", "sounds", f"{name}.wav")
    return extra_dir() / f"{name}.wav"


def available(name):
    """自带的，或者下好了的（大小对；SHA-256 下载时对过了）。"""
    if name == BUNDLED:
        return True
    target = path(name)
    return name in EXTRA and target.is_file() and target.stat().st_size == EXTRA[name][0]


def download(name):
    """下载一段音效（在后台线程里调用；出错抛 fetch.DownloadError）。"""
    size, sha256 = EXTRA[name]
    target = path(name)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise fetch.DownloadError("disk", str(e)) from e
    fetch.fetch_any([f"{source}{name}.wav" for source in SOURCES], target, size, sha256)


class Download(QObject):
    """在后台线程里下载（几十 KB，一般一两秒）；挂在 QApplication 上：设置窗口关了也会下完。"""
    finished = Signal(str, str)   # 名字, "" = 下好了 / fetch.DownloadError 的 kind

    def start(self, name):
        threading.Thread(target=self._run, args=(name,), name="sound-download", daemon=True).start()

    def _run(self, name):
        try:
            download(name)
            kind = ""
        except fetch.DownloadError as e:
            log.info("音效 %s 没下载成功：%s", name, e)
            kind = e.kind
        except Exception:
            log.exception("下载音效时出现意外错误")
            kind = "network"
        self.finished.emit(name, kind)   # 从后台线程发：Qt 自动排到主线程去


_downloader = None


def downloader():
    global _downloader
    if _downloader is None:
        _downloader = Download(QCoreApplication.instance())
    return _downloader


def play(name):
    """放一段音效（马上返回，不等放完）；没有 / 放不了只记日志。"""
    target = path(name)
    if not target.is_file():
        log.warning("没有音效文件：%s", target.name)
        return False
    try:
        _play_file(str(target))
    except (RuntimeError, OSError) as e:   # 没有声卡、被别的程序占着
        log.info("音效没放出来：%s", e)
        return False
    return True


def _play_file(path):
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
