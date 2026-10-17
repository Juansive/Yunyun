"""评测里用 Claude（E5）：走 Anthropic 官方 SDK（anthropic），接口和 run.py 的 LLM 一样。

mail_agent / systems 说的是 OpenAI 格式的对话（system 消息、assistant 的 tool_calls、role=tool 的结果），
这里换成 Messages API 的格式：system 单独放；工具调用是 tool_use 块；连着的几个工具结果合成一条 user 消息里的 tool_result 块。
只在评测脚本里用——芸芸进程从不 import anthropic。Key 用助手模式存的（凭据管理器 yunyun-assistant / anthropic）。
"""
import json

import anthropic

from yunyun.ai.client import AIError, Reply, ToolCall, count_usage
from yunyun.ai.summarizer import TEMPERATURE

SUMMARIZER_MAX_TOKENS = 200   # 和芸芸里一样（providers.Provider.max_tokens）


class AnthropicLLM:
    def __init__(self, model, api_key, timeout=120):
        self.model, self.usage = model, {}
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=3)   # SDK 自己重试 429 / 5xx

    def __call__(self, messages, max_tokens=None):
        return self.reply(messages, None, max_tokens or SUMMARIZER_MAX_TOKENS).text

    def reply(self, messages, tools, max_tokens):
        system, converted = to_anthropic(messages)
        # SDK 1.x 的参数里没有 temperature 了（新模型不收）；Haiku 4.5 的接口还认，和别的模型一样设 0.3
        request = {"model": self.model, "max_tokens": max_tokens, "messages": converted, "extra_body": {"temperature": TEMPERATURE},
                   **({"system": system} if system else {}), **({"tools": to_anthropic_tools(tools)} if tools else {})}
        try:
            response = self.client.messages.create(**request)
        except anthropic.AuthenticationError as e:
            raise AIError("Key 不对", kind="auth", detail=str(e), status=401) from e
        except anthropic.RateLimitError as e:
            raise AIError("限流", kind="rate_limit", detail=str(e), status=429) from e
        except anthropic.APIStatusError as e:
            raise AIError(f"HTTP {e.status_code}", kind="other", detail=str(e)[:300], status=e.status_code) from e
        except anthropic.APIConnectionError as e:
            raise AIError("网络错误", kind="network", detail=str(e)) from e
        return from_anthropic(response, self.usage)

    def tool_result(self, call, content):
        return {"role": "tool", "tool_call_id": call.id, "content": content}


def to_anthropic_tools(tools):
    return [{"name": t["function"]["name"], "description": t["function"]["description"],
             "input_schema": t["function"]["parameters"]} for t in tools]


def to_anthropic(messages):
    """OpenAI 格式的对话 → (system 文字, Messages API 的 messages)。"""
    system, out = [], []
    for m in messages:
        if m["role"] == "system":
            system.append(m["content"])
        elif m["role"] == "tool":
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
            last = out[-1] if out else None
            if last and last["role"] == "user" and isinstance(last["content"], list):
                last["content"].append(block)      # 同一轮的几个结果放在一条消息里
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant":
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            blocks += [{"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"] or "{}")} for c in m.get("tool_calls") or []]
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "（空）"}]})
        else:
            out.append({"role": "user", "content": m["content"]})
    return "\n\n".join(system), out


def from_anthropic(response, usage=None):
    """Messages API 的回答 → client.Reply（message 是 OpenAI 格式，接着问时原样放回对话）。"""
    count_usage(usage, response.usage.input_tokens, response.usage.output_tokens)
    text = "".join(b.text for b in response.content if b.type == "text")
    calls = [ToolCall(b.id, b.name, b.input if isinstance(b.input, dict) else None, json.dumps(b.input, ensure_ascii=False))
             for b in response.content if b.type == "tool_use"]
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw}}
                                 for c in calls]
    return Reply(text, calls, message, response.stop_reason == "max_tokens")
