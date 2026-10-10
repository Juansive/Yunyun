"""新版本提醒：问 GitHub 上最新的 Release 是哪个版本（只读版本号，不下载任何东西）。

- 启动 1 分钟后看一眼，之后芸芸开着时每小时看一眼"离上次查是不是满一天了"——每天最多联网查一次；
- 设置 → 通用"自动检查新版本"能关（关了就一次都不自动查）；设置 → 关于"检查更新"随时手动查；
- 发现新版本：记下来（重启后右键菜单里还有"下载新版本"），芸芸说一次（同一个版本只说一次，见 PetWindow.on_new_version）。
- 只连 api.github.com（走系统代理），请求里只有程序名和版本号（GitHub 要求带 User-Agent），不带你的任何信息。
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from yunyun import __version__

RELEASES_API = "https://api.github.com/repos/Juansive/Yunyun/releases/latest"   # 最新的正式版（不含草稿、预览版）
RELEASES_PAGE = "https://github.com/Juansive/Yunyun/releases/latest"
CHECK_EVERY_S = 24 * 3600
FIRST_CHECK_MS = 60_000     # 启动后等一会儿再查：不和启动抢网络、抢时间
TICK_MS = 3600_000          # 每小时看一眼满没满一天（芸芸常年开着，靠这个每天查一次）
_KEY_AUTO = "update/auto_check"
_KEY_LAST = "update/last_check"         # 上次联网查的时间（成功失败都算：网络不通时也别一直重试）
_KEY_LATEST = "update/latest"           # 查到的比自己新的版本号和下载页："0.6.2|https://…"

log = logging.getLogger(__name__)


class UpdateError(Exception):
    """没查到（连不上、GitHub 回的东西不对）。"""


def parse_version(text):
    """"v0.6.2" / "0.6.2" → (0, 6, 2)；不像版本号返回 None。"""
    try:
        return tuple(int(n) for n in str(text).strip().lstrip("vV").split("."))
    except ValueError:
        return None


def is_newer(latest, current=__version__):
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def fetch_latest(timeout=15):
    """联网问最新版本：返回 (版本号, 下载页)。出错抛 UpdateError。测试里 conftest 把它换成"不许联网"。"""
    request = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"Yunyun/{__version__}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (OSError, ValueError) as e:   # 断网、超时、HTTP 错误（URLError / HTTPError 都是 OSError）、回的不是 JSON
        raise UpdateError(str(e)) from e
    version = str(data.get("tag_name") or "").lstrip("vV")
    if parse_version(version) is None:
        raise UpdateError(f"看不懂的版本号：{data.get('tag_name')!r}")
    return version, str(data.get("html_url") or RELEASES_PAGE)


class UpdateChecker(QObject):
    found = Signal(str, str)          # (新版本号, 下载页)：比自己新才发
    checked = Signal(object, object)  # (最新版本号或 None, 错误说明或 None)：每次查完都发（关于页显示结果用）
    _done = Signal(object, object)    # 后台线程 → 主线程

    def __init__(self, parent=None):
        super().__init__(parent)
        self._done.connect(self._on_done)
        self._checking = False
        self.latest = None            # (版本号, 下载页)：查到的比自己新的版本；没有就是 None
        self._tick = QTimer(self, interval=TICK_MS, timeout=self._maybe_check)

    # ---------- 对外 ----------

    def start(self):
        """芸芸启动时调用：先恢复上次查到的新版本，再按计划自动查。"""
        saved = str(QSettings().value(_KEY_LATEST, "") or "")
        version, _, url = saved.partition("|")
        if is_newer(version):         # 更新过了的话，记着的那个就不算新了
            self.latest = (version, url or RELEASES_PAGE)
            QTimer.singleShot(0, lambda: self.found.emit(*self.latest))   # 等界面都连好了再发
        QTimer.singleShot(FIRST_CHECK_MS, self._maybe_check)
        self._tick.start()

    @property
    def auto_check(self):
        return QSettings().value(_KEY_AUTO, True, type=bool)

    def set_auto_check(self, on):
        QSettings().setValue(_KEY_AUTO, bool(on))

    def check_now(self):
        """马上查一次（关于页的"检查更新"）。正在查就不重复查。"""
        if self._checking:
            return
        self._checking = True
        QSettings().setValue(_KEY_LAST, time.time())
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------- 内部 ----------

    def _maybe_check(self):
        last = float(QSettings().value(_KEY_LAST, 0) or 0)
        if self.auto_check and time.time() - last >= CHECK_EVERY_S:
            self.check_now()

    def _worker(self):
        """后台线程：只联网，不碰界面。"""
        try:
            result, error = fetch_latest(), None
        except UpdateError as e:
            result, error = None, str(e)
        except Exception as e:   # 兜底：不能让"正在查"一直卡着
            log.exception("检查新版本时出现意外错误")
            result, error = None, str(e)
        try:
            self._done.emit(result, error)
        except RuntimeError:
            pass   # 芸芸正在退出，结果没人要了

    def _on_done(self, result, error):
        self._checking = False
        if error is not None:
            log.info("检查新版本没成功：%s", error)
            self.checked.emit(None, error)
            return
        version, url = result
        newer = is_newer(version)
        if newer:
            log.info("有新版本 %s（现在是 %s）", version, __version__)
            QSettings().setValue(_KEY_LATEST, f"{version}|{url}")
            self.latest = (version, url)
        self.checked.emit(version, None)   # 关于页靠 self.latest 判断有没有新版本：先记下再发
        if newer:
            self.found.emit(version, url)
