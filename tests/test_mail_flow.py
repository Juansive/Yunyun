"""端到端测试：新邮件 → 小红点 → 点击 → 思考 + 转圈 → 卡片逐张出现 → 处理完恢复。

邮箱和 AI 都用假的（AI 每封故意慢 0.15 秒，模拟真实的网络延迟），不联网。
"""
import time

import pytest

from datetime import datetime

from yunyun import ai, i18n
from yunyun.ai.mail_agent import Inbox, Suggestion
from yunyun.main import connect_mail
from yunyun.mail import service as service_module
from yunyun.mail.imap_client import MailAccount, MailboxStatus
from yunyun.mail.message import MailMessage
from yunyun.mail.tracker import MailTracker
from yunyun.notify.history import MailHistory
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import PetState, StateMachine
from yunyun.pet.window import PetWindow
from helpers import wait_until

A = MailAccount("QQ邮箱", "qq", "me@qq.com")


@pytest.fixture
def world(qapp, tmp_path, monkeypatch):
    i18n._current = "zh"
    box = {"uidnext": 10, "unseen": []}

    monkeypatch.setattr(service_module, "check_unseen", lambda account: MailboxStatus(1, box["uidnext"], list(box["unseen"])))
    monkeypatch.setattr(service_module, "fetch_messages", lambda account, uids: (
        MailMessage(uid, f"标题{uid}", f"发件人{uid}", f"s{uid}@x.com", None, "正文", []) for uid in uids))
    monkeypatch.setattr(service_module.accounts, "load_accounts", lambda: [A])

    def slow_ai(address, message):
        time.sleep(0.15)
        if message.uid == 11:   # 邮件 agent 给了一条提醒建议
            return ai.Description(f"AI：标题{message.uid}来啦！", "ok",
                                  suggestions=[Suggestion("add", "组会", datetime(2026, 10, 22, 9, 0))])
        return ai.Description(f"AI：标题{message.uid}来啦！", "ok", suspicious=message.uid == 12)

    mail = service_module.MailService(tracker=MailTracker(tmp_path / "s.json"), process_message=slow_ai)
    mail._load_accounts()
    pet = PetWindow(load_sprites())
    monkeypatch.setattr(pet, "_check_covered", lambda: None)
    pet.move(600, 500)
    pet.show()
    machine = StateMachine()
    machine.state_changed.connect(pet.set_state)
    history = MailHistory(tmp_path / "history.db")
    pet.history = history
    mail.inbox = Inbox()   # 测试里要看邮件 agent 的收件箱
    connect_mail(mail, pet, machine, history, inbox=mail.inbox)

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


def test_private_mailbox_never_reaches_ai(qapp, world, monkeypatch):
    """隐私规则：这个邮箱的邮件不交给 AI——只显示发件人 + 标题（带 🔒），卡片堆底部也不说"AI 没用上"。"""
    from yunyun.ai import privacy, settings as ai_settings
    from yunyun.ai.settings import AISettings
    mail, pet, machine = world
    called = []
    monkeypatch.setattr(ai_settings, "load_settings", lambda: AISettings("ollama", "qwen3:1.7b", ""))
    monkeypatch.setattr(ai, "summarize", lambda *args: called.append(args) or "AI 写的")
    mail._process = lambda address, message: ai.describe(message, "zh", address)   # 和 main() 里一样
    privacy.save_rules(privacy.PrivacyRules(accounts=["me@qq.com"]))

    pet._on_badge_clicked()
    assert wait_until(qapp, lambda: not mail.fetching)
    assert [c.bubble for c in pet._deck._cards] == [f"🔒 发件人{uid} 发来了「标题{uid}」" for uid in (12, 11, 10)]
    assert called == [] and pet._deck._note == ""


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


def test_cards_show_which_mailbox_with_two_or_more(qapp, world):
    """两个以上邮箱：卡片标出是哪个邮箱收的，历史记下、"刚才的邮件"和历史页也一样；只有一个邮箱时不标。"""
    from yunyun.pet.history_page import HistoryPage
    mail, pet, machine = world
    msg = MailMessage(20, "论文", "王老师", "wang@univ.edu.cn", None, "正文", [])
    say = ai.Description("王老师说论文要补充", "ok")

    pet.begin_mail_batch()
    mail.message_ready.emit("me@qq.com", msg, say)              # world 里只有一个 QQ 邮箱
    assert pet._deck._cards[-1].mailbox == ""

    mail._accounts = [A, MailAccount("163邮箱", "163", "work@163.com")]
    pet.begin_mail_batch()
    mail.message_ready.emit("work@163.com", msg, say)
    card = pet._deck._cards[-1]
    assert (card.mailbox, card.mailbox_address) == ("163邮箱", "work@163.com")
    assert "收件邮箱：work@163.com" in pet._deck._detail_lines(card)
    assert pet.history.latest_batch()[-1].account == "work@163.com"

    pet._deck.close_deck()
    pet.show_recent_mail()
    assert pet._deck._cards[-1].mailbox == "163邮箱"            # 回看时也有

    page = HistoryPage(pet.history, pet.mail_accounts)
    texts = [page._tree.topLevelItem(0).child(i).text(0) for i in range(page._tree.topLevelItem(0).childCount())]
    assert texts[0].endswith("163邮箱 · 王老师说论文要补充")
    assert texts[1].endswith("QQ邮箱 · 王老师说论文要补充")   # 按现在的邮箱来标：那时只有一个，现在两个了也标上


def test_quiet_hours_only_count_new_mail(qapp, tmp_path, monkeypatch):
    """免打扰时来了新邮件：芸芸不激动、小红点不去角落，只在身上安静地计数。"""
    from datetime import time as dtime
    from yunyun.notify.quiet import QuietHours
    from yunyun.pet import winapi
    box = {"uidnext": 10, "unseen": []}
    monkeypatch.setattr(service_module, "check_unseen", lambda account: MailboxStatus(1, box["uidnext"], list(box["unseen"])))
    monkeypatch.setattr(service_module.accounts, "load_accounts", lambda: [A])
    monkeypatch.setattr(winapi, "covered_ratio", lambda *a, **k: 1.0)   # 芸芸被别的窗口挡住了
    monkeypatch.setattr(winapi, "is_user_busy", lambda: False)

    mail = service_module.MailService(tracker=MailTracker(tmp_path / "s.json"))
    mail._load_accounts()
    pet = PetWindow(load_sprites())
    pet.move(600, 500)
    pet.show()
    machine = StateMachine()
    machine.state_changed.connect(pet.set_state)
    quiet = QuietHours(clock=lambda: dtime(23, 30))
    quiet.set_enabled(True)
    machine.set_quiet(quiet.active)
    connect_mail(mail, pet, machine, quiet=quiet)

    mail.poll_now()
    assert wait_until(qapp, lambda: not mail._polling)
    box.update(uidnext=12, unseen=[10, 11])
    mail.poll_now()
    assert wait_until(qapp, lambda: not mail._polling)

    assert machine.state is PetState.SLEEPING           # 继续睡，不激动
    assert pet._badge._text == "2"                      # 数字照常 +2
    assert not pet._corner_badge.isVisible()            # 不跳到任务栏角落


def test_cards_carry_agent_suggestions_and_history_feeds_its_inbox(qapp, world):
    mail, pet, machine = world
    pet._on_badge_clicked()
    assert wait_until(qapp, lambda: not mail.fetching)
    cards = {c.bubble: c for c in pet._deck._cards}
    assert [s.text for s in cards["AI：标题11来啦！"].suggestions] == ["组会"]
    assert cards["AI：标题12来啦！"].suspicious and not cards["AI：标题10来啦！"].suspicious
    assert sorted(m.subject for m in mail.inbox.snapshot()) == ["标题10", "标题11", "标题12"]
