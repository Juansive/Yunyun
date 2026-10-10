"""邮箱账号增删，以及设置窗口"邮箱"页的完整流程测试。"""
import time

from PySide6.QtWidgets import QMessageBox

from yunyun import config as cfg, i18n
from yunyun.mail import accounts
from yunyun.mail.imap_client import MailAccount, MailError
from yunyun.pet import mail_page
from helpers import wait_until

QQ = MailAccount(name="QQ邮箱", provider="qq", address="me@qq.com")


def test_save_update_and_remove(fake_store):
    assert accounts.load_accounts() == []          # 没有配置文件 → 空列表

    accounts.save_account(QQ, "code-1")
    assert accounts.load_accounts() == [QQ]
    assert fake_store == {"me@qq.com": "code-1"}
    text = cfg.CONFIG_PATH.read_text(encoding="utf-8")
    assert "me@qq.com" in text and "code-1" not in text  # 授权码绝不写进文件

    # 同一个地址（大小写不同）再保存 = 更新，不会出现两份
    accounts.save_account(MailAccount("QQ邮箱", "qq", "ME@qq.com"), "code-2")
    assert len(accounts.load_accounts()) == 1
    assert fake_store["ME@qq.com"] == "code-2"

    accounts.remove_account("me@qq.com")
    assert accounts.load_accounts() == []


def test_other_config_sections_are_kept(fake_store):
    cfg.CONFIG_PATH.write_text("ai:\n  model: qwen\n", encoding="utf-8")
    accounts.save_account(QQ, "code")
    assert cfg.load_config(cfg.CONFIG_PATH)["ai"] == {"model": "qwen"}


# ---------- 窗口 ----------


def make_dialog(qapp, monkeypatch, login_error=None):
    def fake_check_login(account, password):
        time.sleep(0.05)  # 模拟网络延迟
        if login_error:
            raise login_error
    monkeypatch.setattr(mail_page, "check_login", fake_check_login)
    return mail_page.MailAccountsPage()


def test_dialog_adds_account_after_successful_login(qapp, fake_store, monkeypatch):
    dialog = make_dialog(qapp, monkeypatch)
    changed = []
    dialog.accounts_changed.connect(lambda: changed.append(1))
    assert dialog._list.count() == 1 and not dialog._remove_button.isEnabled()  # 只有"还没有添加"的提示

    dialog._address.setText("me@qq.com")
    dialog._password.setText("abcd efgh ijkl mnop")  # 带空格的也能用
    dialog._save_button.click()
    assert not dialog._save_button.isEnabled()  # 测试期间按钮变灰，防止重复点击

    assert wait_until(qapp, lambda: changed)
    assert fake_store == {"me@qq.com": "abcdefghijklmnop"}
    assert dialog._password.text() == ""        # 保存后清空输入框
    assert "me@qq.com" in dialog._list.item(0).text()
    assert dialog._save_button.isEnabled()


def test_provider_for_address():
    from yunyun.mail.imap_client import provider_for
    assert provider_for(" Me@QQ.com ") == provider_for("x@foxmail.com") == "qq"
    assert provider_for("x@163.com") == "163" and provider_for("x@googlemail.com") == "gmail"
    assert provider_for("x@126.com") is None and provider_for("qq.com") is None and provider_for("x@qq") is None


def test_dialog_guesses_provider_and_links_help(qapp, fake_store, monkeypatch):
    """填好地址自动选类型（手动改了也行）；说明里有能点的链接，跟着类型变。"""
    i18n._current = "zh"
    dialog = make_dialog(qapp, monkeypatch)
    assert "https://mail.qq.com" in dialog._hint.text() and dialog._hint.openExternalLinks()
    dialog._address.textEdited.emit("someone@gmail.com")              # 用户打字（textEdited 只在用户改的时候发）
    assert dialog._provider.currentData() == "gmail"
    assert 'href="https://myaccount.google.com/apppasswords"' in dialog._hint.text()
    dialog._address.textEdited.emit("someone@school.edu.cn")          # 不认识的域名：不动用户选的
    assert dialog._provider.currentData() == "gmail"
    dialog._address.textEdited.emit("someone@163.com")
    assert dialog._provider.currentData() == "163" and "https://mail.163.com" in dialog._hint.text()


def test_dialog_shows_error_and_saves_nothing(qapp, fake_store, monkeypatch):
    error = MailError("x", kind="login", detail="Login fail")
    dialog = make_dialog(qapp, monkeypatch, login_error=error)
    dialog._address.setText("me@qq.com")
    dialog._password.setText("wrong")
    dialog._save_button.click()

    assert wait_until(qapp, lambda: dialog._save_button.isEnabled())
    assert "登录失败" in dialog._status.text() and "Login fail" in dialog._status.text()
    assert fake_store == {} and accounts.load_accounts() == []
    assert dialog._password.text() == "wrong"  # 失败时保留输入，方便修改


def test_dialog_validates_input(qapp, fake_store, monkeypatch):
    dialog = make_dialog(qapp, monkeypatch)
    dialog._address.setText("not-an-email")
    dialog._password.setText("x")
    dialog._save_button.click()
    assert "格式不对" in dialog._status.text()
    dialog._address.setText("me@qq.com")
    dialog._password.setText("")
    dialog._save_button.click()
    assert "授权码" in dialog._status.text()


def test_dialog_removes_account(qapp, fake_store, monkeypatch):
    accounts.save_account(QQ, "code")
    dialog = make_dialog(qapp, monkeypatch)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._list.setCurrentRow(0)
    assert dialog._remove_button.isEnabled()
    dialog._remove_button.click()
    assert accounts.load_accounts() == [] and fake_store == {}
