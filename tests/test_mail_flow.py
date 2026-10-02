"""端到端测试：新邮件 → 小红点 → 点击 → 思考 + 转圈 → 卡片逐张出现 → 处理完恢复。

邮箱和 AI 都用假的（AI 每封故意慢 0.15 秒，模拟真实的网络延迟），不联网。
"""
import time

import pytest

from yunyun import ai, i18n
from yunyun.main import connect_mail
from yunyun.mail import service as service_module
from yunyun.mail.imap_client import MailAccount, MailboxStatus
from yunyun.mail.message import MailMessage
from yunyun.mail.tracker import MailTracker
from yunyun.notify.history import MailHistory
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState, StateMachine
from yunyun.pet.window import PetWindow

A = MailAccount("QQ邮箱", "qq", "me@qq.com")


def wait_until(qapp, condition, timeout=5.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


@pytest.fixture
def world(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    i18n._current = "zh"
    box = {"uidnext": 10, "unseen": []}

    monkeypatch.setattr(service_module, "check_unseen", lambda account: MailboxStatus(1, box["uidnext"], list(box["unseen"])))
    monkeypatch.setattr(service_module, "fetch_messages", lambda account, uids: (
        MailMessage(uid, f"标题{uid}", f"发件人{uid}", f"s{uid}@x.com", None, "正文", []) for uid in uids))
    monkeypatch.setattr(service_module.accounts, "load_accounts", lambda: [A])

    def slow_ai(message):
        time.sleep(0.15)
        return ai.Description(f"AI：标题{message.uid}来啦！", "ok")

    mail = service_module.MailService(tracker=MailTracker(tmp_path / "s.json"), process_message=slow_ai)
    mail._load_accounts()
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.move(600, 500)
    pet.show()
    machine = StateMachine()
    machine.state_changed.connect(pet.set_state)
    history = MailHistory(tmp_path / "history.db")
    pet.set_history(history)
    connect_mail(mail, pet, machine, history)

    def poll():
        mail.poll_now()
        assert wait_until(qapp, lambda: not mail._polling)

    poll()                                        # 第一次检查：建立基线
    box.update(uidnext=13, unseen=[10, 11, 12])   # 来了 3 封新邮件
    poll()
    return mail, pet, machine


def test_full_flow(qapp, world):
    mail, pet, machine = world
    assert pet._badge.isVisible() and pet._badge._text == "3"
    assert machine.state is PetState.EXCITED      # 芸芸看得见：激动

    pet._on_badge_clicked()                       # 点小红点
    assert machine.state is PetState.THINKING and pet._badge.loading

    assert wait_until(qapp, lambda: pet._deck.card_count >= 1)
    assert pet._deck.isVisible() and mail.fetching   # 第一张已经出来了，后面的还在处理（处理好一封就先展开一封）

    assert wait_until(qapp, lambda: not mail.fetching)
    deck = pet._deck
    assert [c.bubble for c in deck._cards] == ["AI：标题12来啦！", "AI：标题11来啦！", "AI：标题10来啦！"]  # 最新的在前
    assert machine.state is not PetState.THINKING   # 处理完回到工作 / 待机
    assert not pet._badge.loading and not pet._badge.isVisible()   # 都处理完了：小红点消失
    assert mail.pending_count == 0


def test_close_midway_keeps_rest_for_later(qapp, world):
    mail, pet, machine = world
    pet._on_badge_clicked()
    assert wait_until(qapp, lambda: pet._deck.card_count >= 1)
    pet._deck.close_deck()                        # 第一张出来后马上收起
    assert wait_until(qapp, lambda: not mail.fetching)
    assert mail.pending_count == 3 - pet._deck.card_count   # 没放进卡片堆的，仍然算待处理
    assert mail.pending_count >= 1
    assert pet._badge.isVisible()                 # 小红点还在，下次再点

    time.sleep(0.5)                               # 等过"刚刚收起"的时间
    pet._on_badge_clicked()                       # 再点：只处理剩下的
    assert wait_until(qapp, lambda: not mail.fetching and pet._deck.card_count >= 1)
    assert mail.pending_count == 0


def recent_action(pet):
    return next(a for a in pet._build_menu().actions() if a.text() == "刚才的邮件")


def test_history_records_batch_and_recent_mail_reopens_it(qapp, world):
    mail, pet, machine = world
    assert not recent_action(pet).isEnabled()                  # 还没有历史：灰的
    pet._on_badge_clicked()
    assert wait_until(qapp, lambda: pet._deck.card_count >= 1)
    assert not recent_action(pet).isEnabled()                  # 正在处理：灰的
    assert wait_until(qapp, lambda: not mail.fetching)

    batch = pet.history.latest_batch()
    assert [e.bubble for e in batch] == ["AI：标题12来啦！", "AI：标题11来啦！", "AI：标题10来啦！"]
    assert len({e.batch for e in batch}) == 1                  # 同一批

    pet._deck.close_deck()
    assert recent_action(pet).isEnabled()
    recent_action(pet).trigger()                               # 右键 → 刚才的邮件
    assert pet._deck.isVisible()
    assert [c.bubble for c in pet._deck._cards] == [e.bubble for e in batch]   # 和当时看到的一样
    assert not pet.deck_accepting                              # 这是回看，不是在处理
