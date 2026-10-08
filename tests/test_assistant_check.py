"""设置窗口的"测试连接"：小进程里的 check()（官方 SDK 换成假的）和芸芸这头的 KeyCheck（用假的小进程）。不联网、不花钱。"""
import sys
import time
from pathlib import Path

import anthropic
import httpx2
import pytest

from yunyun.assistant import check as check_module
from yunyun.assistant import process as assistant_process
from yunyun.assistant import protocol
from yunyun.assistant.process import KeyCheck

FAKE_CHECK = Path(__file__).with_name("fake_assistant_check.py")
REQUEST = httpx2.Request("GET", "https://api.anthropic.com/v1/models/claude-haiku-4-5")


def status_error(cls, code, message="出错了"):
    return cls(message, response=httpx2.Response(code, request=REQUEST), body=None)


class FakeAnthropic:
    """代替 anthropic.Anthropic：记下怎么被创建的，models.retrieve 时按设定抛错（或成功）。"""
    created = []
    error = None

    def __init__(self, **kwargs):
        FakeAnthropic.created.append(kwargs)
        self.models = self

    def retrieve(self, model):
        if FakeAnthropic.error is not None:
            raise FakeAnthropic.error
        return {"id": model}


@pytest.fixture
def fake_sdk(monkeypatch):
    FakeAnthropic.created, FakeAnthropic.error = [], None
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(check_module, "system_proxy_env", lambda: {})
    monkeypatch.setattr(check_module, "clean_environment", lambda: [])   # 别动测试进程自己的环境变量
    return FakeAnthropic


# ---------- 小进程里的 check() ----------

def test_check_ok(fake_sdk):
    assert check_module.check("anthropic", "claude-haiku-4-5", "sk-ant-test") == (None, "")
    assert fake_sdk.created == [{"api_key": "sk-ant-test", "base_url": None, "max_retries": 1, "timeout": 20}]


@pytest.mark.parametrize("error, kind", [
    (status_error(anthropic.NotFoundError, 404), "model"),
    (status_error(anthropic.AuthenticationError, 401), "auth"),
    (status_error(anthropic.PermissionDeniedError, 403), "auth"),
    (status_error(anthropic.RateLimitError, 429), "rate_limit"),
    (anthropic.APIConnectionError(request=REQUEST), "network"),
    (anthropic.APITimeoutError(request=REQUEST), "network"),
    (status_error(anthropic.InternalServerError, 500), "other"),
])
def test_check_error_kinds(fake_sdk, error, kind):
    fake_sdk.error = error
    found, detail = check_module.check("anthropic", "m", "k")
    assert found == kind and kind in protocol.ERROR_KINDS
    assert detail


def test_check_shows_only_the_servers_sentence(fake_sdk):
    """服务器回的是一大段 JSON：只把里面那句话给用户看。"""
    fake_sdk.error = anthropic.AuthenticationError(
        "Error code: 401 - {...}", response=httpx2.Response(401, request=REQUEST),
        body={"type": "error", "error": {"type": "authentication_error", "message": "API key is invalid."}})
    assert check_module.check("anthropic", "m", "k") == ("auth", "API key is invalid.")


def test_check_unknown_provider(fake_sdk):
    assert check_module.check("no-such", "m", "k")[0] == "other"
    assert fake_sdk.created == []


def test_check_cleans_inherited_variables_before_creating_the_client(monkeypatch):
    """官方 SDK 会读 ANTHROPIC_BASE_URL：开发时 Claude 桌面版设了它，不清掉的话测试会发到桌面版的通道去。"""
    order = []
    monkeypatch.setattr(check_module, "clean_environment", lambda: order.append("clean"))
    monkeypatch.setattr(check_module, "system_proxy_env", lambda: order.append("proxy") or {})

    class Recorder(FakeAnthropic):
        def __init__(self, **kwargs):
            order.append("client")
            super().__init__(**kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", Recorder)
    check_module.check("anthropic", "m", "k")
    assert order == ["clean", "proxy", "client"]


# ---------- DeepSeek：查模型列表、查余额（get_json 换成假的） ----------

DEEPSEEK_MODELS = {"object": "list", "data": [{"id": "deepseek-flash", "object": "model"},
                                              {"id": "deepseek-v4-pro", "object": "model"}]}
DEEPSEEK_BALANCE = {"is_available": True, "balance_infos": [
    {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance": "100.00"}]}


@pytest.fixture
def fake_http(monkeypatch):
    """get_json 换成按网址回答的假函数；记下问了哪些网址、用的什么 Key。"""
    answers, calls = {}, []
    monkeypatch.setattr(check_module, "system_proxy_env", lambda: {})
    monkeypatch.setattr(check_module, "clean_environment", lambda: [])

    def get_json(url, key):
        calls.append((url, key))
        answer = answers.get(url)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(check_module, "get_json", get_json)
    answers["https://api.deepseek.com/models"] = DEEPSEEK_MODELS
    answers["https://api.deepseek.com/user/balance"] = DEEPSEEK_BALANCE
    return answers, calls


def test_deepseek_check_uses_its_model_list(fake_http, fake_sdk):
    _, calls = fake_http
    assert check_module.check("deepseek", "deepseek-v4-pro", "sk-ds") == (None, "")
    assert calls == [("https://api.deepseek.com/models", "sk-ds")] and fake_sdk.created == []   # 不用 Anthropic 的 SDK
    kind, detail = check_module.check("deepseek", "deepseek-typo", "sk-ds")   # 它会悄悄换成 flash：这里要查出来
    assert kind == "model" and "deepseek-flash" in detail


def test_deepseek_check_errors(fake_http):
    answers, _ = fake_http
    answers["https://api.deepseek.com/models"] = check_module.CheckError("auth", "401: Authentication Fails")
    assert check_module.check("deepseek", "deepseek-flash", "bad") == ("auth", "401: Authentication Fails")


def test_run_check_adds_the_balance(fake_http):
    assert check_module.run({"provider": "deepseek", "model": "deepseek-flash", "key": "k"}) == {
        "ok": True, "balance": {"available": True, "items": [{"currency": "CNY", "total": "110.00"}]}}
    assert check_module.run({"provider": "deepseek", "key": "k", "what": "balance"})["balance"]["items"][0]["total"] == "110.00"


def test_run_check_passes_even_if_balance_fails(fake_http):
    answers, _ = fake_http
    answers["https://api.deepseek.com/user/balance"] = check_module.CheckError("network", "timed out")
    assert check_module.run({"provider": "deepseek", "model": "deepseek-flash", "key": "k"}) == {"ok": True}
    assert check_module.run({"provider": "deepseek", "key": "k", "what": "balance"}) == {
        "ok": False, "kind": "network", "detail": "timed out"}


def test_anthropic_has_no_balance(fake_sdk, fake_http):
    _, calls = fake_http
    assert check_module.run({"provider": "anthropic", "model": "claude-haiku-4-5", "key": "k"}) == {
        "ok": True, "balance": None}
    assert calls == []


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.mark.parametrize("code, kind", [(401, "auth"), (402, "billing"), (429, "rate_limit"), (500, "other")])
def test_get_json_http_errors(monkeypatch, code, kind):
    import io
    import urllib.error

    def urlopen(request, timeout):
        assert request.get_header("Authorization") == "Bearer sk-x"
        body = io.BytesIO(b'{"error": {"message": "Authentication Fails"}}')
        raise urllib.error.HTTPError(request.full_url, code, "err", {}, body)
    monkeypatch.setattr(check_module.urllib.request, "urlopen", urlopen)
    with pytest.raises(check_module.CheckError) as info:
        check_module.get_json("https://api.deepseek.com/models", "sk-x")
    assert info.value.kind == kind and "Authentication Fails" in info.value.detail


def test_get_json_network_and_ok(monkeypatch):
    import urllib.error

    def offline(request, timeout):
        raise urllib.error.URLError("getaddrinfo failed")
    monkeypatch.setattr(check_module.urllib.request, "urlopen", offline)
    with pytest.raises(check_module.CheckError) as info:
        check_module.get_json("https://api.deepseek.com/models", "k")
    assert info.value.kind == "network"
    monkeypatch.setattr(check_module.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b'{"a": 1}'))
    assert check_module.get_json("https://api.deepseek.com/models", "k") == {"a": 1}


# ---------- 芸芸这头的 KeyCheck（用假的小进程） ----------

def wait_until(qapp, condition, timeout=10.0):
    end = time.time() + timeout
    while not condition() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    return condition()


def run_check(qapp, model, key="sk-ant-test"):
    results = []
    check = KeyCheck("anthropic", model, key, command=(sys.executable, [str(FAKE_CHECK)], None))
    check.done.connect(results.append)
    check.start()
    assert wait_until(qapp, lambda: results)
    return check, results


def test_key_check_ok(qapp):
    check, results = run_check(qapp, "ok")
    assert results == [None]
    assert "sk-ant-test" not in " ".join(check._proc.arguments())   # Key 走标准输入，不在命令行上


def test_key_check_reports_kind_and_detail(qapp):
    check, results = run_check(qapp, "claude-nope")
    assert results[0].kind == "model"
    assert "Key 长 11" in results[0].detail                       # Key 确实从标准输入传过去了


def test_key_check_unknown_kind_becomes_other(qapp):
    assert run_check(qapp, "bad-kind")[1][0].kind == "other"


def test_key_check_without_result(qapp):
    assert run_check(qapp, "crash")[1][0].kind == "worker"


def test_key_check_timeout(qapp, monkeypatch):
    monkeypatch.setattr(assistant_process, "CHECK_TIMEOUT_MS", 300)
    check, results = run_check(qapp, "slow")
    assert results[0] == protocol.Error("network", "测试连接超时")
    assert wait_until(qapp, lambda: not check.is_running())          # 超时后把小进程结束掉了


def test_key_check_balance_comes_before_done(qapp):
    order = []
    check = KeyCheck("deepseek", "", "sk-ds", command=(sys.executable, [str(FAKE_CHECK)], None), what="balance")
    check.balance.connect(lambda balance: order.append(("balance", balance)))
    check.done.connect(lambda error: order.append(("done", error)))
    check.start()
    assert wait_until(qapp, lambda: len(order) == 2)
    assert order == [("balance", {"available": True, "items": [{"currency": "CNY", "total": "1.00"}]}), ("done", None)]


def test_key_check_program_missing(qapp):
    results = []
    check = KeyCheck("anthropic", "ok", "k", command=("no_such_program_for_yunyun.exe", [], None))
    check.done.connect(results.append)
    check.start()
    assert wait_until(qapp, lambda: results)
    assert results[0].kind == "worker" and len(results) == 1
