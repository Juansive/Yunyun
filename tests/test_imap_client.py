"""IMAP 读取流程的测试：用一个假的 IMAP 服务器代替真的 QQ 邮箱，不联网、不需要授权码。"""
import imaplib

import pytest

from yunyun.mail import imap_client
from yunyun.mail.imap_client import MailAccount, MailError, accounts_from_config, fetch_unread

ACCOUNT = MailAccount(name="QQ邮箱", provider="qq", address="me@qq.com")


def raw_mail(subject):
    return f"From: a@b.com\r\nSubject: {subject}\r\nContent-Type: text/plain\r\n\r\nbody of {subject}".encode()


class FakeIMAP:
    """模仿 imaplib.IMAP4_SSL 的一小部分行为，并记录我们发了哪些命令。"""

    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.commands = []
        self.logged_out = False
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        if password != "right-code":
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Login fail")
        return "OK", [b"LOGIN completed"]

    def select(self, mailbox, readonly=False):
        self.commands.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def uid(self, command, *args):
        self.commands.append(("uid", command) + args)
        if command == "SEARCH":
            return "OK", [b"3 10 7"]  # 服务器返回的 UID 不一定有序
        if command == "FETCH":
            uid = args[0]
            return "OK", [(f"1 (UID {uid} BODY[]<0> {{50}}".encode(), raw_mail(f"mail-{uid}")), b")"]
        raise AssertionError(command)

    def logout(self):
        self.logged_out = True


@pytest.fixture
def fake_server(monkeypatch):
    """把真的 IMAP 连接和凭据管理器都换成假的（monkeypatch 会在测试结束后自动恢复）。"""
    FakeIMAP.instances.clear()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(imap_client.credentials, "get_password", lambda address: "right-code")
    return FakeIMAP


def test_fetch_unread_newest_first_and_read_only(fake_server):
    messages = fetch_unread(ACCOUNT, limit=2)

    assert [m.uid for m in messages] == [10, 7]          # 最新的两封，最新的在前
    assert messages[0].subject == "mail-10"

    imap = fake_server.instances[0]
    assert (imap.host, imap.port) == ("imap.qq.com", 993)
    assert ("select", "INBOX", True) in imap.commands     # 只读打开收件箱
    fetches = [c for c in imap.commands if c[:2] == ("uid", "FETCH")]
    assert all("BODY.PEEK" in c[3] for c in fetches)     # 用 PEEK，不会标记为已读
    assert imap.logged_out                                # 用完自动登出


def test_wrong_password_gives_friendly_error(fake_server, monkeypatch):
    monkeypatch.setattr(imap_client.credentials, "get_password", lambda address: "wrong")
    with pytest.raises(MailError, match="登录失败"):
        fetch_unread(ACCOUNT)


def test_missing_password_tells_user_where_to_fix(fake_server, monkeypatch):
    monkeypatch.setattr(imap_client.credentials, "get_password", lambda address: None)
    with pytest.raises(MailError, match="邮箱账号"):
        fetch_unread(ACCOUNT)


def test_network_error_becomes_mail_error(monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(imaplib, "IMAP4_SSL", broken)
    monkeypatch.setattr(imap_client.credentials, "get_password", lambda address: "x")
    with pytest.raises(MailError, match="连接 imap.qq.com 失败"):
        fetch_unread(ACCOUNT)


def test_accounts_from_config():
    config = {"mail": {"accounts": [{"name": "QQ邮箱", "provider": "QQ", "address": "me@qq.com"}]}}
    assert accounts_from_config(config) == [ACCOUNT]
    with pytest.raises(MailError, match="不支持"):
        accounts_from_config({"mail": {"accounts": [{"provider": "hotmail", "address": "x"}]}})
    assert accounts_from_config({}) == []


def test_error_kinds(fake_server, monkeypatch):
    monkeypatch.setattr(imap_client.credentials, "get_password", lambda address: "wrong")
    with pytest.raises(MailError) as info:
        fetch_unread(ACCOUNT)
    assert info.value.kind == "login"
    assert "AUTHENTICATIONFAILED" in info.value.detail

    monkeypatch.setattr(imap_client.credentials, "get_password", lambda address: None)
    with pytest.raises(MailError) as info:
        fetch_unread(ACCOUNT)
    assert info.value.kind == "no_password"
