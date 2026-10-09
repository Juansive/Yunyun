"""邮件服务的测试：检查 / 读取都用假的函数代替，不联网。"""

from yunyun.mail import service as service_module
from yunyun.mail.imap_client import MailAccount, MailboxStatus, MailError
from yunyun.mail.message import MailMessage
from yunyun.mail.tracker import MailTracker
from helpers import wait_until

A = MailAccount("QQ邮箱", "qq", "me@qq.com")


def make_service(qapp, tmp_path, monkeypatch, mailbox):
    """mailbox：一个字典 {"uidnext": .., "unseen": [..], "error": None}，测试中可以随时修改。"""
    def fake_check(account):
        if mailbox.get("error"):
            raise mailbox["error"]
        return MailboxStatus(1, mailbox["uidnext"], list(mailbox["unseen"]))

    def fake_fetch(account, uids):
        for uid in uids:
            yield MailMessage(uid, f"subject-{uid}", "", "a@b.com", None, "", [])

    monkeypatch.setattr(service_module, "check_unseen", fake_check)
    monkeypatch.setattr(service_module, "fetch_messages", fake_fetch)
    monkeypatch.setattr(service_module.accounts, "load_accounts", lambda: [A])
    s = service_module.MailService(tracker=MailTracker(tmp_path / "s.json"),
                                   process_message=lambda m: f"processed-{m.uid}")
    s._load_accounts()
    events = []
    s.pending_changed.connect(lambda total, newly: events.append((total, newly)))
    return s, events


def poll(qapp, s):
    s.poll_now()
    assert wait_until(qapp, lambda: not s._polling)


def test_poll_counts_only_new_mail(qapp, tmp_path, monkeypatch):
    box = {"uidnext": 10, "unseen": [3, 5]}
    s, events = make_service(qapp, tmp_path, monkeypatch, box)
    poll(qapp, s)
    assert events[-1] == (0, 0)          # 旧的未读不算

    box.update(uidnext=12, unseen=[3, 5, 10, 11])
    poll(qapp, s)
    assert events[-1] == (2, 2)          # 2 封新邮件


def test_fetch_pending_newest_first_and_mark_handled(qapp, tmp_path, monkeypatch):
    box = {"uidnext": 10, "unseen": []}
    s, events = make_service(qapp, tmp_path, monkeypatch, box)
    poll(qapp, s)
    box.update(uidnext=12, unseen=[10, 11])
    poll(qapp, s)

    got = []
    s.message_ready.connect(lambda address, m, result: (got.append((m.uid, result)), s.mark_handled(address, m.uid)))
    assert s.fetch_pending() is True
    assert s.fetch_pending() is False          # 正在处理中：不会重复开始
    assert wait_until(qapp, lambda: not s.fetching)
    assert got == [(11, "processed-11"), (10, "processed-10")]   # 每封都经过了 process_message
    assert s.pending_count == 0 and events[-1] == (0, 0)


def test_dismiss_all(qapp, tmp_path, monkeypatch):
    box = {"uidnext": 10, "unseen": []}
    s, events = make_service(qapp, tmp_path, monkeypatch, box)
    poll(qapp, s)
    box.update(uidnext=13, unseen=[10, 11, 12])
    poll(qapp, s)
    assert s.dismiss_all() == 3
    assert events[-1] == (0, 0)


def test_auth_failure_reported_once(qapp, tmp_path, monkeypatch):
    box = {"uidnext": 10, "unseen": [], "error": MailError("x", kind="login")}
    s, _ = make_service(qapp, tmp_path, monkeypatch, box)
    warned = []
    s.auth_failed.connect(warned.append)
    poll(qapp, s)
    poll(qapp, s)
    assert warned == ["me@qq.com"]       # 每次运行只提醒一次，不刷屏

    box["error"] = MailError("offline", kind="connect")
    poll(qapp, s)
    assert warned == ["me@qq.com"]       # 断网不提醒
