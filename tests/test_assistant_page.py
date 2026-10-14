"""设置窗口的"助手"页（pet/assistant_page.py）。"测试连接"换成假的，不起小进程、不联网。"""
import pytest
from PySide6.QtCore import QObject, QTimer, Signal

from yunyun import config as cfg
from yunyun import i18n
from yunyun.assistant import protocol
from yunyun.assistant import settings as assistant_settings
from yunyun.assistant.settings import AssistantSettings
from yunyun.pet.assistant_page import AssistantSettingsPage


class FakeCheck(QObject):
    """代替 KeyCheck：记下被要求测什么，然后（在下一轮事件循环里）给出预先设定的结果（和余额）。"""
    done = Signal(object)
    balance = Signal(object)
    requests = []
    result = None
    balance_result = None   # 查得到的余额（None = 不发 balance 信号）
    delay_ms = 0
    finished = []           # 真的给出了结果的

    def __init__(self, provider, model, key, what="check"):
        super().__init__()
        self.what = what
        FakeCheck.requests.append((provider, model, key) if what == "check" else (what, provider, key))
        # 定时器挂在自己下面：和真的 KeyCheck（QProcess 挂在它下面）一样，被删掉就不会再给结果
        self._timer = QTimer(self, singleShot=True, interval=FakeCheck.delay_ms)
        self._timer.timeout.connect(self._finish)

    def start(self):
        self._timer.start()

    def _finish(self):
        FakeCheck.finished.append(self.what)
        if FakeCheck.balance_result is not None:
            self.balance.emit(FakeCheck.balance_result)
        self.done.emit(FakeCheck.result)


@pytest.fixture
def keys(monkeypatch):
    """用字典代替凭据管理器里的助手 Key。"""
    store = {}
    monkeypatch.setattr(assistant_settings, "get_api_key", lambda provider: store.get(provider))
    monkeypatch.setattr(assistant_settings, "set_api_key", lambda provider, key: store.__setitem__(provider, key))
    FakeCheck.requests, FakeCheck.result, FakeCheck.balance_result = [], None, None
    FakeCheck.delay_ms, FakeCheck.finished = 0, []
    return store


def make_page():
    return AssistantSettingsPage(make_check=FakeCheck)


def wait_until(qapp, condition):
    for _ in range(200):
        if condition():
            return True
        qapp.processEvents()
    return condition()


def test_defaults(qapp, keys):
    page = make_page()
    assert page._provider.currentData() == "anthropic"
    assert page.chosen_model == "claude-haiku-4-5"                  # 用户定的默认：最便宜的 Haiku
    assert "$1" in page._model.currentText()                         # 下拉框里写着价格
    assert page._budget.value() == 0.1 and page._conversation_budget.value() == 0.5
    assert page._budget_warning.isHidden()                           # 默认值不用提醒
    assert page._key.text() == "" and page._key.placeholderText() == "粘贴你的 API Key"
    assert not page._form.isRowVisible(page._custom_model)


def test_saved_key_is_never_shown(qapp, keys):
    keys["anthropic"] = "sk-ant-secret"
    page = make_page()
    assert page._key.text() == ""                                    # Key 不进界面
    assert "已保存" in page._key.placeholderText()


def test_saved_custom_model_shows_in_the_text_box(qapp, keys):
    assistant_settings.save_settings(AssistantSettings("anthropic", "claude-haiku-9", 0.5))
    page = make_page()
    assert page._model.currentData() is None                         # 选中了"自己填写"
    assert page._form.isRowVisible(page._custom_model)
    assert page.chosen_model == "claude-haiku-9"
    assert page._budget.value() == 0.5


def test_test_and_save_success(qapp, keys):
    page = make_page()
    saved = []
    page.settings_saved.connect(lambda: saved.append(True))
    page._model.setCurrentIndex(page._model.findData("claude-sonnet-5-5"))
    page._key.setText("  sk-ant-new  ")
    page._budget.setValue(0.25)
    page._conversation_budget.setValue(1.2)
    page._test_and_save()
    assert not page._save_button.isEnabled()                         # 测试期间不能再点
    assert wait_until(qapp, lambda: saved)
    assert FakeCheck.requests == [("anthropic", "claude-sonnet-5-5", "sk-ant-new")]
    assert keys == {"anthropic": "sk-ant-new"}
    assert assistant_settings.load_settings() == AssistantSettings("anthropic", "claude-sonnet-5-5", 0.25, 1.2)
    assert page._key.text() == "" and "已保存" in page._key.placeholderText()
    assert page._save_button.isEnabled() and "已保存" in page._status.text()


def test_compact_limit_choice(qapp, keys, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    page = make_page()
    assert page._compact.currentData() == 40_000 and page._compact.currentText() == "超过 4万 token（推荐）"
    assert page._compact.itemText(0) == "不整理"
    page._compact.setCurrentIndex(page._compact.findData(0))
    page._key.setText("sk-ant-new")
    saved = []
    page.settings_saved.connect(lambda: saved.append(True))
    page._test_and_save()
    assert wait_until(qapp, lambda: saved)
    assert assistant_settings.load_settings().compact_tokens == 0


def test_screenshot_ask_checkbox_is_saved(qapp, keys):
    page = make_page()
    assert not page._screenshot_ask.isChecked()                       # 默认不问
    page._screenshot_ask.setChecked(True)
    page._key.setText("sk-ant-new")
    saved = []
    page.settings_saved.connect(lambda: saved.append(True))
    page._test_and_save()
    assert wait_until(qapp, lambda: saved)
    assert assistant_settings.load_settings().screenshot_ask


def test_personality_is_saved(qapp, keys, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    page = make_page()
    assert page._personality.currentData() == "default" and page._personality_text.toPlainText() == ""
    assert "补充几句" in page._personality_text.placeholderText() and "300" in page._personality_hint.text()
    page._personality.setCurrentIndex(page._personality.findData("custom"))
    assert "温柔的大姐姐" in page._personality_text.placeholderText() and "替换" in page._personality_hint.text()
    page._personality_text.setPlainText("长" * 400)
    assert page._personality_text.toPlainText() == "长" * 300          # 多出来的打不进去
    page._user_name.setText("主人")
    page._key.setText("sk-ant-new")
    saved = []
    page.settings_saved.connect(lambda: saved.append(True))
    page._test_and_save()
    assert wait_until(qapp, lambda: saved)
    settings = assistant_settings.load_settings()
    assert (settings.personality, settings.personality_text, settings.user_name) == ("custom", "长" * 300, "主人")
    again = make_page()                                                  # 再打开设置：填好上次的
    assert again._personality.currentData() == "custom" and again._personality_text.toPlainText() == "长" * 300
    assert again._user_name.text() == "主人"


def test_unusual_saved_compact_limit_is_listed(qapp, keys):
    assistant_settings.save_settings(AssistantSettings(compact_tokens=50_000))   # 改过配置文件
    page = make_page()
    assert page._compact.currentData() == 50_000


def test_empty_key_uses_the_saved_one(qapp, keys):
    keys["anthropic"] = "sk-ant-old"
    page = make_page()
    page._test_and_save()
    assert wait_until(qapp, lambda: "已保存" in page._status.text())
    assert FakeCheck.requests[0][2] == "sk-ant-old"
    assert keys == {"anthropic": "sk-ant-old"}                       # 没粘新的：原来的不动


def test_no_key_at_all(qapp, keys):
    page = make_page()
    page._test_and_save()
    assert FakeCheck.requests == []                                  # 根本不去测
    assert page._status.text() == "请先粘贴 API Key。"


def test_custom_model_must_be_filled(qapp, keys):
    page = make_page()
    page._model.setCurrentIndex(page._model.count() - 1)             # "自己填写模型名…"
    page._key.setText("sk-ant-x")
    page._test_and_save()
    assert FakeCheck.requests == [] and page._status.text() == "请填写模型名。"


@pytest.mark.parametrize("kind", ["auth", "model", "network", "worker"])
def test_failure_saves_nothing(qapp, keys, kind):
    FakeCheck.result = protocol.Error(kind, "服务器说的话")
    page = make_page()
    page._key.setText("sk-ant-wrong")
    page._test_and_save()
    assert wait_until(qapp, lambda: page._save_button.isEnabled())
    assert keys == {}                                                # Key 不对就不存
    assert not cfg.CONFIG_PATH.exists()
    assert "服务器说的话" in page._status.text()
    assert page._key.text() == "sk-ant-wrong"                        # 留着让用户改


def test_settings_window_has_the_assistant_page(qapp, keys):
    """设置窗口里有"助手"页，排在"AI"后面。"""
    from yunyun.pet import settings_window
    assert settings_window.PAGES.index("assistant") == settings_window.PAGES.index("ai") + 1


def test_low_budget_warnings(qapp, keys):
    page = make_page()
    page._budget.setValue(0.01)                                      # 每问一次就要 1 美分左右
    assert not page._budget_warning.isHidden()
    assert page._budget_warning.text() == i18n.tr("assistant_budget_low")
    page._budget.setValue(0.3)
    page._conversation_budget.setValue(0.2)                          # 每段对话比每次回答还低
    assert page._budget_warning.text() == i18n.tr("assistant_budget_conversation_low")
    page._conversation_budget.setValue(1.0)
    assert page._budget_warning.isHidden()



def test_spending_and_balance_link(qapp, keys):
    from yunyun.assistant import ledger
    ledger.record(0.0123)
    page = make_page()
    text = page._spent.text()
    assert "$0.012" in text and "platform.claude.com/settings/billing" in text
    ledger.record(1.0)
    page.show()                                      # 每次显示这一页都刷新
    assert "$1.01" in page._spent.text()
    assert FakeCheck.requests == []                  # Anthropic 没有查余额的接口：不去查
    page.close()


def choose(page, provider):
    page._provider.setCurrentIndex(page._provider.findData(provider))


def test_deepseek_balance_shows_when_the_page_is_shown(qapp, keys):
    keys["deepseek"] = "sk-ds"
    FakeCheck.balance_result = {"available": True, "items": [{"currency": "CNY", "total": "110.00"}]}
    page = make_page()
    choose(page, "deepseek")                         # 换到能查余额的服务商：马上查
    assert wait_until(qapp, lambda: "¥110.00" in page._spent.text())
    assert FakeCheck.requests == [("balance", "deepseek", "sk-ds")]
    assert "platform.deepseek.com/usage" in page._spent.text()
    choose(page, "anthropic")                        # 别家不显示 DeepSeek 的余额
    assert "¥" not in page._spent.text() and "platform.claude.com" in page._spent.text()
    page.close()


def test_deepseek_balance_not_enough_and_no_key(qapp, keys):
    page = make_page()
    choose(page, "deepseek")
    assert FakeCheck.requests == []                  # 还没存 Key：不查
    keys["deepseek"] = "sk-ds"
    FakeCheck.balance_result = {"available": False, "items": [{"currency": "USD", "total": "0.37"}]}
    page.show()
    assert wait_until(qapp, lambda: "$0.37" in page._spent.text())
    assert "不够用了" in page._spent.text()
    page.close()


def test_balance_reply_after_the_page_is_gone(qapp, keys):
    """查余额时设置窗口就关了、这一页删掉了：结果不能再回到删掉的页面上（查询挂在页面下面，一起删掉）。"""
    import time

    import shiboken6
    keys["deepseek"] = "sk-ds"
    FakeCheck.balance_result = {"available": True, "items": [{"currency": "CNY", "total": "1.00"}]}
    FakeCheck.delay_ms = 100
    page = make_page()
    choose(page, "deepseek")
    gone = []
    page._balance_check.destroyed.connect(lambda: gone.append(True))
    shiboken6.delete(page)                           # 马上删掉（deleteLater 要等事件循环）
    end = time.time() + 0.3
    while time.time() < end:                         # 等过了假查询给结果的时间
        qapp.processEvents()
    assert gone and FakeCheck.finished == []         # 查询跟着页面删掉了，没有再回调


def test_test_and_save_shows_the_balance_it_found(qapp, keys):
    page = make_page()
    choose(page, "deepseek")
    page._key.setText("sk-ds-new")
    FakeCheck.balance_result = {"available": True, "items": [{"currency": "CNY", "total": "5.20"}]}
    page._test_and_save()
    assert wait_until(qapp, lambda: "¥5.20" in page._spent.text())
    assert FakeCheck.requests == [("deepseek", "deepseek-flash", "sk-ds-new")]
    assert keys == {"deepseek": "sk-ds-new"}


def test_balance_text():
    from yunyun.pet.assistant_page import balance_text
    assert balance_text({"items": [{"currency": "CNY", "total": "1.5"}, {"currency": "USD", "total": "2"}]}) == "¥1.5 + $2"
    assert balance_text({"items": [{"currency": "EUR", "total": "3"}]}) == "3 EUR"
    assert balance_text({}) == "—"
