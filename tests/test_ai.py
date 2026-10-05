"""AI 模块的测试：不联网、不需要 API Key（网络请求和凭据管理器都用假的代替）。"""
import io
import json
import re
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


@pytest.mark.parametrize("bubble, expected", [
    # 实测本地小模型真的写出过的句子（2026-10-05）
    ("招商银行发来10月账单，应还款3256元，最后还款日25号", "招商银行发来10月账单，最后还款日25号"),
    ("您的包裹已到菜鸟驿站，取件码6-3-2048，3天内取件", "您的包裹已到菜鸟驿站，3天内取件"),
    ("Steam发来游戏购买凭证，金额为98元", "Steam发来游戏购买凭证"),
    ("京东发来双11预售的广告，iPhone 立减 800 元", "京东发来双11预售的广告"),
    ("某银行发来登录验证码384920", "某银行发来登录验证码"),                 # 第一句只删那个数
    ("你的快递到了，运单号 7300123456789，记得去取", "你的快递到了，记得去取"),
    ("Your receipt is here, total $9.99", "Your receipt is here"),
    ("Credit card bill is out, due amount 3,256.00 by Oct 25", "Credit card bill is out"),   # 没写单位的金额
    ("JD sent an ad for Double 11—iPhone 16 discount 800 yuan", "JD sent an ad for Double 11"),   # 破折号断句
    ("Bank sent a verification code—384920, valid for 5 minutes", "Bank sent a verification code—valid for 5 minutes"),
    ("请联系 zhang@example.com，网址 https://a.example/x", "请联系"),
    # 正常的句子不受影响
    ("王老师说第三章要补充对比实验，下周一前发他", "王老师说第三章要补充对比实验，下周一前发他"),
    ("字节跳动邀请你10月9日14:00线上面试", "字节跳动邀请你10月9日14:00线上面试"),
    ("Alice asked for a code review", "Alice asked for a code review"),
    ("京东双11预售来了啦！", "京东双11预售来了啦！"),
])
def test_sensitive_info_never_reaches_the_bubble(bubble, expected):
    assert summarizer.scrub_sensitive(bubble) == expected
    assert summarizer.parse_bubble(json.dumps({"bubble": bubble}, ensure_ascii=False)) == expected


def test_fallback_hides_codes_in_subject():
    i18n._current = "zh"
    mail = MailMessage(1, "【某银行】登录验证码 384920", "某银行", "a@b.com", None, "", [])
    assert summarizer.fallback_bubble(mail) == "某银行 发来了「【某银行】登录验证码」"


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
        captured.update(provider=provider, model=model, messages=messages, temperature=kwargs.get("temperature"))
        return '{"bubble": "Hey! Your fuel invoice is here!"}'

    monkeypatch.setattr(summarizer, "ask", fake_ask)
    assert summarizer.summarize(MAIL, "en", SETTINGS) == "Hey! Your fuel invoice is here!"
    messages = captured["messages"]
    system, user = messages[0]["content"], messages[-1]["content"]   # 中间是示范对话，最后一条才是这封邮件
    assert "English" in system and "验证码" in system        # 用英文回答；不要写敏感信息
    assert "电子发票通知" in user and "发票.pdf" in user        # 标题、附件名发给了模型
    assert "中国石化客服" not in user                           # 签名被去掉了
    examples = [m for m in messages[1:-1] if m["role"] == "assistant"]
    chinese = re.compile(r"[一-鿿]")
    assert examples and not any(chinese.search(json.loads(m["content"])["bubble"]) for m in examples)   # 示范的回答也是英文
    assert captured["temperature"] == summarizer.TEMPERATURE
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
    assert 2 <= body["options"]["num_thread"] == client.OLLAMA_THREADS             # 少用几个线程，不拖卡电脑


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


def ndjson(*items):
    """Ollama 下载时的返回：每行一个 JSON。"""
    return FakeResponse(b"".join(json.dumps(item).encode() + b"\n" for item in items))


def test_pull_reports_total_progress_across_layers(monkeypatch, no_system_proxy):
    captured = {}
    stream = ndjson(
        {"status": "pulling manifest"},
        {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 0},
        {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 500},
        {"status": "pulling bbb", "digest": "sha256:bbb", "total": 100, "completed": 100},   # 第二层（小文件）
        {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 1000},
        {"status": "verifying sha256 digest"},
        {"status": "success"},
    )

    def fake_open(request, timeout=None):
        captured.update(url=request.full_url, body=json.loads(request.data))
        return stream
    monkeypatch.setattr(client._NO_PROXY, "open", fake_open)

    progress = []
    client.pull_ollama("http://127.0.0.1:11434", "qwen3:1.7b", lambda done, total: progress.append((done, total)))
    assert captured == {"url": "http://127.0.0.1:11434/api/pull", "body": {"model": "qwen3:1.7b", "stream": True}}
    assert progress == [(0, 1000), (500, 1000), (600, 1100), (1100, 1100)]   # 各层加起来算总进度


def test_pull_errors(monkeypatch, no_system_proxy):
    bad = ndjson({"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"})
    monkeypatch.setattr(client._NO_PROXY, "open", lambda request, timeout=None: bad)
    with pytest.raises(AIError) as info:
        client.pull_ollama("http://127.0.0.1:11434", "qwen9:99b")              # 模型名写错了
    assert "file does not exist" in info.value.detail

    cut = ndjson({"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 10})
    monkeypatch.setattr(client._NO_PROXY, "open", lambda request, timeout=None: cut)
    with pytest.raises(AIError):
        client.pull_ollama("http://127.0.0.1:11434", "qwen3:1.7b")             # 没等到 success 就断了

    monkeypatch.setattr(client._NO_PROXY, "open", fake_urlopen(network_error=urllib.error.URLError("refused")))
    with pytest.raises(AIError) as info:
        client.pull_ollama("http://127.0.0.1:11434", "qwen3:1.7b")
    assert info.value.kind == "not_running"


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


def test_prepare_warms_up_only_local_models(fake_keyring, monkeypatch):
    import time
    loaded = []
    monkeypatch.setattr(ai, "load_ollama_model", lambda provider, model: loaded.append(model))
    ai_settings.save_settings(SETTINGS)                                  # 在线服务商：不用提前准备
    ai.prepare()
    ai_settings.save_settings(AISettings("ollama", "qwen3:1.7b", ""))   # 本地模型：后台提前加载
    ai.prepare()
    end = time.time() + 2
    while not loaded and time.time() < end:
        time.sleep(0.01)
    assert loaded == ["qwen3:1.7b"]


def test_card_deck_note_explains_why_ai_was_not_used():
    from yunyun.main import ai_note
    i18n._current = "zh"
    note = lambda status, kind=None: ai_note(ai.Description("…", status, AIError("x", kind=kind) if kind else None))
    assert "还没设置 AI" in note("not_configured")
    assert "Ollama 装好了吗" in note("failed", "not_running")      # 本地模型：说清楚该做什么
    assert "还没下载" in note("failed", "model_missing")
    assert note("failed", "rate_limit") == i18n.tr("ai_failed")
    assert note("failed") == i18n.tr("ai_failed")                 # 处理时出了意外（没有错误对象）


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
    assert "还没有模型 qwen3:1.7b" in d._status.text() and not d._download_button.isHidden()
    click_and_wait(d)
    assert "没找到 Ollama" in d._status.text() and d._download_button.isHidden()
    click_and_wait(d)
    assert ai_settings.load_settings() == AISettings("ollama", "qwen3:1.7b", "")


def test_ai_page_downloads_missing_model_then_saves(qapp, fake_keyring, monkeypatch):
    import time
    from yunyun.pet import ai_page
    i18n._current = "zh"
    tests = iter([AIError("x", kind="model_missing", status=404), None])

    def fake_test(settings):
        error = next(tests)
        if error:
            raise error

    downloads = iter([AIError("x", kind="other", detail="pull model manifest: file does not exist"), None])
    progress_seen = []

    def fake_download(settings, on_progress):
        error = next(downloads)
        if error:
            raise error
        on_progress(500_000_000, 1_400_000_000)
        on_progress(1_400_000_000, 1_400_000_000)
    monkeypatch.setattr(ai_page, "test_connection", fake_test)
    monkeypatch.setattr(ai_page, "download_model", fake_download)

    def wait_until(condition):
        end = time.time() + 3
        while not condition() and time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)

    d = ai_page.AISettingsPage()
    d._download_progress.connect(lambda model, percent, sizes: progress_seen.append((percent, sizes)))
    d._provider.setCurrentIndex(d._provider.findData("ollama"))
    d._save_button.click()
    wait_until(lambda: not d._download_button.isHidden())               # 缺模型：出现"下载模型"

    d._download_button.click()                                          # 第一次下载失败
    wait_until(lambda: "没下载成功" in d._status.text())
    assert "file does not exist" in d._status.text() and not d._download_button.isHidden()   # 可以再试

    d._download_button.click()                                          # 再试：成功，自动测试并保存
    wait_until(lambda: ai_settings.load_settings() is not None)
    assert ai_settings.load_settings() == AISettings("ollama", "qwen3:1.7b", "")
    assert progress_seen == [(35, "500 MB / 1.4 GB"), (100, "1.4 GB / 1.4 GB")]
