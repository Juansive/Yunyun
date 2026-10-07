"""助手的大脑（assistant/claude_brain.py）：启动选项、环境准备、把 SDK 的消息翻译成事件。不启动 Claude Code。"""
import subprocess

import anyio
import pytest
from claude_agent_sdk import (
    AssistantMessage, ResultMessage, ServerToolUseBlock, StreamEvent, TextBlock, ToolResultBlock, ToolUseBlock,
    UserMessage,
)

from yunyun.assistant import claude_brain, protocol
from yunyun.assistant.claude_brain import TurnState, error_kind, translate
from yunyun.assistant.persona import persona
from yunyun.assistant.settings import AssistantSettings


def text_event(text):
    return StreamEvent(uuid="u", session_id="s",
                       event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})


def result(subtype="success", total=None, usage=None, is_error=False, result_text=None, errors=None):
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error, num_turns=1,
                         session_id="s", total_cost_usd=total, usage=usage, result=result_text, errors=errors)


# ---------- 翻译：文字和工具 ----------

def test_text_deltas_become_text_events():
    state = TurnState()
    assert translate(text_event("你好"), state) == [protocol.TextDelta("你好")]
    thinking = StreamEvent(uuid="u", session_id="s",
                           event={"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "嗯"}})
    tool_json = StreamEvent(uuid="u", session_id="s",
                            event={"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": "{"}})
    assert translate(thinking, state) == []                 # 思考过程不显示
    assert translate(tool_json, state) == []                # 工具参数的半截 JSON 不显示
    assert translate(text_event(""), state) == []


def test_tool_use_and_result():
    state = TurnState()
    message = AssistantMessage(content=[TextBlock("我查一下"), ToolUseBlock("toolu_1", "WebSearch", {"query": "芸芸"})],
                               model="m")
    assert translate(message, state) == [protocol.ToolStarted("toolu_1", "WebSearch", "芸芸")]
    assert translate(message, state) == []                  # 同一个工具调用只报一次
    fetch = AssistantMessage(content=[ToolUseBlock("toolu_2", "WebFetch", {"url": "https://example.com", "prompt": "x"})],
                             model="m")
    assert translate(fetch, state)[0].detail == "https://example.com"
    done = UserMessage(content=[ToolResultBlock("toolu_1", "结果", is_error=False),
                                ToolResultBlock("toolu_2", "出错了", is_error=True)])
    assert translate(done, state) == [protocol.ToolFinished("toolu_1", ok=True), protocol.ToolFinished("toolu_2", ok=False)]


def test_server_tool_counts_as_tool():
    message = AssistantMessage(content=[ServerToolUseBlock("srv_1", "web_search", {"query": "q"})], model="m")
    assert translate(message, TurnState()) == [protocol.ToolStarted("srv_1", "web_search", "q")]


def test_plain_user_message_is_ignored():
    assert translate(UserMessage(content="你好"), TurnState()) == []


# ---------- 翻译：这一轮结束、花费 ----------

def test_cost_is_per_turn_although_sdk_reports_session_total():
    state = TurnState()
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 1000, "output_tokens": 50}
    first = translate(result(total=0.010, usage=usage), state)
    assert first == [protocol.TurnFinished(cost_usd=0.010, session_cost_usd=0.010, input_tokens=3002, output_tokens=50)]
    state.new_turn()
    second = translate(result(total=0.015, usage={"output_tokens": 4}), state)[0]
    assert second.cost_usd == pytest.approx(0.005) and second.session_cost_usd == 0.015


def test_provider_without_usd_shows_no_cost():
    event = translate(result(total=0.02, usage={"input_tokens": 10, "output_tokens": 3}), TurnState(cost_in_usd=False))[0]
    assert event.cost_usd is None and event.session_cost_usd is None
    assert (event.input_tokens, event.output_tokens) == (10, 3)   # token 数照样显示


def test_user_stop_is_not_an_error():
    """点了"停止"时 SDK 的结果是 error_during_execution：这是正常停下，不报错。"""
    state = TurnState(interrupted=True)
    # 这一轮的花费 SDK 没报（None）；这次打开助手以来的累计还是知道的（这里是 0）
    assert translate(result("error_during_execution", is_error=True), state) == [
        protocol.TurnFinished(cost_usd=None, session_cost_usd=0.0, stopped=True)]


def test_budget_exceeded():
    events = translate(result("error_max_budget_usd", total=0.11, is_error=True), TurnState())
    assert events[0] == protocol.Error("budget")
    assert isinstance(events[1], protocol.TurnFinished)


def test_unexpected_failure_is_an_error():
    events = translate(result("error_during_execution", is_error=True, errors=["boom"]), TurnState())
    assert events[0].kind == "other" and "boom" in events[0].detail


def test_api_error_in_assistant_message():
    state = TurnState()
    message = AssistantMessage(content=[TextBlock("API Error: 401 invalid x-api-key")], model="m",
                               error="authentication_failed")
    assert translate(message, state) == [protocol.Error("auth", "API Error: 401 invalid x-api-key")]
    # 后面的结果消息也说出错了：不要再报一次
    assert [type(e) for e in translate(result(is_error=True, result_text="API Error"), state)] == [protocol.TurnFinished]


@pytest.mark.parametrize("text, sdk_error, kind", [
    ("", "billing_error", "billing"),
    ("", "rate_limit", "rate_limit"),
    ("API Error: Connection error.", "unknown", "network"),
    ("fetch failed: getaddrinfo ENOTFOUND api.anthropic.com", None, "network"),
    ("Your credit balance is too low", None, "billing"),
    ("429 Too Many Requests", None, "rate_limit"),
    ("Invalid API key · 401", None, "auth"),
    ("something odd", "server_error", "other"),
])
def test_error_kind(text, sdk_error, kind):
    assert error_kind(text, sdk_error) == kind
    assert kind in protocol.ERROR_KINDS


# ---------- 启动前的准备 ----------

def test_clean_environment_removes_inherited_claude_variables():
    env = {"ANTHROPIC_BASE_URL": "x", "CLAUDE_CODE_SESSION_ID": "x", "MCP_SERVER_X": "x", "CLAUDECODE": "1",
           "PATH": "C:\\Windows", "APPDATA": "C:\\Users\\a"}
    removed = claude_brain.clean_environment(env)
    assert set(removed) == {"ANTHROPIC_BASE_URL", "CLAUDE_CODE_SESSION_ID", "MCP_SERVER_X", "CLAUDECODE"}
    assert env == {"PATH": "C:\\Windows", "APPDATA": "C:\\Users\\a"}


def test_prepare_process_skips_version_check_and_hides_consoles(monkeypatch):
    calls = []

    async def fake_open_process(*args, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(anyio, "open_process", fake_open_process)   # 测完 monkeypatch 会换回真的
    env = {"CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "0", "CLAUDE_CODE_ENTRYPOINT": "x"}
    claude_brain.prepare_process(env)
    assert env == {"CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK": "1"}      # 先清掉、再设上自己要的
    claude_brain.hide_child_consoles()                              # 重复调用不会包两层
    anyio.run(anyio.open_process, ["claude.exe"])
    assert calls == [{"creationflags": subprocess.CREATE_NO_WINDOW}]


@pytest.mark.parametrize("environ, proxies, expected", [
    ({}, {}, {}),                                                         # 没开系统代理（比如 Clash 的 TUN 模式）
    ({}, {"https": "http://127.0.0.1:7890"}, {"HTTPS_PROXY": "http://127.0.0.1:7890"}),
    ({}, {"http": "127.0.0.1:7890"}, {"HTTPS_PROXY": "http://127.0.0.1:7890"}),   # 没写 http:// 也认得
    ({"HTTPS_PROXY": "http://p:1"}, {"https": "http://127.0.0.1:7890"}, {}),        # 自己设过了：不管
])
def test_system_proxy_env(environ, proxies, expected):
    env = claude_brain.system_proxy_env(environ, getproxies=lambda: proxies)
    assert {k: v for k, v in env.items() if k != "NO_PROXY"} == expected


def test_build_options(monkeypatch):
    monkeypatch.setattr(claude_brain, "system_proxy_env", lambda: {})
    options = claude_brain.build_options(AssistantSettings(budget_usd=0.1), "sk-ant-test", "en")
    assert options.model == "claude-haiku-4-5"
    assert options.tools == options.allowed_tools == ["WebSearch", "WebFetch"]   # 阶段 1：只有网页工具
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == [] and options.strict_mcp_config
    assert options.effort is None                                  # Haiku 4.5 不支持 effort
    assert options.max_budget_usd == 0.1
    assert options.include_partial_messages
    assert options.system_prompt == {"type": "preset", "preset": "claude_code", "append": persona("en")}
    assert options.cwd == str(claude_brain.WORKSPACE)
    assert options.env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert options.env["CLAUDE_CONFIG_DIR"] == str(claude_brain.CONFIG_DIR)
    assert options.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_persona_languages():
    assert "芸芸" in persona("zh") and "Yunyun" in persona("en") and "芸芸" in persona("ja")
    assert persona("fr") == persona("zh")                          # 不认识的语言用中文
