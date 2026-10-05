"""实时接收（IMAP IDLE）的测试：在本机起一个假的 IMAP 服务器，不碰真的邮箱。

假服务器只会最基本的几句 IMAP，测试里可以随时"来一封新邮件"（给正在 IDLE 的连接推送 EXISTS）、
"踢断所有连接"、或者假装"不支持 IDLE"。用的是不加密的 IMAP（把 IMAP4_SSL 换成 IMAP4），其余流程和真的一样。
"""
import imaplib
import socket
import socketserver
import threading
import time

import pytest

from yunyun.mail import idle, imap_client
from yunyun.mail import service as service_module
from yunyun.mail.imap_client import MailAccount, MailError
from yunyun.mail.tracker import MailTracker

A = MailAccount("QQ邮箱", "qq", "me@qq.com")


class FakeIMAPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, idle_supported=True):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.idle_supported = idle_supported
        self.like_qq = False   # True：像 QQ 邮箱一样，IDLE 时不推送，而且每个连接只看得到打开收件箱那一刻的"快照"
        self.uidnext = 10
        self.unseen = [3, 5]
        self.lock = threading.RLock()     # 数据和"往连接里写"都用它保护（推送来自测试线程）
        self.idlers = set()               # 正在 IDLE 的连接
        self.connections = set()
        self.logins = 0
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def port(self):
        return self.server_address[1]

    def new_mail(self):
        """来一封新邮件：正在 IDLE 的连接会马上收到 "* N EXISTS"。"""
        with self.lock:
            uid = self.uidnext
            self.uidnext += 1
            self.unseen.append(uid)
            if not self.like_qq:
                for handler in list(self.idlers):
                    handler.send(f"* {len(self.unseen)} EXISTS")
        return uid

    def kick_all(self):
        """模拟断网 / 服务器断开连接。"""
        for sock in list(self.connections):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop(self):
        self.kick_all()
        self.shutdown()
        self.server_close()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        srv.connections.add(self.request)
        idling_tag = None
        snapshot = None   # like_qq 时：打开收件箱那一刻的未读列表
        try:
            self.send("* OK fake IMAP ready")
            for raw in self.rfile:
                line = raw.decode().rstrip("\r\n")
                if idling_tag:                         # IDLE 中：只等 DONE
                    if line.upper() == "DONE":
                        with srv.lock:
                            srv.idlers.discard(self)
                        self.send(f"{idling_tag} OK IDLE terminated")
                        idling_tag = None
                    continue
                tag, _, rest = line.partition(" ")
                command, _, args = rest.partition(" ")
                command = command.upper()
                if command == "CAPABILITY":
                    self.send("* CAPABILITY IMAP4rev1" + (" IDLE" if srv.idle_supported else ""))
                    self.send(f"{tag} OK done")
                elif command == "LOGIN":
                    srv.logins += 1
                    self.send(f"{tag} OK logged in")
                elif command in ("SELECT", "EXAMINE"):
                    with srv.lock:
                        count, uidnext = len(srv.unseen), srv.uidnext
                        snapshot = list(srv.unseen) if srv.like_qq else None
                    for text in (f"* {count} EXISTS", "* OK [UIDVALIDITY 1] ok", f"* OK [UIDNEXT {uidnext}] ok",
                                 f"{tag} OK [READ-ONLY] done"):
                        self.send(text)
                elif command == "UID" and args.upper().startswith("SEARCH"):
                    with srv.lock:
                        uids = " ".join(map(str, snapshot if snapshot is not None else srv.unseen))
                    self.send(f"* SEARCH {uids}".rstrip())
                    self.send(f"{tag} OK done")
                elif command == "IDLE" and srv.idle_supported:
                    with srv.lock:
                        self.send("+ idling")
                        srv.idlers.add(self)
                    idling_tag = tag
                elif command == "LOGOUT":
                    self.send("* BYE bye")
                    self.send(f"{tag} OK done")
                    return
                else:
                    self.send(f"{tag} BAD unknown command")
        except OSError:
            pass
        finally:
            with srv.lock:
                srv.idlers.discard(self)
            srv.connections.discard(self.request)

    def send(self, text):
        with self.server.lock:
            self.wfile.write(text.encode() + b"\r\n")


@pytest.fixture
def server(monkeypatch, fake_store):
    srv = FakeIMAPServer()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", imaplib.IMAP4)                     # 测试用不加密的连接
    monkeypatch.setitem(imap_client.PROVIDERS, "qq", ("127.0.0.1", srv.port))
    monkeypatch.setattr(idle, "RETRY_MIN_S", 0.1)
    fake_store[A.address] = "auth-code"
    yield srv
    srv.stop()


def wait_until(condition, timeout=5.0, qapp=None):
    end = time.time() + timeout
    while time.time() < end:
        if qapp is not None:
            qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def start_watcher():
    statuses = []
    watcher = idle.IdleWatcher(A, lambda account, status: statuses.append(status))
    watcher.start()
    return watcher, statuses


def test_new_mail_is_noticed_within_a_second(server):
    watcher, statuses = start_watcher()
    try:
        assert wait_until(lambda: statuses and watcher.healthy)           # 连上就先报一次当前情况
        assert statuses[0].unseen == [3, 5]
        uid = server.new_mail()
        pushed_at = time.time()
        assert wait_until(lambda: uid in statuses[-1].unseen)
        assert time.time() - pushed_at < 1.0                               # 几乎立刻（原来最慢要 2 分钟）
        assert statuses[-1].uidnext == uid + 1
    finally:
        watcher.stop()
    assert wait_until(lambda: not watcher._thread.is_alive(), 2)          # 停止后线程很快结束
    assert wait_until(lambda: not server.connections, 2)                  # 连接也断开了


def test_renews_idle_and_rechecks_without_push(server, monkeypatch):
    monkeypatch.setattr(idle, "FAST_RENEW_S", 0.2)                         # 很快就"续"一次
    watcher, statuses = start_watcher()
    try:
        assert wait_until(lambda: len(statuses) >= 4)                      # 没有推送也会定期查一次
        server.unseen.remove(3)                                            # 在手机上读掉了一封（服务器没推送）
        assert wait_until(lambda: statuses[-1].unseen == [5])              # 下次续的时候就发现了
        assert server.logins == 1                                          # 一直是同一个连接，没有反复登录
    finally:
        watcher.stop()


def test_server_that_never_pushes_like_qq(server, monkeypatch):
    """QQ 邮箱：IDLE 时不推送，连接只看得到快照。靠经常重新打开收件箱，新邮件也能很快发现。"""
    server.like_qq = True
    monkeypatch.setattr(idle, "FAST_RENEW_S", 0.3)
    watcher, statuses = start_watcher()
    try:
        assert wait_until(lambda: watcher.healthy)
        uid = server.new_mail()                                            # 服务器不会推送这封
        sent_at = time.time()
        assert wait_until(lambda: uid in statuses[-1].unseen)
        assert time.time() - sent_at < 1.5                                 # 下次重新打开收件箱时就看见了
        assert server.logins == 1                                          # 不需要重新登录
    finally:
        watcher.stop()


def test_server_that_pushes_is_checked_less_often(server, monkeypatch):
    monkeypatch.setattr(idle, "FAST_RENEW_S", 0.2)
    monkeypatch.setattr(idle, "RENEW_S", 60)
    watcher, statuses = start_watcher()
    try:
        assert wait_until(lambda: watcher.healthy)
        uid = server.new_mail()                                            # 服务器推送了：说明它会推送
        assert wait_until(lambda: uid in statuses[-1].unseen)
        count = len(statuses)
        time.sleep(1.0)                                                    # 远超过 FAST_RENEW_S
        assert len(statuses) == count                                      # 已经改成很久才看一次，不再频繁检查
    finally:
        watcher.stop()


def test_reconnects_after_the_connection_drops(server):
    watcher, statuses = start_watcher()
    try:
        assert wait_until(lambda: watcher.healthy)
        server.kick_all()                                                  # 断网 / 服务器断开
        assert wait_until(lambda: server.logins == 2 and watcher.healthy)  # 很快自动重连
        uid = server.new_mail()
        assert wait_until(lambda: uid in statuses[-1].unseen)              # 重连后照样实时收到
    finally:
        watcher.stop()


def test_server_without_idle_falls_back_to_polling(server):
    server.idle_supported = False
    with pytest.raises(MailError) as info:
        idle.watch(A, lambda status: None, threading.Event())
    assert info.value.kind == "no_idle"

    watcher, statuses = start_watcher()
    assert wait_until(lambda: not watcher._thread.is_alive(), 2)          # 不支持就不再尝试
    assert not watcher.healthy                                             # 这个邮箱交给定时检查


def test_service_uses_idle_but_keeps_polling(qapp, server, tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "IDLE_ENABLED", True)              # conftest 默认关掉了
    monkeypatch.setattr(service_module.accounts, "load_accounts", lambda: [A])
    s = service_module.MailService(tracker=MailTracker(tmp_path / "s.json"))
    events = []
    s.pending_changed.connect(lambda total, newly: events.append((total, newly)))
    s._load_accounts()
    s._start_watchers()
    try:
        assert wait_until(lambda: s._watchers[A.address].healthy and events, qapp=qapp)   # 实时接收已经连上
        server.new_mail()
        assert wait_until(lambda: events[-1] == (1, 1), qapp=qapp)         # 推送了：新邮件马上让小红点 +1

        s.poll_now()                                                       # 定时检查照常进行（服务器不一定推送）
        assert s._polling
        assert wait_until(lambda: not s._polling, qapp=qapp)
        assert events[-1] == (1, 0)                                        # 同一封邮件不会被算两次
    finally:
        s.stop()
    assert not s._watchers
