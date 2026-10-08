"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"对话太长时自动整理"（阶段 10，Claude Code 的 /compact）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_compact.py                   # 芸芸自动整理
    .\\.venv\\Scripts\\python tools\\try_compact.py 100000:30:40000:8000   # 另外看 Claude Code 自带的自动压缩

  1. 芸芸自动整理：门槛 2 万，假接口每次报 3 万输入 token → 第一问答完就整理（Compacting / Compacted）；
     第二问的请求里，第一问还在不在（应该换成了总结）？整理的花费有没有算进这段对话？
  2. （给了参数才做）Claude Code 自带的自动压缩：参数是 窗口:百分比:每次报的输入 token:每一问加多少字。
     2026-10-08 实测：窗口最小 10 万（代码里写死）；加百分比能让它提早尝试，但假对话里一直 too_few_groups。
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
    """主循环照剧本；"总结对话"的请求（Claude Code 的整理）回一段总结，并记下它长什么样。"""

    def __init__(self, steps, usage=None):
        super().__init__(steps, usage)
        self.compacts = []    # 整理请求：(模型, 消息条数, 系统提示词里有没有芸芸, 最后一条的开头)

    def handle(self, path, body):
        messages = body.get("messages") or []
        last = messages[-1] if messages else {}
        content = last.get("content")
        text = content if isinstance(content, str) else " ".join(
            b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
        if "/v1/messages" in path and not path.endswith("/count_tokens") and "Respond with TEXT ONLY" in text:
            system = json.dumps(body.get("system", ""), ensure_ascii=False)
            self.compacts.append((body.get("model"), len(messages), "芸芸" in system, text[:80].replace("\n", " ")))
            model = body.get("model") or "claude-haiku-4-5"
            return (self._message([{"type": "text", "text": SUMMARY}], "end_turn", model,
                                  {"input_tokens": 10, "output_tokens": 5}), bool(body.get("stream")))
        return super().handle(path, body)


async def conversation(server, tmp, questions, compact_tokens=0):
    events = []
    brain = make_brain(server, tmp, "", events.append, compact_tokens=compact_tokens)
    try:
        await asyncio.wait_for(brain.start(), 60)
        for question in questions:
            await asyncio.wait_for(brain.ask(question), 90)
    finally:
        await brain.close()
    return events


def brief(event):
    if isinstance(event, protocol.TurnFinished):
        return (f"TurnFinished(花费 {event.cost_usd}, 共 {event.session_cost_usd}, "
                f"对话 {event.context_tokens} / {event.context_limit})")
    if isinstance(event, protocol.Compacted):
        return (f"Compacted(ok={event.ok}, {event.before} → {event.after}, 花费 {event.cost_usd}, "
                f"共 {event.session_cost_usd}, {event.detail!r})")
    return type(event).__name__ + (f"({event.kind}: {event.detail[:80]})" if isinstance(event, protocol.Error) else "")


async def yunyun_compacts():
    print("1. 芸芸自动整理：门槛 2 万，假接口每次报输入 30000 token")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-compact-"))
    api = CompactApi([("text", "好的，记住了 42"), ("text", "整理后：是 42")],
                     usage={"input_tokens": 30000, "output_tokens": 5})
    server = serve(api)
    try:
        events = await conversation(server, tmp, ["第一问：记住 42", "第二问：刚才的数字是几？"], compact_tokens=20000)
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    shown = [brief(e) for e in events if not isinstance(e, (protocol.Ready, protocol.TextDelta))]
    print("  事件：\n   ", "\n    ".join(shown))
    print("  整理请求：", api.compacts or "没有")
    print("  第二问的请求带着的提问：", [p[:40] for p in api.prompts_seen[-1]] if api.prompts_seen else None)
    compacted = [e for e in events if isinstance(e, protocol.Compacted)]
    finished = [e for e in events if isinstance(e, protocol.TurnFinished)]
    return (bool(compacted) and compacted[0].ok and compacted[0].cost_usd is not None
            and not any("第一问" in p for p in api.prompts_seen[-1])
            and len(finished) == 2 and finished[1].session_cost_usd > finished[0].session_cost_usd + finished[1].cost_usd
            - 1e-9)


def with_env(extra):
    original = claude_brain.build_options

    def patched(*args, **kwargs):
        options = original(*args, **kwargs)
        options.env.update(extra)
        return options
    claude_brain.build_options = patched
    return original


async def claude_code_auto(arg):
    window, pct, tokens, filler = arg.split(":")
    env = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": window, **({"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": pct} if pct else {})}
    print(f"\n2. Claude Code 自带的自动压缩：{env}，假接口每次报输入 {tokens} token")
    seen = []
    original_translate = claude_brain.translate

    def spy(message, state):
        data = getattr(message, "data", None)
        if type(message).__name__ == "SystemMessage" and isinstance(data, dict) and "compact" in json.dumps(data):
            seen.append({k: v for k, v in data.items() if "compact" in k})
        return original_translate(message, state)
    claude_brain.translate = spy
    original = with_env(env)
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-compact-"))
    api = CompactApi([("text", f"第 {i} 答") for i in range(1, 10)], usage={"input_tokens": int(tokens), "output_tokens": 5})
    server = serve(api)
    try:
        await conversation(server, tmp, [f"第 {i} 问" + "。填充" * (int(filler) // 3) for i in range(1, 9)])
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
        claude_brain.build_options = original
        claude_brain.translate = original_translate
    print("  和压缩有关的 SDK 消息：", seen or "没有")
    print("  整理请求：", api.compacts or "没有")


async def after_compaction():
    """整理之后：撤销整理之前那一问的改动、历史记录读出来对不对、接着聊能不能接上。"""
    print("\n3. 整理之后：撤销、历史记录、接着聊")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-compact-"))
    proj = tmp / "proj"
    proj.mkdir()
    hello = proj / "hello.py"
    hello.write_text("print('hi')\n", encoding="utf-8")
    api = CompactApi([("tool", "Read", {"file_path": str(hello)}),
                      ("tool", "Edit", {"file_path": str(hello), "old_string": "print('hi')",
                                        "new_string": "print('hello')"}),
                      ("text", "改好了"), ("text", "第 2 答"), ("text", "接着聊：还记得")],
                     usage={"input_tokens": 30000, "output_tokens": 5})
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, brain = [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))

    try:
        brain = make_brain(server, tmp, proj, emit, compact_tokens=20000)
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("第 1 问：改 hello"), 90)
        await asyncio.wait_for(brain.ask("第 2 问"), 90)
        first = next(e for e in events if isinstance(e, protocol.TurnFinished))
        await brain.rewind(first.checkpoint)
        await brain.close()
        rewound = next((e for e in events if isinstance(e, protocol.Rewound)), None)
        undone = hello.read_text(encoding="utf-8").strip() == "print('hi')"
        print(f"  整理了 {len([e for e in events if isinstance(e, protocol.Compacted) and e.ok])} 次；"
              f"撤销第 1 问：{rewound.ok if rewound else None}（{rewound.detail[:60] if rewound else ''}），"
              f"hello.py 现在是 {hello.read_text(encoding='utf-8').strip()!r}")

        from yunyun.assistant import transcripts
        path = next((tmp / "claude" / "projects").glob(f"*/{first.session_id}.jsonl"))
        print("  记录文件里每一行（只看结构）：")
        for line in path.read_text(encoding="utf-8").splitlines():
            data = json.loads(line)
            content = (data.get("message") or {}).get("content") if isinstance(data.get("message"), dict) else None
            head = content[:50] if isinstance(content, str) else [b.get("type") for b in content or []
                                                                   if isinstance(b, dict)]
            flags = [k for k in ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly", "promptSource",
                                 "subtype", "compactMetadata") if data.get(k)]
            print(f"    {data.get('type')}: {flags} {head!r}".replace("\n", " ")[:150])
        transcript = transcripts.read_transcript(str(path))
        questions = [t.question[:30] for t in transcript.turns] if transcript else None
        print("  历史记录读出来的提问：", questions)
        history_ok = questions == ["第 1 问：改 hello", "第 2 问"]

        events.clear()
        brain = make_brain(server, tmp, proj, emit, resume=first.session_id, compact_tokens=0)
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("第 3 问：接着聊"), 90)
        print("  接着聊时交给模型的提问：", [p[:40] for p in api.prompts_seen[-1]])
        resumed = any("continued from a previous conversation" in p for p in api.prompts_seen[-1])
    finally:
        if brain is not None:
            await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("  结论：", "撤销 ✓" if undone else "撤销 ✗", "；历史记录 " + ("✓" if history_ok else "✗（要过滤）"),
          "；接着聊 " + ("✓ 带着总结" if resumed else "✗"))
    return undone and history_ok and resumed


async def main():
    ok = await yunyun_compacts()
    later_ok = await after_compaction()
    for arg in sys.argv[1:]:
        await claude_code_auto(arg)
    if ok and later_ok:
        print("\n结论： ✓ 自动整理有效（之后不再带着前文，花费算进了这段对话）；整理之后撤销、历史记录、接着聊都正常")
    else:
        print("\n结论： ✗", "自动整理不对（看第 1 段）" if not ok else "整理之后撤销 / 历史记录 / 接着聊有问题（看第 3 段）")


if __name__ == "__main__":
    asyncio.run(main())
