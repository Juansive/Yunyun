"""隐私规则（ai/privacy.py）和 AI 页的"隐私"一块（pet/privacy_box.py）。"""
from PySide6.QtWidgets import QCheckBox

from yunyun import ai, i18n
from yunyun.ai import privacy, settings as ai_settings
from yunyun.ai.privacy import PrivacyRules, normalize_sender
from yunyun.ai.settings import AISettings
from yunyun.mail import accounts
from yunyun.mail.imap_client import MailAccount
from yunyun.mail.message import MailMessage

MAIL = MailMessage(uid=1, subject="工资条", sender_name="人事部", sender_address="hr@company.com",
                   date=None, body="本月工资 12345 元", attachments=[])


def test_normalize_sender():
    assert normalize_sender(" Boss@Company.com ") == "boss@company.com"
    assert normalize_sender("@ICBC.com.cn") == normalize_sender("icbc.com.cn") == "@icbc.com.cn"   # 没写 @ 也当域名
    assert normalize_sender("工商银行.中国") == "@工商银行.中国"                                        # 中文域名
    for bad in ("", "  ", "abc", "a@b", "a b@c.com", "a@b@c.com", "<a@b.com>", "@"):
        assert normalize_sender(bad) is None, bad


def test_is_private():
    rules = PrivacyRules(accounts=["work@163.com"], senders=["boss@company.com", "@icbc.com.cn"])
    assert rules.is_private("Work@163.com", "anyone@x.com")             # 整个邮箱（不分大小写）
    assert rules.is_private("me@qq.com", "BOSS@company.com")            # 完整地址
    assert not rules.is_private("me@qq.com", "hr@company.com")          # 同公司别人不算
    assert rules.is_private("me@qq.com", "notice@icbc.com.cn")          # 域名
    assert rules.is_private("me@qq.com", "x@mail.icbc.com.cn")          # 子域名也算
    assert not rules.is_private("me@qq.com", "x@fakeicbc.com.cn")       # 只是结尾一样不算
    assert not rules.is_private("me@qq.com", "")
    assert not PrivacyRules().is_private("me@qq.com", "boss@company.com")


def test_rules_roundtrip_and_other_settings_keep_them(fake_store):
    assert privacy.load_rules() == PrivacyRules()                       # 还没设过
    rules = PrivacyRules(accounts=["work@163.com"], senders=["@icbc.com.cn"])
    privacy.save_rules(rules)
    accounts.save_account(MailAccount("QQ邮箱", "qq", "me@qq.com"), "code")   # 加邮箱、存 AI 设置都不能把规则冲掉
    ai_settings.save_settings(AISettings("ollama", "qwen3:1.7b", ""))
    assert privacy.load_rules() == rules


def test_describe_private_never_reaches_ai(monkeypatch):
    i18n._current = "zh"
    called = []
    monkeypatch.setattr(ai_settings, "load_settings", lambda: AISettings("ollama", "qwen3:1.7b", ""))
    monkeypatch.setattr(ai, "summarize", lambda *args: called.append(args) or "AI 写的")
    privacy.save_rules(PrivacyRules(senders=["@company.com"]))

    d = ai.describe(MAIL, "zh", "me@qq.com")
    assert d.status == "private" and d.bubble == "🔒 人事部 发来了「工资条」" and called == []   # 本地模型也不给
    privacy.save_rules(PrivacyRules())
    assert ai.describe(MAIL, "zh", "me@qq.com").status == "ok" and len(called) == 1


def checkboxes(box):
    return [b for b in box.findChildren(QCheckBox) if not b.isHidden()]


def test_privacy_box(qapp, fake_store):
    from yunyun.pet.privacy_box import PrivacyBox
    i18n._current = "zh"
    accounts.save_account(MailAccount("QQ邮箱", "qq", "me@qq.com"), "code")
    box = PrivacyBox()
    [qq] = checkboxes(box)
    assert "me@qq.com" in qq.text() and not qq.isChecked()
    qq.setChecked(True)                                                 # 勾上就存，不用点保存
    assert privacy.load_rules().accounts == ["me@qq.com"]

    box._input.setText("abc")
    box._add()
    assert not box._status.isHidden() and privacy.load_rules().senders == []   # 填错了：说明怎么填，不存
    for text in ("ICBC.com.cn", "@icbc.com.cn", "boss@company.com"):
        box._input.setText(text)
        box._add()
    assert box._status.isHidden() and box._input.text() == ""
    assert privacy.load_rules().senders == ["@icbc.com.cn", "boss@company.com"]   # 重复的不加第二次
    assert [box._senders.item(i).text() for i in range(box._senders.count())] == ["@icbc.com.cn", "boss@company.com"]

    assert not box._remove.isEnabled()
    box._senders.setCurrentRow(0)
    assert box._remove.isEnabled()
    box._remove_selected()
    assert privacy.load_rules().senders == ["boss@company.com"]

    accounts.save_account(MailAccount("163邮箱", "163", "work@163.com"), "code")   # 邮箱页加了一个邮箱
    box.refresh()
    qapp.processEvents()
    assert [b.isChecked() for b in checkboxes(box)] == [True, False]
    assert box._senders.count() == 1


def test_privacy_box_follows_mail_page(qapp, fake_store):
    """设置窗口里：邮箱页增删邮箱，AI 页的隐私规则跟着列出。"""
    from yunyun.pet.sprites import load_sprites
    from yunyun.pet.window import PetWindow
    pet = PetWindow(load_sprites())
    pet.open_settings("ai")
    window = pet.settings_window
    box = window.ai_page.privacy_box
    assert checkboxes(box) == []
    accounts.save_account(MailAccount("QQ邮箱", "qq", "me@qq.com"), "code")
    window.mail_page.accounts_changed.emit()
    qapp.processEvents()                       # 窗口开着时，新加的控件要等下一轮事件才显示
    assert len(checkboxes(box)) == 1
    window.close()
