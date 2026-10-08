"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"压缩对话"（compact）怎么表现。不花钱。

    .\\.venv\\Scripts\\python tools\\try_compact.py

  1. 手动：问一句 → 发 "/compact" → 再问一句。压缩时 Claude Code 发了什么请求？SDK 传回什么消息？
     压缩之后的请求里，前面的对话还在不在（换成了总结）？
  2. 自动：CLAUDE_CODE_AUTO_COMPACT_WINDOW 设小一点，假接口每次报很多输入 token → 到多少会自动压缩？
"""
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import claude_brain, protocol

SUMMARY = "<analysis>略</analysis>\n<summary>用户让我记住数字 42。</summary>"


class CompactApi(FakeApi):
    """主循环照剧本；看起来是"总结对话"的请求回一段总结，并记下它长什么样。"""

    def __init__(self, steps, usage=None):
        super().__init__(steps, usage)
        self.compacts = []    # 压缩请求：(模型, 消息条数, 系统提示词里有没有芸芸, 最后一条的开头)
        self.main_sizes = []  # 每次主循环请求：消息条数

    def handle(self, path, body):
        messages = body.get("messages") or []
        last = messages[-1] if messages else {}
        content = last.get("content")
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
        if "/v1/messages" in path and not path.endswith("/count_tokens") and "Respond with TEXT ONLY" in text:
            system = json.dumps(body.get("system", ""), ensure_ascii=False)
            self.compacts.append((body.get("model"), len(messages), "芸芸" in system, text[:120].replace("\n", " ")))
            model = body.get("model") or "claude-haiku-4-5"
            return (self._message([{"type": "text", "text": SUMMARY}], "end_turn", model,
                                  {"input_tokens": 10, "output_tokens": 5}), bool(body.get("stream")))
        if "/v1/messages" in path and "芸芸" in json.dumps(body.get("system", ""), ensure_ascii=False):
            self.main_sizes.append(len(messages))
        return super().handle(path, body)


def spy_messages(raw):
    """记下 SDK 传回的每条消息（流式的小片段不记）。"""
    original = claude_brain.translate

    def spy(message, state):
        name = type(message).__name__
        if name != "StreamEvent":
            data = getattr(message, "data", None) if name == "SystemMessage" else None
            raw.append((name, getattr(message, "subtype", None), sorted(data) if isinstance(data, dict) else None))
            if name == "SystemMessage" and isinstance(data, dict) and "compact" in json.dumps(data):
                detail = {k: v for k, v in data.items() if "compact" in k}
                raw.append(("  ↳", json.dumps(detail, ensure_ascii=False)[:300], None))
        return original(message, state)
    claude_brain.translate = spy


def with_env(extra):
    original = claude_brain.build_options

    def patched(*args, **kwargs):
        options = original(*args, **kwargs)
        options.env.update(extra)
        return options
    claude_brain.build_options = patched
    return original


async def conversation(server, tmp, questions):
    events, results = [], []
    brain = make_brain(server, tmp, "", events.append)
    try:
        await asyncio.wait_for(brain.start(), 60)
        for question in questions:
            before = len(events)
            await asyncio.wait_for(brain.ask(question), 90)
            finished = next((e for e in events[before:] if isinstance(e, protocol.TurnFinished)), None)
            errors = [e for e in events[before:] if isinstance(e, protocol.Error)]
            results.append((question, finished, errors))
    finally:
        await brain.close()
    return results


def show(results, api, raw):
    for question, finished, errors in results:
        print(f"  问「{question[:20]}」：花费 {finished.cost_usd if finished else None}，"
              f"输入 token {finished.input_tokens if finished else None}，出错 {[(e.kind, e.detail[:80]) for e in errors]}")
    print("  主循环每次请求的消息条数：", api.main_sizes)
    print("  每次主循环请求带着的提问：", [p[-3:] for p in api.prompts_seen])
    print("  压缩请求：", api.compacts or "没有")
    print("  别的请求：", api.others)
    print("  和压缩有关的 SDK 消息：", [r for r in raw if "compact" in str(r)] or "没有")


async def main():
    raw = []
    spy_messages(raw)

    print("1. 手动 /compact")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-compact-"))
    api = CompactApi([("text", "好的，记住了 42"), ("text", "压缩后：是 42")])
    server = serve(api)
    try:
        results = await conversation(server, tmp, ["第一问：记住 42", "/compact", "第二问：刚才的数字是几？"])
        show(results, api, raw)
        manual_ok = bool(api.compacts) and not any("第一问" in p for p in api.prompts_seen[-1])
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    auto_ok = False
    # 窗口:百分比:假接口每次报的输入 token:每一问后面加多少字的填充（百分比空 = 不设）
    for arg in sys.argv[1:] or ["100000:30:40000:8000"]:
        window, pct, tokens, filler = arg.split(":")
        env = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": window, **({"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": pct} if pct else {})}
        print(f"\n2. 自动压缩：{env}，假接口每次报输入 {tokens} token")
        raw.clear()
        original = with_env(env)
        tmp = Path(tempfile.mkdtemp(prefix="yunyun-compact-"))
        api = CompactApi([("text", f"第 {i} 答") for i in range(1, 10)],
                         usage={"input_tokens": int(tokens), "output_tokens": 5})
        server = serve(api)
        try:
            results = await conversation(server, tmp, [f"第 {i} 问" + "。填充" * (int(filler) // 3)
                                                       for i in range(1, 9)])
            show(results, api, raw)
            auto_ok = auto_ok or bool(api.compacts)
        finally:
            server.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)
            claude_brain.build_options = original

    print("\n结论：", "✓ 手动压缩有效（之后的请求里没有原来的提问）" if manual_ok else "✗ 手动压缩没生效",
          "；自动压缩" + ("触发了" if auto_ok else "没触发"))


if __name__ == "__main__":
    asyncio.run(main())
