"""AI 模块的测试：不联网、不需要 API Key（网络请求和凭据管理器都用假的代替）。"""
import io
import json
import urllib.error

import pytest

from yunyun import ai, i18n
from yunyun.ai import client, settings as ai_settings, summarizer
from yunyun.ai.client import AIError
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

    def fake_chat(base_url, api_key, model, messages, extra=None, **kwargs):
        captured.update(base_url=base_url, model=model, messages=messages, extra=extra)
        return '{"bubble": "Hey! Your fuel invoice is here!"}'

    monkeypatch.setattr(summarizer, "chat", fake_chat)
    assert summarizer.summarize(MAIL, "en", SETTINGS) == "Hey! Your fuel invoice is here!"
    system, user = captured["messages"][0]["content"], captured["messages"][1]["content"]
    assert "English" in system and "验证码" in system        # 用英文回答；不要写敏感信息
    assert "电子发票通知" in user and "发票.pdf" in user        # 标题、附件名发给了模型
    assert "中国石化客服" not in user                           # 签名被去掉了
    assert captured["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
    assert captured["extra"] == {"thinking": {"type": "disabled"}}


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


def test_settings_keep_mail_accounts(fake_keyring):
    from yunyun.mail import accounts
    from yunyun.mail.imap_client import MailAccount
    accounts.save_account(MailAccount("QQ邮箱", "qq", "me@qq.com"), "code")
    ai_settings.save_settings(SETTINGS)
    assert [a.address for a in accounts.load_accounts()] == ["me@qq.com"]   # 互不覆盖


# ---------- AI 设置窗口 ----------

def test_ai_dialog_saves_only_after_successful_test(qapp, fake_keyring, monkeypatch):
    import time
    from yunyun.pet import ai_dialog
    i18n._current = "zh"
    results = iter([AIError("x", kind="auth", detail="invalid key"), None])

    def fake_test(settings):
        time.sleep(0.05)
        error = next(results)
        if error:
            raise error
    monkeypatch.setattr(ai_dialog, "test_connection", fake_test)

    d = ai_dialog.AISettingsDialog()
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
