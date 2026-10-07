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


def test_key_check_program_missing(qapp):
    results = []
    check = KeyCheck("anthropic", "ok", "k", command=("no_such_program_for_yunyun.exe", [], None))
    check.done.connect(results.append)
    check.start()
    assert wait_until(qapp, lambda: results)
    assert results[0].kind == "worker" and len(results) == 1
