"""芸芸这一头：在后台线程里下载识别模型（models.download），进度变成 Qt 信号。

只有一个（downloader()，挂在 QApplication 上）：设置窗口关了也接着下，再打开设置页还能看到进度。
"""
import logging
import threading
import time

from PySide6.QtCore import QCoreApplication, QObject, Signal

from yunyun.voice import models

log = logging.getLogger(__name__)
PROGRESS_EVERY_S = 0.2


class ModelDownload(QObject):
    progress = Signal(int, int)   # 已下字节, 总字节
    finished = Signal(str)        # "" = 下好了；否则是 models.DownloadError 的 kind

    def __init__(self, parent=None, download=None):
        """download：换成假的（测试）；不给就用 models.download（运行时才查，conftest 换掉它也管用）。"""
        super().__init__(parent)
        self._download = download
        self._thread = None
        self._cancel = threading.Event()
        self.done = self.total = 0

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._cancel = threading.Event()
        self.done, self.total = 0, models.TOTAL_BYTES
        self._thread = threading.Thread(target=self._run, name="voice-model-download", daemon=True)
        self._thread.start()

    def cancel(self):
        self._cancel.set()

    def _run(self):
        last = 0.0

        def progress(done, total):
            nonlocal last
            self.done, self.total = done, total
            if time.monotonic() - last >= PROGRESS_EVERY_S or done >= total:
                last = time.monotonic()
                self.progress.emit(done, total)   # 从后台线程发：Qt 自动排到主线程去

        try:
            (self._download or models.download)(progress, self._cancel.is_set)
            kind = ""
        except models.DownloadError as e:
            log.info("识别模型没下载成功：%s", e)
            kind = e.kind
        except Exception:
            log.exception("下载识别模型时出现意外错误")
            kind = "network"
        self.finished.emit(kind)


_instance = None


def downloader():
    global _instance
    if _instance is None:
        _instance = ModelDownload(QCoreApplication.instance())
    return _instance
