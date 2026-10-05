"""IMAP IDLE：和邮箱服务器保持一个连接，新邮件一到，服务器主动通知，小红点几秒内就能出现。

原来每 2 分钟检查一次，新邮件最慢要等 2 分钟。IDLE 的对话是这样的（RFC 2177）：

    芸芸：  A5 IDLE
    服务器：+ idling                 ← 进入"等待"状态，连接一直开着
    服务器：* 23 EXISTS              ← 来了新邮件，服务器主动发这样一行（没有就一直等）
    芸芸：  DONE                     ← 结束等待
    服务器：A5 OK IDLE terminated

收到通知后，芸芸在同一个连接上重新只读打开收件箱、查一下"有哪些未读"（和定时检查一样，只查编号、
不下载内容），再接着 IDLE。

但服务器不一定推送。实测（2026-10-05）QQ 邮箱的连接看到的是"快照"：打开收件箱以后来的新邮件，
在这个连接上既不推送（IDLE），问也问不出来（NOOP），必须重新打开收件箱才能看见。所以：
- 刚连上时先当作服务器不推送：每 FAST_RENEW_S 秒结束一次 IDLE、重新打开收件箱看一眼
  （同一个连接，不用重新登录，很便宜）。QQ 邮箱的新邮件最慢 15 秒就能发现。
- 一旦收到过服务器的推送（比如 Gmail），说明它会推送，就改成每 RENEW_S 秒才看一次：
  RFC 建议最长 29 分钟，路由器 / 服务器可能更早悄悄断开空闲连接；顺便能发现服务器没推送的变化（比如在手机上读了邮件）。
- 服务器不支持 IDLE（登录后也没声明）：这个邮箱继续用定时检查。
- 断网等错误：等一会儿重连（等待时间逐渐加长，最长 RETRY_MAX_S）；连不上的期间由定时检查兜底。
- 授权码不对：不重试（免得反复登录失败），交给定时检查去提醒用户。

Python 3.12 的 imaplib 还没有 idle()（3.14 才加），所以这里用它的底层方法 send() / readline() 自己收发。
这个模块不依赖 Qt：结果通过回调交出去（在后台线程里调用），由 MailService 转成 Qt 信号。
"""
import imaplib
import logging
import re
import socket
import threading

from yunyun.mail.imap_client import TIMEOUT, MailError, _connect, _password_for, mailbox_status, mask_address

log = logging.getLogger(__name__)

FAST_RENEW_S = 15       # 服务器不推送时（比如 QQ 邮箱）：多久重新打开收件箱看一眼
RENEW_S = 5 * 60        # 服务器会推送时：多久"续"一次 IDLE（顺便查一次未读）
RETRY_MIN_S = 15        # 断开后第一次等多久重连……
RETRY_MAX_S = 5 * 60    # ……之后每次翻倍，最长等这么久

# 服务器推送的"邮箱有变化"：新邮件（EXISTS / RECENT）、邮件被删（EXPUNGE）、已读状态等变了（FETCH）
_PUSH = re.compile(rb"^\* \d+ (EXISTS|RECENT|EXPUNGE|FETCH)\b", re.IGNORECASE)


class IdleWatcher:
    """一个邮箱一个：后台线程里一直用 IDLE 等推送；断了就等一会儿重连。

    on_status(account, MailboxStatus)：连上时、每次有变化、每次续 IDLE 时调用（在后台线程里）。
    healthy：此刻是否正在实时接收。是的话，定时检查可以跳过这个邮箱。
    """

    def __init__(self, account, on_status):
        self.account = account
        self.healthy = False
        self._on_status = on_status
        self._stop = threading.Event()
        self._imap = None             # 当前的连接（停止时直接断开它，打断正在进行的等待）
        self._delay = RETRY_MIN_S
        self._thread = threading.Thread(target=self._run, daemon=True, name="mail-idle")

    def start(self):
        self._thread.start()

    def stop(self):
        """停止（换了邮箱、芸芸退出）。不等线程结束：断开连接后它很快会自己退出。"""
        self._stop.set()
        imap = self._imap
        if imap is not None:
            try:
                imap.sock.shutdown(socket.SHUT_RDWR)   # 正在等推送的 readline() 会马上返回
            except OSError:
                pass

    def _run(self):
        last_kind = None
        while not self._stop.is_set():
            try:
                watch(self.account, self._report, self._stop, on_connected=self._connected)
                return   # 正常返回 = 被要求停止
            except MailError as e:
                if self._stop.is_set():
                    return
                if e.kind != last_kind:   # 只在情况变化时记日志（断网时不会每次重连都记一条）
                    log.warning("邮箱 %s 的实时接收断开了（%s）：%s", mask_address(self.account.address),
                                e.kind, e.detail or e)
                    last_kind = e.kind
                if e.kind in ("no_idle", "login", "no_password"):
                    return   # 不重试：服务器不支持 / 授权码有问题，交给定时检查
            except Exception:
                log.exception("邮箱 %s 的实时接收出现意外错误", mask_address(self.account.address))
            finally:
                self.healthy = False
                self._imap = None
            self._stop.wait(self._delay)                 # 等一会儿再重连（被要求停止时立刻醒来）
            self._delay = min(self._delay * 2, RETRY_MAX_S)

    def _connected(self, imap):
        self._imap = imap
        if self._stop.is_set():   # 刚连上就被要求停止了
            self.stop()

    def _report(self, status):
        if not self.healthy:
            log.info("邮箱 %s 开始实时接收新邮件（IMAP IDLE）", mask_address(self.account.address))
        self.healthy = True
        self._delay = RETRY_MIN_S   # 连上了：下次断开时重新从短的等待开始
        self._on_status(self.account, status)


def watch(account, on_status, stop_event, on_connected=None):
    """连着邮箱等推送，直到 stop_event 被设置（正常返回）或出错（抛 MailError）。"""
    with _connect(account, _password_for(account)) as imap:
        if on_connected is not None:
            on_connected(imap)
        if "IDLE" not in _capabilities(imap):
            raise MailError("这个邮箱服务器不支持 IDLE", kind="no_idle")
        on_status(mailbox_status(imap))
        renew_s = FAST_RENEW_S   # 先当作服务器不会推送：经常重新打开收件箱看一眼
        while not stop_event.is_set():
            pushed = idle(imap, renew_s)
            if stop_event.is_set():
                return
            if pushed and renew_s != RENEW_S:
                log.info("邮箱 %s 的服务器会主动推送，改为每 %d 秒检查一次", mask_address(account.address), RENEW_S)
                renew_s = RENEW_S
            # 重新只读打开收件箱：服务器会告诉我们最新的 UIDNEXT（QQ 邮箱只有这样才看得见新邮件）
            imap.select("INBOX", readonly=True)
            on_status(mailbox_status(imap))


def idle(imap, timeout):
    """发 IDLE，等到服务器推送变化或 timeout 秒后结束。返回 True = 服务器说邮箱有变化。"""
    tag = imap._new_tag()
    imap.tagged_commands.pop(tag, None)   # _new_tag 会把编号记进 imaplib 的表里；我们自己收回复，不用它记（不然越积越多）
    imap.send(tag + b" IDLE\r\n")
    changed = False
    while True:                                   # 等服务器回 "+ idling"（之前积压的通知可能先到）
        line = _readline(imap)
        if line.startswith(b"+"):
            break
        if line.startswith(tag):                  # 直接回了 "A5 BAD ..."：其实不支持
            raise MailError("服务器拒绝了 IDLE", kind="no_idle", detail=_text(line))
        changed = changed or bool(_PUSH.match(line))
    if not changed:
        changed = _wait_for_push(imap, timeout)
    imap.send(b"DONE\r\n")
    while True:                                   # 读到 "A5 OK IDLE terminated" 为止
        line = _readline(imap)
        if line.startswith(tag):
            if not line[len(tag):].strip().upper().startswith(b"OK"):
                raise imaplib.IMAP4.error(f"结束 IDLE 失败：{_text(line)}")
            return changed
        changed = changed or bool(_PUSH.match(line))


def _wait_for_push(imap, timeout):
    """一行一行地等服务器的通知，最多等 timeout 秒。"""
    sock = imap.sock
    sock.settimeout(timeout)
    try:
        while True:
            if _PUSH.match(_readline(imap)):
                return True
            # 其他通知（比如服务器的保活消息 "* OK Still here"）：接着等
    except TimeoutError:   # 到时间了，没有新通知
        # Python 的规定：读取超时以后，这个读取对象（makefile 得到的）就不能再用了，换一个新的
        old, imap.file = imap.file, sock.makefile("rb")
        old.close()
        return False
    finally:
        try:
            sock.settimeout(TIMEOUT)
        except OSError:
            pass   # 连接已经被关掉了（比如正在停止）


def _readline(imap):
    line = imap.readline()
    if not line:
        raise imaplib.IMAP4.abort("连接被关闭")
    if line.startswith(b"* BYE"):
        raise imaplib.IMAP4.abort(f"服务器断开了连接：{_text(line)}")
    return line


def _capabilities(imap):
    """登录后服务器声明的功能（有的服务器登录后才说自己支持 IDLE）。"""
    try:
        status, data = imap.capability()
        if status == "OK" and data and data[0]:
            return set(data[0].decode("ascii", errors="replace").upper().split())
    except imaplib.IMAP4.error:
        pass
    return {c.upper() for c in imap.capabilities}


def _text(line):
    return line.decode("utf-8", errors="replace").strip()
