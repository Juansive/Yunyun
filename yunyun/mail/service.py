"""邮件服务：定时检查邮箱、读取待处理的邮件，通过 Qt 信号告诉界面。

- 每 2 分钟检查一次（只查有哪些未读，不下载内容）；
- 只有用户点了小红点，才调用 fetch_pending() 真正读取邮件内容；
- 网络操作都在后台线程里做；对外发出的信号都在主线程里发出，界面可以放心使用。
"""
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Signal

from yunyun.config import ConfigError
from yunyun.mail import accounts
from yunyun.mail.imap_client import MailError, check_unseen, fetch_messages
from yunyun.mail.tracker import MailTracker


def default_state_path():
    """用户数据目录（Windows 上是 %APPDATA%\\Yunyun\\Yunyun）里的 mail_state.json。"""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "mail_state.json"


class MailService(QObject):
    POLL_INTERVAL_MS = 120_000   # 多久检查一次邮箱
    FIRST_POLL_DELAY_MS = 2_000  # 启动后稍等一下再检查，先让芸芸出现
    MAX_FETCH = 20               # 一次最多处理多少封（每个邮箱）

    # ---- 对外信号（都在主线程发出）----
    pending_changed = Signal(int, int)           # (待处理总数, 这次新到达的封数)
    fetch_started = Signal()                     # 开始处理（用户点了小红点）
    message_ready = Signal(str, object, object)  # (邮箱地址, MailMessage, 处理结果)：处理好一封发一次
    fetch_finished = Signal()                    # 这一轮处理结束
    auth_failed = Signal(str)                    # 某个邮箱的授权码失效了（每个邮箱每次运行只提醒一次）

    # ---- 内部信号：后台线程 → 主线程 ----
    _poll_done = Signal(object)
    _fetched = Signal(str, object, object)
    _fetch_done = Signal()

    def __init__(self, tracker=None, process_message=None, parent=None):
        """process_message(message)：可选，在后台线程里对每封邮件调用（比如交给 AI），
        它的返回值会随 message_ready 一起送出。不给就送出 None。"""
        super().__init__(parent)
        self._tracker = tracker or MailTracker(default_state_path())
        self._process = process_message
        self._accounts = []
        self._polling = False
        self._fetching = False
        self._auth_warned = set()

        self._poll_done.connect(self._on_poll_done)
        self._fetched.connect(self.message_ready)   # 信号连信号：直接转发
        self._fetch_done.connect(self._on_fetch_done)

        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_now)

    @property
    def pending_count(self):
        return self._tracker.total_pending()

    @property
    def fetching(self):
        return self._fetching

    @property
    def has_accounts(self):
        return bool(self._accounts)

    def start(self):
        self._load_accounts()
        self._timer.start()
        QTimer.singleShot(self.FIRST_POLL_DELAY_MS, self.poll_now)

    def reload_accounts(self):
        """用户添加 / 删除了邮箱：重新读取账号列表，并马上检查一次。"""
        self._load_accounts()
        self.pending_changed.emit(self.pending_count, 0)
        self.poll_now()

    # ---------- 定时检查 ----------

    def poll_now(self):
        if self._polling or not self._accounts:
            return  # 上一次还没查完，或者还没有邮箱
        self._polling = True
        threading.Thread(target=self._poll_worker, args=(list(self._accounts),), daemon=True).start()

    def _poll_worker(self, account_list):
        """后台线程：逐个邮箱检查未读。一个邮箱出错不影响其他邮箱。"""
        results = []
        for account in account_list:
            try:
                results.append((account, check_unseen(account), None))
            except MailError as e:
                results.append((account, None, e))
            except Exception as e:  # 兜底：意外错误也要送回主线程，不能让 _polling 永远是 True
                results.append((account, None, MailError(str(e), detail=str(e))))
        _safe_emit(self._poll_done, results)

    def _on_poll_done(self, results):
        self._polling = False
        newly = 0
        for account, status, error in results:
            if error is not None:
                if error.kind in ("login", "no_password") and account.address not in self._auth_warned:
                    self._auth_warned.add(account.address)
                    self.auth_failed.emit(account.address)
                continue  # 断网之类的错误：安静地等下一次检查
            self._auth_warned.discard(account.address)
            newly += len(self._tracker.update(account.address, status.uidvalidity, status.uidnext, status.unseen))
        self.pending_changed.emit(self.pending_count, newly)

    # ---------- 读取待处理的邮件（用户点了小红点）----------

    def fetch_pending(self):
        """读取并处理待处理的邮件（最新的在前）。真的开始处理了返回 True。"""
        if self._fetching:
            return False
        jobs = [(a, self._tracker.pending(a.address)[: self.MAX_FETCH]) for a in self._accounts]
        jobs = [(a, uids) for a, uids in jobs if uids]
        if not jobs:
            return False
        self._fetching = True
        self.fetch_started.emit()
        threading.Thread(target=self._fetch_worker, args=(jobs,), daemon=True).start()
        return True

    def _fetch_worker(self, jobs):
        for account, uids in jobs:
            try:
                for message in fetch_messages(account, uids):  # 读到一封、处理一封、送出一封
                    _safe_emit(self._fetched, account.address, message, self._run_process(message))
            except Exception:
                pass  # 这个邮箱读失败了：没读到的邮件仍然留在待处理里，下次点击再试
        _safe_emit(self._fetch_done)

    def _run_process(self, message):
        if self._process is None:
            return None
        try:
            return self._process(message)
        except Exception:
            return None  # 处理出意外：这封照样送出（界面会用兜底文字），不影响后面的邮件

    def _on_fetch_done(self):
        self._fetching = False
        self.fetch_finished.emit()

    # ---------- 用户操作 ----------

    def mark_handled(self, address, uid):
        self._tracker.mark_handled(address, uid)
        self.pending_changed.emit(self.pending_count, 0)

    def dismiss_all(self):
        """全部标为已看，返回清掉的封数。"""
        count = self._tracker.dismiss_all()
        self.pending_changed.emit(self.pending_count, 0)
        return count

    # ---------- 内部 ----------

    def _load_accounts(self):
        try:
            self._accounts = accounts.load_accounts()
        except (ConfigError, MailError):
            self._accounts = []
        current = {a.address for a in self._accounts}
        for address in self._tracker.known_addresses():
            if address not in current:
                self._tracker.forget(address)  # 邮箱被删了，计数里也去掉


def _safe_emit(signal, *args):
    """在后台线程里发信号。程序正在退出时对象可能已被销毁，这时安静地放弃。"""
    try:
        signal.emit(*args)
    except RuntimeError:
        pass
