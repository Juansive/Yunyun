"""助手模式的服务商列表和设置（assistant/providers.py、assistant/settings.py）。"""
from dataclasses import replace

import pytest

from yunyun import config as cfg
from yunyun.assistant import providers
from yunyun.assistant import settings as assistant_settings
from yunyun.assistant.providers import (
    DEFAULT_BUDGET_USD, DEFAULT_PROVIDER, PROVIDERS, Provider, launch_env, usable_tools,
)
from yunyun.assistant.settings import AssistantSettings, load_settings, save_settings


@pytest.fixture
def fake_keyring(monkeypatch):
    """用一个字典代替 Windows 凭据管理器：(服务名, 用户名) → Key。"""
    keys = {}
    keyring = assistant_settings.keyring
    monkeypatch.setattr(keyring, "get_password", lambda service, user: keys.get((service, user)))
    monkeypatch.setattr(keyring, "set_password", lambda service, user, pw: keys.__setitem__((service, user), pw))

    def delete(service, user):
        if (service, user) not in keys:
            raise assistant_settings.PasswordDeleteError("没有")
        del keys[(service, user)]

    monkeypatch.setattr(keyring, "delete_password", delete)
    return keys


# ---------- 服务商 ----------

def test_default_is_claude_with_haiku_first():
    """用户 2026-10-07 定的：默认 Claude 官方 + 最便宜的 Haiku 4.5，每次最多 $0.1。"""
    provider = PROVIDERS[DEFAULT_PROVIDER]
    assert provider.code == "anthropic"
    assert provider.default_model == "claude-haiku-4-5"
    assert DEFAULT_BUDGET_USD == 0.1
    assert set(provider.prices) == set(provider.models)   # 每个可选模型都有价格，设置页才能显示档次


def test_claude_only_needs_the_api_key():
    env = launch_env(PROVIDERS["anthropic"], "sk-ant-test", "claude-haiku-4-5")
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-test"}      # 不改接口地址、不改模型别名


def test_compatible_provider_gets_base_url_token_and_model_aliases():
    """阶段 3 要接的那种服务商（这里造一个假的）：地址、Key 的变量名、模型别名都要设。"""
    other = Provider(code="other", models=("other-chat",), base_url="https://api.other.example/anthropic",
                     key_env="ANTHROPIC_AUTH_TOKEN", alias_models=True, web_search=False)
    env = launch_env(other, "k", "other-chat")
    assert env["ANTHROPIC_BASE_URL"] == "https://api.other.example/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "k" and "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_MODEL"] == env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "other-chat"
    assert usable_tools(other, ["WebSearch", "WebFetch"]) == ["WebFetch"]   # 别家用不了 Anthropic 的网页搜索


def test_claude_keeps_web_search():
    assert usable_tools(PROVIDERS["anthropic"], ["WebSearch", "WebFetch"]) == ["WebSearch", "WebFetch"]


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_deepseek_launch_env(model):
    """DeepSeek（2026-10-08 按官方文档）：Bearer 传 Key；主模型用选的，后台小任务一律 flash（选了 pro 也不跟着贵）。"""
    deepseek = PROVIDERS["deepseek"]
    env = launch_env(deepseek, "sk-test", model)
    assert env == {
        "ANTHROPIC_AUTH_TOKEN": "sk-test",
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
    }
    assert usable_tools(deepseek, ["WebSearch", "WebFetch", "Read"]) == ["WebFetch", "Read"]
    assert deepseek.own_pricing                           # Claude Code 按 Claude 的价格算，对别家不准：芸芸自己算
    assert set(deepseek.prices) == set(deepseek.models)


def test_rates_follow_each_providers_pricing():
    claude = PROVIDERS["anthropic"].rates("claude-haiku-4-5")
    assert claude.cost(1_000_000, 1_000_000, 1_000_000, 1_000_000, searches=1) == pytest.approx(1 + 5 + 1.25 + 0.1 + 0.01)
    flash = PROVIDERS["deepseek"].rates("deepseek-flash")
    assert flash.cost(1_000_000, 1_000_000, 1_000_000, 1_000_000) == pytest.approx(0.3 + 1.2 + 0.3 + 0.006)   # 写缓存不另收
    assert PROVIDERS["anthropic"].rates("my-own-model") is None              # 不知道价格：不估算
    assert PROVIDERS["deepseek"].rates("deepseek-typo") == flash              # DeepSeek 认不出的名字当 flash 用


def test_usage_cost_and_recorded_cost():
    deepseek = PROVIDERS["deepseek"]
    usage = {"deepseek-v4-pro": {"inputTokens": 1_000_000, "outputTokens": 1_000_000, "cacheReadInputTokens": 1_000_000},
             "deepseek-flash": {"inputTokens": 1_000_000}}
    expected = 1.32 + 3.96 + 0.044 + 0.3
    assert providers.usage_cost(deepseek, usage) == pytest.approx(expected)
    assert providers.usage_cost(deepseek, None) is None and providers.usage_cost(deepseek, {}) is None
    assert providers.usage_cost(PROVIDERS["anthropic"], {"my-own-model": {"inputTokens": 1}}) is None
    # 历史面板：DeepSeek 的对话按它的价格算；Claude 的（或混着别的模型）用 Claude Code 记的
    assert providers.recorded_cost(9.9, usage) == pytest.approx(expected)
    assert providers.recorded_cost(0.5, {"claude-haiku-4-5": {"inputTokens": 1}}) == 0.5
    assert providers.recorded_cost(0.5, None) == 0.5


def test_every_provider_has_texts():
    """每家服务商在设置页都要有显示名和申请 Key 的说明（三种语言）。"""
    from yunyun.i18n import TEXTS
    for code in PROVIDERS:
        for key in (f"assistant_provider_{code}", f"assistant_hint_{code}"):
            assert set(TEXTS[key]) >= {"zh", "en", "ja"}, key


# ---------- 设置 ----------

def test_defaults_when_nothing_saved(fake_keyring):
    settings = load_settings()
    assert settings == AssistantSettings()
    assert settings.effective_model == "claude-haiku-4-5"
    assert not assistant_settings.is_ready(settings)          # 还没有 Key


def test_roundtrip_keeps_other_config(fake_keyring):
    cfg.save_config({"accounts": [{"address": "a@qq.com"}], "ai": {"provider": "ollama"}})
    save_settings(AssistantSettings("anthropic", "claude-sonnet-5-5", 0.5))
    assert load_settings() == AssistantSettings("anthropic", "claude-sonnet-5-5", 0.5)
    config = cfg.load_config()
    assert config["accounts"] == [{"address": "a@qq.com"}]     # 邮箱、邮件 AI 的设置都还在
    assert config["ai"] == {"provider": "ollama"}


def test_empty_model_means_provider_default(fake_keyring):
    save_settings(AssistantSettings(model=""))
    assert load_settings().effective_model == "claude-haiku-4-5"
    assert AssistantSettings(model="my-model").effective_model == "my-model"   # 用户手填的模型名照用


@pytest.mark.parametrize("saved, expected", [
    (0.5, 0.5),
    ("0.3", 0.3),
    (0, 0.01),          # 太小：按最小值
    (1000, 10.0),       # 手滑填太大：按最大值
    ("很多", 0.1),       # 看不懂：用默认值
    (None, 0.1),
    (float("nan"), 0.1),
])
def test_budget_is_kept_in_a_sane_range(fake_keyring, saved, expected):
    cfg.save_config({"assistant": {"provider": "anthropic", "budget_usd": saved}})
    assert load_settings().budget_usd == expected


def test_unknown_provider_falls_back_to_defaults(fake_keyring):
    """比如以后的版本加了服务商，又换回旧版本：不出错，用默认值。"""
    cfg.save_config({"assistant": {"provider": "no-such-provider", "model": "x"}})
    assert load_settings() == AssistantSettings()


def test_broken_config_file_falls_back_to_defaults(fake_keyring):
    cfg.CONFIG_PATH.write_text("assistant: [不对", encoding="utf-8")
    assert load_settings() == AssistantSettings()


def test_keys_are_stored_per_provider(fake_keyring, monkeypatch):
    """每家服务商一把 Key，换服务商不会覆盖别家的；用户名就是服务商代号（和 tools/assistant_key.py 一致）。"""
    monkeypatch.setitem(PROVIDERS, "other", replace(PROVIDERS["anthropic"], code="other"))
    assistant_settings.set_api_key("anthropic", "sk-ant-1")
    assistant_settings.set_api_key("other", "k-2")
    assert fake_keyring == {("yunyun-assistant", "anthropic"): "sk-ant-1", ("yunyun-assistant", "other"): "k-2"}
    assert assistant_settings.is_ready(AssistantSettings("anthropic"))
    assistant_settings.delete_api_key("other")
    assistant_settings.delete_api_key("other")                 # 删两次也不出错
    assert not assistant_settings.is_ready(AssistantSettings("other"))
    assert assistant_settings.get_api_key("anthropic") == "sk-ant-1"


@pytest.mark.parametrize("saved, expected", [
    (2, 2.0),
    (0, 0.05),          # 太小：按最小值
    (999, 50.0),        # 太大：按最大值
    (None, 0.5),        # 以前的设置里没有这一项：用默认值
    ("abc", 0.5),
])
def test_conversation_budget_is_kept_in_a_sane_range(fake_keyring, saved, expected):
    cfg.save_config({"assistant": {"provider": "anthropic", "conversation_budget_usd": saved}})
    assert load_settings().conversation_budget_usd == expected


def test_both_budgets_are_saved(fake_keyring):
    save_settings(AssistantSettings(budget_usd=0.2, conversation_budget_usd=1.5))
    loaded = load_settings()
    assert (loaded.budget_usd, loaded.conversation_budget_usd) == (0.2, 1.5)


@pytest.mark.parametrize("saved, expected", [
    (60_000, 60_000),
    (0, 0),             # 0 = 不自动整理
    (-5, 0),
    (3_000, 10_000),    # 太小：按最小值（不然每问一次都要整理）
    (10 ** 9, 200_000),
    (None, 40_000),     # 以前的设置里没有这一项 / 看不懂：用默认值
    ("abc", 40_000),
])
def test_compact_limit_is_kept_in_a_sane_range(fake_keyring, saved, expected):
    cfg.save_config({"assistant": {"provider": "anthropic", "compact_tokens": saved}})
    assert load_settings().compact_tokens == expected


def test_compact_limit_defaults_to_40k_and_is_saved(fake_keyring):
    cfg.save_config({"assistant": {"provider": "anthropic"}})
    assert load_settings().compact_tokens == 40_000
    save_settings(AssistantSettings(compact_tokens=0))
    assert load_settings().compact_tokens == 0


# ---------- 工作文件夹 ----------

def folders(tmp_path, *names):
    paths = []
    for name in names:
        (tmp_path / name).mkdir()
        paths.append(str(tmp_path / name))
    return paths


def test_no_work_folder_by_default(fake_keyring):
    assert load_settings().workspace == "" and assistant_settings.recent_workspaces() == []


def test_work_folder_is_remembered_newest_first(fake_keyring, tmp_path):
    a, b, c = folders(tmp_path, "a", "b", "c")
    for path in (a, b, c, a):
        assistant_settings.set_workspace(path)
    assert load_settings().workspace == a
    assert assistant_settings.recent_workspaces() == [a, c, b]       # 选过的往前挪，不重复


def test_only_the_last_five_folders_are_kept(fake_keyring, tmp_path):
    paths = folders(tmp_path, *"abcdefg")
    for path in paths:
        assistant_settings.set_workspace(path)
    assert assistant_settings.recent_workspaces() == paths[::-1][:5]


def test_no_folder_keeps_the_recent_list(fake_keyring, tmp_path):
    a, = folders(tmp_path, "a")
    assistant_settings.set_workspace(a)
    assistant_settings.set_workspace("")
    assert load_settings().workspace == "" and assistant_settings.recent_workspaces() == [a]


def test_deleted_folder_is_forgotten(fake_keyring, tmp_path):
    a, b = folders(tmp_path, "a", "b")
    assistant_settings.set_workspace(a)
    assistant_settings.set_workspace(b)
    (tmp_path / "b").rmdir()
    assert load_settings().workspace == ""                    # 不在了：当没选（大脑用空文件夹）
    assert assistant_settings.recent_workspaces() == [a]


def test_settings_page_and_folder_button_do_not_overwrite_each_other(fake_keyring, tmp_path):
    a, = folders(tmp_path, "a")
    settings = load_settings()                     # 设置页打开时读的（那时还没选文件夹）
    assistant_settings.set_workspace(a)            # 然后在输入框那边选了文件夹
    save_settings(replace(settings, model="claude-sonnet-5-5"))   # 设置页保存
    loaded = load_settings()
    assert loaded.workspace == a and loaded.model == "claude-sonnet-5-5"
