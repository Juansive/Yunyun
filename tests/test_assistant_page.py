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
    """代替 KeyCheck：记下被要求测什么，然后（在下一轮事件循环里）给出预先设定的结果。"""
    done = Signal(object)
    requests = []
    result = None

    def __init__(self, provider, model, key):
        super().__init__()
        FakeCheck.requests.append((provider, model, key))

    def start(self):
        QTimer.singleShot(0, lambda: self.done.emit(FakeCheck.result))


@pytest.fixture
def keys(monkeypatch):
    """用字典代替凭据管理器里的助手 Key。"""
    store = {}
    monkeypatch.setattr(assistant_settings, "get_api_key", lambda provider: store.get(provider))
    monkeypatch.setattr(assistant_settings, "set_api_key", lambda provider, key: store.__setitem__(provider, key))
    FakeCheck.requests, FakeCheck.result = [], None
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


def test_settings_window_has_the_assistant_page(qapp, keys, monkeypatch):
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

