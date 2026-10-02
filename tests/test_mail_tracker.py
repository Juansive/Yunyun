"""小红点计数规则的测试。"""
from yunyun.mail.tracker import MailTracker

A = "me@qq.com"


def test_existing_unread_mails_are_ignored(tmp_path):
    t = MailTracker(tmp_path / "s.json")
    # 第一次看到邮箱：已经有 3 封未读（UID 5、8、9），下一封会是 10
    assert t.update(A, uidvalidity=1, uidnext=10, unseen=[5, 8, 9]) == []
    assert t.total_pending() == 0


def test_new_mail_counts_and_is_announced_once(tmp_path):
    t = MailTracker(tmp_path / "s.json")
    t.update(A, 1, 10, [5, 8, 9])
    assert t.update(A, 1, 12, [5, 8, 9, 10, 11]) == [11, 10]   # 新来 2 封（最新的在前）
    assert t.pending(A) == [11, 10]                           # 最新的在前
    assert t.update(A, 1, 12, [5, 8, 9, 10, 11]) == []        # 再查一次：不重复宣布
    assert t.total_pending() == 2


def test_mail_read_elsewhere_disappears(tmp_path):
    t = MailTracker(tmp_path / "s.json")
    t.update(A, 1, 10, [])
    t.update(A, 1, 12, [10, 11])
    t.update(A, 1, 12, [11])        # 在手机上读掉了 10
    assert t.pending(A) == [11]


def test_mark_handled(tmp_path):
    t = MailTracker(tmp_path / "s.json")
    t.update(A, 1, 10, [])
    t.update(A, 1, 12, [10, 11])
    t.mark_handled(A, 11)
    assert t.pending(A) == [10]


def test_dismiss_all_then_count_from_one(tmp_path):
    t = MailTracker(tmp_path / "s.json")
    t.update(A, 1, 10, [])
    t.update(A, 1, 20, list(range(10, 20)))   # 10 封
    assert t.dismiss_all() == 10
    assert t.total_pending() == 0
    assert t.update(A, 1, 21, list(range(10, 21))) == [20]   # 新来 1 封：从 1 开始数
    assert t.total_pending() == 1


def test_state_survives_restart(tmp_path):
    path = tmp_path / "s.json"
    t = MailTracker(path)
    t.update(A, 1, 10, [])
    t.update(A, 1, 12, [10, 11])
    t.mark_handled(A, 10)

    t2 = MailTracker(path)                      # 模拟重启
    assert t2.update(A, 1, 12, [10, 11]) == []  # 重启后不会把老邮件再宣布一遍
    assert t2.pending(A) == [11]                # 处理过的仍然算处理过


def test_uidvalidity_change_resets(tmp_path):
    t = MailTracker(tmp_path / "s.json")
    t.update(A, 1, 10, [])
    t.update(A, 1, 12, [10, 11])
    t.update(A, 2, 5, [1, 2, 3, 4])   # 服务器重建了编号：旧记录作废，现有的都不提醒
    assert t.total_pending() == 0


def test_forget_and_corrupted_file(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ not json", encoding="utf-8")
    t = MailTracker(path)             # 文件坏了也能启动
    t.update(A, 1, 10, [])
    t.update(A, 1, 11, [10])
    t.forget(A)
    assert t.total_pending() == 0 and t.known_addresses() == []
