"""日志的测试：写到文件、邮箱地址打码、断网时不刷屏、不记录敏感内容。"""
import logging
import sys

import pytest

from yunyun import logs
from yunyun.mail.imap_client import MailAccount, MailboxStatus, MailError
from yunyun.mail.service import MailService, mask_address
from yunyun.mail.tracker import MailTracker


@pytest.fixture
def log_file(tmp_path):
    import threading
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    path = logs.setup_logging(tmp_path)
    yield path
    sys.excepthook, threading.excepthook = old_hook, old_thread_hook  # 测试结束后还原
    for h in logging.getLogger("yunyun").handlers:
        h.close()
    logging.getLogger("yunyun").handlers.clear()


def read(path):
    for h in logging.getLogger("yunyun").handlers:
        h.flush()
    return path.read_text(encoding="utf-8")


def test_writes_to_file_and_logs_crashes(log_file):
    assert "芸芸启动" in read(log_file)
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())        # 模拟"没被捕获的异常"
    text = read(log_file)
    assert "未处理的错误" in text and "ValueError: boom" in text


def test_mask_address():
    assert mask_address("me123@qq.com") == "m***@qq.com"
    assert mask_address("weird") == "***"


def test_poll_errors_logged_only_on_change(qapp, tmp_path, log_file):
    s = MailService(tracker=MailTracker(tmp_path / "s.json"))
    a = MailAccount("QQ邮箱", "qq", "secret.person@qq.com")
    offline = MailError("x", kind="connect", detail="timed out")
    for _ in range(5):                          # 断网 5 次（10 分钟）
        s._on_poll_done([(a, None, offline)])
    s._on_poll_done([(a, MailboxStatus(1, 10, []), None)])   # 恢复
    text = read(log_file)
    assert text.count("timed out") == 1         # 只记了一次
    assert "恢复正常" in text
    assert "secret.person" not in text and "s***@qq.com" in text   # 地址打了码
