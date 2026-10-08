"""助手的大脑（assistant/claude_brain.py）：启动选项、环境准备、把 SDK 的消息翻译成事件、权限。不启动 Claude Code。"""
import asyncio
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


# ---------- 撤销：检查点和改过的文件 ----------

def edit(tool_id, path, tool="Edit"):
    return AssistantMessage(content=[ToolUseBlock(tool_id, tool, {"file_path": path, "old_string": "a"})], model="m")


def test_checkpoint_and_changed_files_are_reported_at_turn_end():
    state = TurnState()
    state.new_turn()
    translate(UserMessage(content="改一下", uuid="cp-1"), state)          # 传回来的这一问：它的 uuid 就是检查点
    translate(UserMessage(content="别的注入消息", uuid="cp-x"), state)     # 只认第一条
    for message in (edit("t1", "F:\\p\\a.py"), edit("t2", "F:\\p\\b.py", "Write"), edit("t3", "F:\\p\\c.py"),
                    AssistantMessage(content=[ToolUseBlock("t4", "Read", {"file_path": "F:\\p\\d.py"})], model="m")):
        translate(message, state)
    translate(UserMessage(content=[ToolResultBlock("t1", "ok", is_error=False), ToolResultBlock("t2", "ok"),
                                   ToolResultBlock("t3", "失败", is_error=True), ToolResultBlock("t4", "内容")],
                          uuid="tool-result-uuid"), state)
    translate(edit("t5", "F:\\p\\a.py"), state)                               # 同一个文件改两次：只记一次
    translate(UserMessage(content=[ToolResultBlock("t5", "ok", is_error=False)]), state)
    finished = translate(result(total=0.01), state)[-1]
    assert finished.checkpoint == "cp-1"
    assert finished.files == ("F:\\p\\a.py", "F:\\p\\b.py")                  # 改失败的、只读的不算


def test_turn_without_changes_has_no_checkpoint():
    state = TurnState()
    state.new_turn()
    translate(UserMessage(content="问问题", uuid="cp-2"), state)
    finished = translate(result(total=0.01), state)[-1]
    assert (finished.checkpoint, finished.files) == ("", ())                  # 没改文件：没什么可撤销的


def test_new_turn_forgets_the_last_checkpoint():
    state = TurnState()
    state.new_turn()
    translate(UserMessage(content="一", uuid="cp-1"), state)
    translate(edit("t1", "a.py"), state)
    translate(UserMessage(content=[ToolResultBlock("t1", "ok", is_error=False)]), state)
    translate(result(total=0.01), state)
    state.new_turn()
    translate(UserMessage(content="二", uuid="cp-2"), state)
    finished = translate(result(total=0.02), state)[-1]
    assert (finished.checkpoint, finished.files) == ("", ())


def test_checkpointing_only_with_a_work_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_brain, "system_proxy_env", lambda: {})
    working = claude_brain.build_options(AssistantSettings(workspace=str(tmp_path)), "k", "zh")
    assert working.enable_file_checkpointing and working.extra_args == {"replay-user-messages": None}
    chatting = claude_brain.build_options(AssistantSettings(), "k", "zh")
    assert not chatting.enable_file_checkpointing and chatting.extra_args == {}


class RewindClient:
    """只会 rewind_files 的假 SDK 客户端。"""

    def __init__(self, error=None):
        self.error, self.rewound = error, []

    async def rewind_files(self, checkpoint):
        if self.error:
            raise self.error
        self.rewound.append(checkpoint)


@pytest.mark.parametrize("error", [None, RuntimeError("No file checkpoint found for this message.")])
def test_brain_rewind_reports_the_result(error):
    events = []
    brain = claude_brain.ClaudeBrain(AssistantSettings(), "k", "zh", events.append)
    brain._client = RewindClient(error)
    asyncio.run(brain.rewind("cp-1"))
    if error is None:
        assert brain._client.rewound == ["cp-1"] and events == [protocol.Rewound("cp-1", True)]
    else:
        assert events[0].ok is False and "No file checkpoint" in events[0].detail


# ---------- 翻译：这一轮结束、花费 ----------

def test_cost_is_per_turn_although_sdk_reports_session_total():
    state = TurnState()
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 1000, "output_tokens": 50}
    first = translate(result(total=0.010, usage=usage), state)
    assert first == [protocol.TurnFinished(cost_usd=0.010, session_cost_usd=0.010, input_tokens=3002, output_tokens=50,
                                           session_id="s")]   # 会话编号："接着聊"时用它恢复
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
        protocol.TurnFinished(cost_usd=None, session_cost_usd=0.0, stopped=True, session_id="s")]


def test_budget_exceeded():
    events = translate(result("error_max_budget_usd", total=0.11, is_error=True), TurnState())
    assert events[0] == protocol.Error("budget")
    assert isinstance(events[1], protocol.TurnFinished)


# ---------- 每次回答的花费：按 token 估算 ----------

def usage_event(kind, usage):
    event = ({"type": "message_start", "message": {"usage": usage}} if kind == "start"
             else {"type": "message_delta", "usage": usage})
    return StreamEvent(uuid="u", session_id="s", event=event)


def test_reply_cost_is_estimated_from_usage():
    state = TurnState(prices=(1, 5), reply_budget=1.0)
    assert translate(usage_event("start", {"input_tokens": 1000, "cache_creation_input_tokens": 8000,
                                           "cache_read_input_tokens": 10000, "output_tokens": 1}), state) == []
    first_input = (1000 + 8000 * 1.25 + 10000 * 0.1) / 1e6          # 写缓存贵 25%，读缓存只要一成
    translate(usage_event("delta", {"output_tokens": 201, "server_tool_use": {"web_search_requests": 1}}), state)
    translate(usage_event("delta", {"output_tokens": 401, "server_tool_use": {"web_search_requests": 1}}), state)
    assert state.estimate == pytest.approx(first_input + 401 * 5 / 1e6 + 0.01)   # 报的是累计值：只算新增的
    translate(usage_event("start", {"input_tokens": 500, "output_tokens": 0}), state)   # 用完工具，第二次调用模型
    assert state.estimate == pytest.approx(first_input + 401 * 5 / 1e6 + 0.01 + 500 / 1e6)
    assert not state.over_budget
    state.new_turn()
    assert state.estimate == 0.0


def test_over_reply_budget_stops_with_its_own_error():
    state = TurnState(prices=(1, 5), reply_budget=0.01)
    translate(usage_event("start", {"input_tokens": 20000}), state)
    assert state.over_budget
    state.interrupted = True                                       # 大脑会去停下这一轮
    events = translate(result("error_during_execution", total=0.02, is_error=True), state)
    assert events[0] == protocol.Error("reply_budget", "~$0.020")
    assert events[1].stopped is False                              # 不算"用户点了停止"


def test_unknown_price_means_no_estimate():
    state = TurnState(prices=None, reply_budget=0.01)
    translate(usage_event("start", {"input_tokens": 10 ** 6}), state)
    assert state.estimate == 0 and not state.over_budget


class FakeClient:
    """假的 ClaudeSDKClient：按剧本吐消息，记下有没有被叫停。"""

    def __init__(self, messages):
        self.messages = messages
        self.interrupted = False

    async def query(self, text):
        pass

    async def receive_response(self):
        for message in self.messages:
            await anyio.sleep(0)      # 让别的任务（去停下这一轮的）有机会运行
            yield message

    async def interrupt(self):
        self.interrupted = True


def test_brain_stops_the_reply_when_over_budget():
    emitted = []
    brain = claude_brain.ClaudeBrain(AssistantSettings(budget_usd=0.01), "sk-ant-test", "zh", emitted.append)
    brain._client = FakeClient([
        usage_event("start", {"input_tokens": 20000}),             # Haiku：$0.02，超过 $0.01
        text_event("写到"),
        result("error_during_execution", total=0.02, is_error=True),
    ])

    async def run():
        await brain.ask("hi")
        await anyio.sleep(0.01)

    anyio.run(run)
    assert brain._client.interrupted
    assert protocol.Error("reply_budget", "~$0.020") in emitted


def test_custom_model_has_no_price_so_no_reply_estimate():
    brain = claude_brain.ClaudeBrain(AssistantSettings(model="my-own-model"), "k", "zh", lambda e: None)
    assert brain._state.prices is None


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
    gate = claude_brain.PermissionGate(claude_brain.WORKSPACE, lambda event: None)
    options = claude_brain.build_options(
        AssistantSettings(budget_usd=0.1, conversation_budget_usd=0.7), "sk-ant-test", "en", gate=gate)
    assert options.model == "claude-haiku-4-5"
    assert options.tools == ["WebSearch", "WebFetch"]             # 没选工作文件夹：只有网页工具
    assert options.allowed_tools == []                            # 一个都不直接放行：全部由 gate 拍板
    assert options.permission_mode == "default"                   # dontAsk 会把"要问"直接变成拒绝
    assert options.hooks["PreToolUse"][0].hooks == [gate.pre_tool_use]
    assert options.can_use_tool == gate.can_use_tool
    assert options.setting_sources == [] and options.strict_mcp_config
    assert options.effort is None                                  # Haiku 4.5 不支持 effort
    assert options.max_budget_usd == 0.7   # Claude Code 按整段对话累计算；每次回答的上限芸芸自己估算
    assert options.include_partial_messages
    assert options.system_prompt == {"type": "preset", "preset": "claude_code", "append": persona("en")}
    assert options.cwd == str(claude_brain.WORKSPACE)
    assert options.env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert options.env["CLAUDE_CONFIG_DIR"] == str(claude_brain.CONFIG_DIR)
    assert options.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


def test_build_options_uses_the_chosen_work_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_brain, "system_proxy_env", lambda: {})
    options = claude_brain.build_options(AssistantSettings(workspace=str(tmp_path)), "sk-ant-test", "zh")
    assert options.cwd == str(tmp_path)
    assert options.tools == ["WebSearch", "WebFetch", "Read", "Glob", "Grep", "Edit", "Write", "Bash", "PowerShell"]
    assert options.system_prompt["append"] == persona("zh", working=True)


def test_project_claude_md_is_read_but_not_its_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_brain, "system_proxy_env", lambda: {})
    (tmp_path / "CLAUDE.md").write_text("# 规矩\n用中文写注释。", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "CLAUDE.md").write_text("测试用 pytest -q", encoding="utf-8")
    (tmp_path / ".claude" / "settings.json").write_text('{"permissions": {"allow": ["Bash"]}}', encoding="utf-8")
    options = claude_brain.build_options(AssistantSettings(workspace=str(tmp_path)), "k", "zh")
    append = options.system_prompt["append"]
    assert "用中文写注释。" in append and "测试用 pytest -q" in append
    assert "permissions" not in append and options.setting_sources == []   # .claude/settings.json 不加载


def test_long_project_notes_are_cut(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_brain, "PROJECT_NOTES_MAX_CHARS", 100)
    (tmp_path / "CLAUDE.md").write_text("很长" * 500, encoding="utf-8")
    notes = claude_brain.read_project_notes(str(tmp_path))
    assert notes.startswith("很长") and len(notes) < 130 and notes.endswith("（后面太长，没有读）")
    assert claude_brain.read_project_notes("") == ""


def test_persona_languages():
    assert "芸芸" in persona("zh") and "Yunyun" in persona("en") and "芸芸" in persona("ja")
    assert persona("fr") == persona("zh")                          # 不认识的语言用中文
    for language in ("zh", "en", "ja"):                            # 选没选工作文件夹，说的能力不一样
        assert persona(language, working=True) != persona(language)
        assert persona(language, project_notes="RULES").endswith("RULES")


def test_tool_detail_shows_paths_inside_the_work_folder_relatively(tmp_path):
    inside, outside = str(tmp_path / "src" / "a.py"), "Z:\\other\\b.py"
    assert claude_brain._tool_detail("Read", {"file_path": inside}, str(tmp_path)) == str(claude_brain.os.path.join("src", "a.py"))
    assert claude_brain._tool_detail("Read", {"file_path": outside}, str(tmp_path)) == outside
    assert claude_brain._tool_detail("Bash", {"command": "pytest -q"}, str(tmp_path)) == "pytest -q"


# ---------- 权限：PermissionGate ----------

class Gate:
    """PermissionGate + 它发出的事件。"""

    def __init__(self, workspace):
        self.events = []
        self.gate = claude_brain.PermissionGate(workspace, self.events.append)


def hook(gate, tool, tool_input):
    output = anyio.run(gate.pre_tool_use, {"tool_name": tool, "tool_input": tool_input}, "toolu_1", {"signal": None})
    return output["hookSpecificOutput"]


def test_hook_decides_every_tool(tmp_path):
    gate = Gate(tmp_path).gate
    assert hook(gate, "Read", {"file_path": str(tmp_path / "a.py")})["permissionDecision"] == "allow"
    assert hook(gate, "WebSearch", {"query": "x"})["permissionDecision"] == "allow"
    assert hook(gate, "Bash", {"command": "pytest"})["permissionDecision"] == "ask"   # → can_use_tool
    denied = hook(gate, "Task", {})
    assert denied["permissionDecision"] == "deny" and denied["permissionDecisionReason"]


def ask_and_answer(gate, tool, tool_input, reply_for):
    """can_use_tool 发出确认请求后，按 reply_for(请求) 回答；返回 (结果, 收到的请求)。"""
    async def main():
        task = asyncio.create_task(gate.gate.can_use_tool(tool, tool_input, None))
        for _ in range(100):
            await asyncio.sleep(0)
            if gate.events:
                break
        requests = list(gate.events)
        for request in requests:
            gate.gate.reply(reply_for(request))
        return await asyncio.wait_for(task, 2), requests
    return asyncio.run(main())


def test_allow_once(tmp_path):
    gate = Gate(tmp_path)
    result_, requests = ask_and_answer(gate, "Bash", {"command": "pytest -q"},
                                       lambda r: protocol.PermissionReply(r.request_id, True))
    assert result_.behavior == "allow"
    assert requests == [protocol.PermissionRequest(1, "Bash", {"command": "pytest -q"}, "command", True, "pytest", False)]
    assert gate.gate.grants == set() and gate.gate.waiting == 0
    assert gate.gate.decide("Bash", {"command": "pytest"}).action == "ask"            # 只允许了这一次


def test_allow_for_the_whole_chat(tmp_path):
    gate = Gate(tmp_path)
    result_, _ = ask_and_answer(gate, "Bash", {"command": "pytest -q"},
                                lambda r: protocol.PermissionReply(r.request_id, True, remember=True))
    assert result_.behavior == "allow" and gate.gate.grants == {("command", "pytest")}
    assert hook(gate.gate, "Bash", {"command": "pytest tests"})["permissionDecision"] == "allow"   # 不再问


def test_remember_is_ignored_when_not_offered(tmp_path):
    gate = Gate(tmp_path)
    _, requests = ask_and_answer(gate, "Bash", {"command": "rm -rf build"},
                                 lambda r: protocol.PermissionReply(r.request_id, True, remember=True))
    assert requests[0].danger and not requests[0].can_remember
    assert gate.gate.grants == set()                                                   # 危险命令不会被"都允许"


def test_deny_with_a_reason(tmp_path):
    gate = Gate(tmp_path)
    result_, _ = ask_and_answer(gate, "Edit", {"file_path": str(tmp_path / "a.py")},
                                lambda r: protocol.PermissionReply(r.request_id, False, "先别改"))
    assert result_.behavior == "deny" and "denied" in result_.message and "先别改" in result_.message
    assert not result_.interrupt                                                       # Claude 接着说话（问你想怎么做）


def test_stopping_denies_everything_waiting(tmp_path):
    gate = Gate(tmp_path)

    async def main():
        tasks = [asyncio.create_task(gate.gate.can_use_tool("Bash", {"command": c}, None)) for c in ("a", "b")]
        while gate.gate.waiting < 2:
            await asyncio.sleep(0)
        gate.gate.deny_all(claude_brain.PermissionGate.STOPPED)
        return await asyncio.wait_for(asyncio.gather(*tasks), 2)

    results = asyncio.run(main())
    assert [r.behavior for r in results] == ["deny", "deny"] and gate.gate.waiting == 0
    gate.gate.reply(protocol.PermissionReply(1, True))                                 # 迟到的答复：不理，不出错


def test_grant_given_while_another_request_waits_applies(tmp_path):
    """两个同样的命令一起等确认，第一个选了"都允许"，第二个再看一次规则就直接放行。"""
    gate = Gate(tmp_path)
    gate.gate.grants.add(("command", "pytest"))
    result_ = asyncio.run(gate.gate.can_use_tool("Bash", {"command": "pytest -q"}, None))
    assert result_.behavior == "allow"
    assert gate.events == [protocol.ToolDecision("", "Bash", "granted", "pytest -q")]   # 没问，只记一笔


# ---------- 操作记录：每次工具调用最后怎么定的 ----------

class Context:
    def __init__(self, tool_use_id):
        self.tool_use_id = tool_use_id


def decisions(gate):
    return [(e.tool_id, e.decision, e.detail) for e in gate.events if isinstance(e, protocol.ToolDecision)]


def test_hook_records_what_it_decided_without_asking(tmp_path):
    gate = Gate(tmp_path)
    anyio.run(gate.gate.pre_tool_use, {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "a.py")}},
              "t1", {})
    anyio.run(gate.gate.pre_tool_use, {"tool_name": "Task", "tool_input": {}}, "t2", {})
    anyio.run(gate.gate.pre_tool_use, {"tool_name": "Bash", "tool_input": {"command": "pytest"}}, "t3", {})
    gate.gate.grants.add(("command", "pytest"))
    anyio.run(gate.gate.pre_tool_use, {"tool_name": "Bash", "tool_input": {"command": "pytest -q"}}, "t4", {})
    assert decisions(gate) == [("t1", "auto", "a.py"), ("t2", "blocked", ""), ("t4", "granted", "pytest -q")]
    # t3 要问：问完了才记（见下面）


@pytest.mark.parametrize("reply, decision", [
    (dict(allow=True), "allowed"),
    (dict(allow=True, remember=True), "remembered"),
    (dict(allow=False, reason="不要"), "denied"),
])
def test_answer_is_recorded(tmp_path, reply, decision):
    gate = Gate(tmp_path)

    async def main():
        task = asyncio.create_task(gate.gate.can_use_tool("Bash", {"command": "pytest -q"}, Context("t9")))
        while not gate.events:
            await asyncio.sleep(0)
        assert gate.events[0].tool_id == "t9"                      # 确认请求带着是哪一次工具调用
        gate.gate.reply(protocol.PermissionReply(gate.events[0].request_id, **reply))
        await task

    asyncio.run(main())
    assert decisions(gate) == [("t9", decision, "pytest -q")]


def test_stopping_is_recorded_as_stopped(tmp_path):
    gate = Gate(tmp_path)

    async def main():
        task = asyncio.create_task(gate.gate.can_use_tool("Bash", {"command": "pytest"}, Context("t1")))
        while not gate.gate.waiting:
            await asyncio.sleep(0)
        gate.gate.deny_all(claude_brain.PermissionGate.STOPPED)
        await task
        await gate.gate.can_use_tool("Bash", {"command": "late"}, Context("t2"))   # 停下之后迟到的

    asyncio.run(main())
    assert [d[:2] for d in decisions(gate)] == [("t1", "stopped"), ("t2", "stopped")]


@pytest.mark.parametrize("how", ["interrupt", "close"])
def test_stop_and_close_deny_waiting_and_end_commands(monkeypatch, tmp_path, how):
    """停止 / 关闭：等着的确认算拒绝；先记下正在跑的命令 → 让 Claude Code 停 → 再结束还在跑的命令。"""
    calls = []

    class StopClient:
        async def interrupt(self):
            calls.append("interrupt")

        async def disconnect(self):
            calls.append("disconnect")

    monkeypatch.setattr(claude_brain.proctree, "before_stop", lambda: calls.append("before") or ["running"])
    monkeypatch.setattr(claude_brain.proctree, "stop_commands", lambda running: calls.append(("end", running)) or 1)
    brain = claude_brain.ClaudeBrain(AssistantSettings(workspace=str(tmp_path)), "k", "zh", lambda e: None)
    brain._client = StopClient()

    async def main():
        task = asyncio.create_task(brain.gate.can_use_tool("Bash", {"command": "pytest"}, Context("t1")))
        while not brain.gate.waiting:
            await asyncio.sleep(0)
        await getattr(brain, how)()
        return await task

    assert asyncio.run(main()).behavior == "deny"
    assert calls == ["before", "interrupt" if how == "interrupt" else "disconnect", ("end", ["running"])]


def test_long_commands_are_recorded_in_full_up_to_a_limit(tmp_path):
    gate = Gate(tmp_path)
    gate.gate.grants.add(("command", "echo"))
    command = "echo " + "x" * 1000
    anyio.run(gate.gate.pre_tool_use, {"tool_name": "PowerShell", "tool_input": {"command": command}}, "t1", {})
    _, decision, detail = decisions(gate)[-1]
    assert decision == "granted" and detail == command[:500]        # 命令记全文（进度行里只有开头 120 个字）


def test_stopped_turn_without_reported_cost_uses_the_estimate():
    """中途停下时 Claude Code 报的花费是 0，可 token 一样计费：用芸芸自己的估算记账（2026-10-07 实测）。"""
    state = TurnState(prices=(1, 5), reply_budget=1.0)
    translate(usage_event("start", {"input_tokens": 10000}), state)   # 估算 $0.01
    state.interrupted = True
    finished = translate(result("error_during_execution", total=0.0, is_error=True), state)[-1]
    assert finished.cost_usd == pytest.approx(0.01) and finished.session_cost_usd == pytest.approx(0.01)
    state.new_turn()
    translate(usage_event("start", {"input_tokens": 3000}), state)
    finished = translate(result("success", total=0.003), state)[-1]   # 下一轮：Claude Code 的总数里没有上一轮
    assert finished.cost_usd == pytest.approx(0.003) and finished.session_cost_usd == pytest.approx(0.013)
