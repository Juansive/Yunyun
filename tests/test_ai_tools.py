"""ai/client.py 的工具调用（邮件 agent 用，E3）：OpenAI 兼容接口和 Ollama 两种格式。

用本机的假服务器（fake_llm_server.py）：真的发 HTTP 请求，回答按剧本给，不联网、不需要 Key。
"""
from dataclasses import replace

import pytest

from fake_llm_server import FakeLLMServer
from yunyun.ai import client
from yunyun.ai.client import AIError, ToolCall
from yunyun.ai.providers import PROVIDERS, custom_provider

TOOLS = [{"type": "function", "function": {
    "name": "suggest_reminder", "description": "建议一个提醒（用户点了才加）",
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "when": {"type": "string"}},
                   "required": ["text", "when"]}}}]
QUESTION = [{"role": "system", "content": "你是邮件助手"}, {"role": "user", "content": "周三下午 2 点组会"}]


def openai_reply(content="", tool_calls=None, finish="stop", usage=None):
    message = {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}
    return {"choices": [{"message": message, "finish_reason": finish}], **({"usage": usage} if usage else {})}


def ollama_reply(content="", tool_calls=None, done="stop"):
    message = {"role": "assistant", "content": content, **({"tool_calls": tool_calls} if tool_calls else {})}
    return {"message": message, "done": True, "done_reason": done, "prompt_eval_count": 50, "eval_count": 7}


def test_openai_tool_round_trip():
    """一来一回：模型要调用工具 → 把结果交回去 → 模型给出回答。第二次请求里要带上它的调用和结果，格式对得上。"""
    call = {"id": "call_abc", "type": "function",
            "function": {"name": "suggest_reminder", "arguments": '{"text": "组会", "when": "2026-10-21 13:00"}'}}
    with FakeLLMServer([openai_reply(tool_calls=[call], finish="tool_calls", usage={"prompt_tokens": 90}),
                        openai_reply("好啦，建议加一个组会提醒", usage={"prompt_tokens": 120, "completion_tokens": 9})]) as server:
        provider, usage = custom_provider(server.url + "/v1"), {}
        first = client.ask_reply(provider, "k-test", "m1", QUESTION, tools=TOOLS, max_tokens=300, usage=usage)
        assert first.text == "" and first.tool_calls == [
            ToolCall("call_abc", "suggest_reminder", {"text": "组会", "when": "2026-10-21 13:00"}, call["function"]["arguments"])]
        messages = QUESTION + [first.message, client.tool_result(provider, first.tool_calls[0], "已记下建议")]
        second = client.ask_reply(provider, "k-test", "m1", messages, tools=TOOLS, usage=usage)
    assert second.text == "好啦，建议加一个组会提醒" and second.tool_calls == []
    sent = [r["body"] for r in server.requests]
    assert server.requests[0]["path"] == "/v1/chat/completions"
    assert server.requests[0]["headers"]["Authorization"] == "Bearer k-test"
    assert sent[0]["tools"] == TOOLS and sent[0]["max_tokens"] == 300
    assert sent[1]["messages"][-2] == {"role": "assistant", "content": "", "tool_calls": [call]}   # 参数原样（字符串）带回去
    assert sent[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_abc", "content": "已记下建议"}
    assert usage == {"input": 210, "output": 9, "calls": 2}


def test_ollama_tool_round_trip():
    call = {"function": {"name": "suggest_reminder", "arguments": {"text": "组会", "when": "2026-10-21 13:00"}}}
    with FakeLLMServer([ollama_reply(tool_calls=[call]), ollama_reply("好啦")]) as server:
        provider = replace(PROVIDERS["ollama"], base_url=server.url)
        first = client.ask_reply(provider, "", "qwen3:4b", QUESTION, tools=TOOLS, max_tokens=300)
        assert first.tool_calls == [ToolCall("call_0", "suggest_reminder", {"text": "组会", "when": "2026-10-21 13:00"},
                                             '{"text": "组会", "when": "2026-10-21 13:00"}')]   # 没有 id：按顺序编
        messages = QUESTION + [first.message, client.tool_result(provider, first.tool_calls[0], "已记下建议")]
        assert client.ask_reply(provider, "", "qwen3:4b", messages, tools=TOOLS).text == "好啦"
    sent = [r["body"] for r in server.requests]
    assert server.requests[0]["path"] == "/api/chat" and "Authorization" not in server.requests[0]["headers"]
    assert sent[0]["tools"] == TOOLS and sent[0]["think"] is False and sent[0]["options"]["num_predict"] == 300
    assert sent[1]["messages"][-2] == {"role": "assistant", "content": "", "tool_calls": [call]}   # 参数是对象
    assert sent[1]["messages"][-1] == {"role": "tool", "content": "已记下建议", "tool_name": "suggest_reminder"}


def test_several_calls_and_odd_arguments():
    """一次回答里好几个调用；参数写坏了是 None（交给调用方告诉模型），空的是 {}，个别兼容接口直接给对象也认。"""
    calls = [{"id": "a", "function": {"name": "today", "arguments": ""}},
             {"id": "b", "function": {"name": "suggest_reminder", "arguments": '{"text": "组会",'}},
             {"function": {"name": "suggest_reminder", "arguments": {"text": "讲座"}}},
             {"id": "d", "function": {"name": "x", "arguments": "[1, 2]"}},
             "不是调用", {"id": "e"}]
    with FakeLLMServer([openai_reply("先看看今天几号", tool_calls=calls)]) as server:
        reply = client.ask_reply(custom_provider(server.url), "", "m", QUESTION, tools=TOOLS)
    assert reply.text == "先看看今天几号"
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("a", "today", {}), ("b", "suggest_reminder", None), ("call_2", "suggest_reminder", {"text": "讲座"}), ("d", "x", None)]
    assert reply.tool_calls[1].raw == '{"text": "组会",'
    assert [c["function"]["arguments"] for c in reply.message["tool_calls"]] == ["", '{"text": "组会",', '{"text": "讲座"}', "[1, 2]"]


def test_truncated_replies_are_marked():
    with FakeLLMServer([openai_reply("写到一半", finish="length"), ollama_reply("写到一半", done="length")]) as server:
        assert client.ask_reply(custom_provider(server.url), "", "m", QUESTION).truncated
        assert client.ask_reply(replace(PROVIDERS["ollama"], base_url=server.url), "", "q", QUESTION).truncated


@pytest.mark.parametrize("ollama, error", [
    (False, {"error": {"message": "Function calling is not supported for this model"}}),
    (True, {"error": "registry.ollama.ai/library/gemma3:1b does not support tools"}),
])
def test_models_without_tools_say_so(ollama, error):
    with FakeLLMServer([(400, error), (400, error)]) as server:
        provider = replace(PROVIDERS["ollama"], base_url=server.url) if ollama else custom_provider(server.url)
        with pytest.raises(AIError) as info:
            client.ask_reply(provider, "", "m", QUESTION, tools=TOOLS)
        assert info.value.kind == "no_tools"
        with pytest.raises(AIError) as info:            # 没发工具时还是普通的错误
            client.ask_reply(provider, "", "m", QUESTION)
        assert info.value.kind == "other"


def test_plain_ask_is_unchanged():
    """不带工具的 ask() 照旧只要文字；请求里没有 tools。"""
    with FakeLLMServer([openai_reply('{"bubble": "好"}')]) as server:
        assert client.ask(custom_provider(server.url), "", "m", QUESTION) == '{"bubble": "好"}'
    assert "tools" not in server.requests[0]["body"] and server.requests[0]["body"]["max_tokens"] == 1000
