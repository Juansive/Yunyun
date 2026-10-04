"""AI 模块的测试：不联网、不需要 API Key（网络请求和凭据管理器都用假的代替）。"""
import io
import json
import urllib.error

import pytest

from yunyun import ai, i18n
from yunyun.ai import client, settings as ai_settings, summarizer
from yunyun.ai.client import AIError
from yunyun.ai.providers import PROVIDERS
from yunyun.ai.settings import AISettings
from yunyun.mail.message import MailMessage

MAIL = MailMessage(
    uid=1, subject="电子发票通知", sender_name="中国石化", sender_address="einvoice@sinopec.com",
    date=None, body="您好，您的加油电子发票已开具，金额 326.50 元。\n\n-- \n中国石化客服", attachments=["发票.pdf"],
)
SETTINGS = AISettings("zhipu", "glm-4.7-flash", "test-key")


# ---------- client：HTTP 请求 ----------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(reply=None, http_error=None, network_error=None, captured=None):
    def _open(request, timeout=None):
        if captured is not None:
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["body"] = json.loads(request.data)
        if network_error:
            raise network_error
        if http_error:
            code, payload = http_error
            raise urllib.error.HTTPError(request.full_url, code, "err", {}, io.BytesIO(json.dumps(payload).encode()))
        return FakeResponse(json.dumps(reply).encode())
    return _open


def test_chat_sends_openai_format(monkeypatch):
    captured = {}
    reply = {"choices": [{"message": {"content": "OK"}}]}
    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen(reply, captured=captured))
    answer = client.chat("https://x.com/v4/", "k123", "m1", [{"role": "user", "content": "hi"}],
                         extra={"thinking": {"type": "disabled"}})
    assert answer == "OK"
    assert captured["url"] == "https://x.com/v4/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer k123"
    assert captured["body"]["model"] == "m1" and captured["body"]["thinking"] == {"type": "disabled"}


@pytest.mark.parametrize("code, kind", [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "other")])
def test_chat_http_errors(monkeypatch, code, kind):
    payload = {"error": {"message": "something bad"}}
    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen(http_error=(code, payload)))
    with pytest.raises(AIError) as info:
        client.chat("https://x", "k", "m", [])
    assert info.value.kind == kind and info.value.detail == "something bad"


def test_chat_network_and_bad_response(monkeypatch):
    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen(network_error=urllib.error.URLError("down")))
    with pytest.raises(AIError) as info:
        client.chat("https://x", "k", "m", [])
    assert info.value.kind == "network"

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen(reply={"unexpected": True}))
    with pytest.raises(AIError) as info:
        client.chat("https://x", "k", "m", [])
    assert info.value.kind == "bad_response"


# ---------- summarizer：提示词与解析 ----------

@pytest.mark.parametrize("answer, expected", [
    ('{"bubble": "嘿！你的加油发票来啦！"}', "嘿！你的加油发票来啦！"),
    ('```json\n{"bubble": "张三向你打招呼呢！"}\n```', "张三向你打招呼呢！"),
    ('好的：{"bubble": "李老师来跟你确认开会时间啦！"} 希望有帮助', "李老师来跟你确认开会时间啦！"),
    ("「嘿！快递到啦！」", "嘿！快递到啦！"),   # 没按 JSON 回答，但给了一句话
])
def test_parse_bubble(answer, expected):
    assert summarizer.parse_bubble(answer) == expected


def test_parse_bubble_rejects_empty_and_trims_long():
    with pytest.raises(AIError):
        summarizer.parse_bubble('{"bubble": ""}')
    long = summarizer.parse_bubble(json.dumps({"bubble": "啦" * 200}))
    assert len(long) == summarizer.BUBBLE_LIMIT + 1 and long.endswith("…")


def test_clean_body_removes_history_signature_and_truncates():
    body = "今晚一起吃饭吗？\n> 上次说的那家店\n\n在 2026年10月1日，张三 写道：\n以前的内容"
    assert summarizer.clean_body(body) == "今晚一起吃饭吗？"
    assert summarizer.clean_body("正文\n-- \n签名") == "正文"
    assert summarizer.clean_body("字" * 2000, limit=10) == "字" * 10 + "…"


def test_prompt_contains_mail_and_language(monkeypatch):
    captured = {}

    def fake_ask(provider, api_key, model, messages, **kwargs):
        captured.update(provider=provider, model=model, messages=messages)
        return '{"bubble": "Hey! Your fuel invoice is here!"}'

    monkeypatch.setattr(summarizer, "ask", fake_ask)
    assert summarizer.summarize(MAIL, "en", SETTINGS) == "Hey! Your fuel invoice is here!"
    system, user = captured["messages"][0]["content"], captured["messages"][1]["content"]
    assert "English" in system and "验证码" in system        # 用英文回答；不要写敏感信息
    assert "电子发票通知" in user and "发票.pdf" in user        # 标题、附件名发给了模型
    assert "中国石化客服" not in user                           # 签名被去掉了
    assert captured["provider"].base_url == "https://open.bigmodel.cn/api/paas/v4"
    assert captured["provider"].extra == {"thinking": {"type": "disabled"}}


# ---------- 本地模型（Ollama）：用假的 HTTP，不需要真的装 Ollama ----------

OLLAMA = PROVIDERS["ollama"]


@pytest.fixture
def no_system_proxy(monkeypatch):
    """连本机的 Ollama 不能走系统代理：走了 urllib.request.urlopen（会用代理）就算失败。"""
    def forbidden(*args, **kwargs):
        raise AssertionError("连本机 Ollama 时不应该用系统代理")
    monkeypatch.setattr(client.urllib.request, "urlopen", forbidden)


def test_ollama_uses_native_api_without_key_or_proxy(monkeypatch, no_system_proxy):
    captured = {}
    reply = {"message": {"role": "assistant", "content": '{"bubble": "嘿！发票来啦！"}'}, "done": True}
    monkeypatch.setattr(client._NO_PROXY, "open", fake_urlopen(reply, captured=captured))
    answer = client.ask(OLLAMA, "", "qwen3:1.7b", [{"role": "user", "content": "hi"}], max_tokens=50)
    assert answer == '{"bubble": "嘿！发票来啦！"}'
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert "Authorization" not in captured["headers"]                 # 不需要 Key
    body = captured["body"]
    assert body["stream"] is False and body["think"] is False          # 一次返回；关掉思考模式
    assert body["keep_alive"] == "30s" and body["options"]["num_predict"] == 50   # 30 秒后卸载模型


def test_ollama_errors_are_easy_to_understand(monkeypatch, no_system_proxy):
    monkeypatch.setattr(client._NO_PROXY, "open",
                        fake_urlopen(network_error=urllib.error.URLError(ConnectionRefusedError(10061, "refused"))))
    with pytest.raises(AIError) as info:
        client.ask(OLLAMA, "", "qwen3:1.7b", [])
    assert info.value.kind == "not_running"                             # 没装 / 没打开 Ollama

    payload = {"error": 'model "qwen3:1.7b" not found, try pulling it first'}
    monkeypatch.setattr(client._NO_PROXY, "open", fake_urlopen(http_error=(404, payload)))
    with pytest.raises(AIError) as info:
        client.ask(OLLAMA, "", "qwen3:1.7b", [])
    assert info.value.kind == "model_missing" and "not found" in info.value.detail


def test_ollama_retries_without_think_for_models_that_cannot_think(monkeypatch, no_system_proxy):
    bodies = []
    replies = iter([
        fake_urlopen(http_error=(400, {"error": '"gemma3:1b" does not support thinking'})),
        fake_urlopen({"message": {"content": "OK"}}),
    ])

    def fake_open(request, timeout=None):
        bodies.append(json.loads(request.data))
        return next(replies)(request, timeout)

    monkeypatch.setattr(client._NO_PROXY, "open", fake_open)
    assert client.ask(OLLAMA, "", "gemma3:1b", []) == "OK"
    assert "think" in bodies[0] and "think" not in bodies[1]           # 第二次去掉了 think


# ---------- describe：保证总有一句话 ----------

def test_describe_ok_not_configured_and_failed(monkeypatch):
    i18n._current = "zh"
    monkeypatch.setattr(ai_settings, "load_settings", lambda: None)
    d = ai.describe(MAIL, "zh")
    assert d.status == "not_configured" and d.bubble == "中国石化 发来了「电子发票通知」"

    monkeypatch.setattr(ai_settings, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(ai, "summarize", lambda m, lang, s: "嘿！你的加油发票来啦！")
    d = ai.describe(MAIL, "zh")
    assert d.status == "ok" and d.bubble == "嘿！你的加油发票来啦！"

    def broken(*args):
        raise AIError("x", kind="rate_limit")
    monkeypatch.setattr(ai, "summarize", broken)
    d = ai.describe(MAIL, "zh")
    assert d.status == "failed" and d.error.kind == "rate_limit" and "电子发票通知" in d.bubble


# ---------- settings：Key 存凭据管理器，其余存 config.yaml ----------

@pytest.fixture
def fake_keyring(monkeypatch, fake_store):
    keys = {}
    monkeypatch.setattr(ai_settings.keyring, "get_password", lambda service, user: keys.get((service, user)))
    monkeypatch.setattr(ai_settings.keyring, "set_password", lambda service, user, pw: keys.__setitem__((service, user), pw))
    return keys


def test_settings_roundtrip(fake_keyring):
    from yunyun import config as cfg
    assert ai_settings.load_settings() is None
    assert ai_settings.saved_choice() == ("zhipu", "glm-4.7-flash")   # 默认推荐智谱

    ai_settings.save_settings(AISettings("siliconflow", "Qwen/Qwen3-8B", "sk-secret"))
    assert ai_settings.load_settings() == AISettings("siliconflow", "Qwen/Qwen3-8B", "sk-secret")
    text = cfg.CONFIG_PATH.read_text(encoding="utf-8")
    assert "siliconflow" in text and "sk-secret" not in text          # Key 绝不写进文件
    assert fake_keyring[("yunyun-ai", "siliconflow")] == "sk-secret"


def test_local_model_needs_no_key(fake_keyring):
    ai_settings.save_settings(AISettings("ollama", "qwen3:1.7b", ""))
    assert ai_settings.load_settings() == AISettings("ollama", "qwen3:1.7b", "")   # 没有 Key 也算设置好了
    assert fake_keyring == {}                                                        # 凭据管理器里什么都没存


def test_settings_keep_mail_accounts(fake_keyring):
    from yunyun.mail import accounts
    from yunyun.mail.imap_client import MailAccount
    accounts.save_account(MailAccount("QQ邮箱", "qq", "me@qq.com"), "code")
    ai_settings.save_settings(SETTINGS)
    assert [a.address for a in accounts.load_accounts()] == ["me@qq.com"]   # 互不覆盖


# ---------- AI 设置窗口 ----------

def test_ai_page_saves_only_after_successful_test(qapp, fake_keyring, monkeypatch):
    import time
    from yunyun.pet import ai_page
    i18n._current = "zh"
    results = iter([AIError("x", kind="auth", detail="invalid key"), None])

    def fake_test(settings):
        time.sleep(0.05)
        error = next(results)
        if error:
            raise error
    monkeypatch.setattr(ai_page, "test_connection", fake_test)

    d = ai_page.AISettingsPage()
    assert d._model.text() == "glm-4.7-flash"                # 默认推荐智谱
    d._key.setText("bad-key")
    d._save_button.click()
    end = time.time() + 3
    while not d._save_button.isEnabled() and time.time() < end:
        qapp.processEvents()
    assert "API Key 不对" in d._status.text() and ai_settings.load_settings() is None

    d._key.setText("good-key")
    d._save_button.click()
    end = time.time() + 3
    while not d._save_button.isEnabled() and time.time() < end:
        qapp.processEvents()
    assert ai_settings.load_settings() == AISettings("zhipu", "glm-4.7-flash", "good-key")

    d._provider.setCurrentIndex(d._provider.findData("siliconflow"))   # 换服务商：模型跟着换
    assert d._model.text() == "Qwen/Qwen3-8B" and d._key.text() == ""


def test_ai_page_local_model(qapp, fake_keyring, monkeypatch):
    import time
    from yunyun.pet import ai_page
    i18n._current = "zh"
    results = iter([AIError("x", kind="model_missing", detail="not found", status=404),
                    AIError("x", kind="not_running"), None])

    def fake_test(settings):
        error = next(results)
        if error:
            raise error
    monkeypatch.setattr(ai_page, "test_connection", fake_test)

    def click_and_wait(page):
        page._save_button.click()
        end = time.time() + 3
        while not page._save_button.isEnabled() and time.time() < end:
            qapp.processEvents()

    d = ai_page.AISettingsPage()
    assert d._form.isRowVisible(d._key)                                 # 在线服务商：要填 Key
    d._provider.setCurrentIndex(d._provider.findData("ollama"))
    assert not d._form.isRowVisible(d._key)                             # 本地模型：Key 那一行藏起来
    assert d._model.text() == "qwen3:1.7b" and "不会发送到网上" in d._privacy.text()

    click_and_wait(d)                                                   # 不填 Key 也能测试
    assert "还没有模型 qwen3:1.7b" in d._status.text()
    click_and_wait(d)
    assert "连不上 Ollama" in d._status.text()
    click_and_wait(d)
    assert ai_settings.load_settings() == AISettings("ollama", "qwen3:1.7b", "")
