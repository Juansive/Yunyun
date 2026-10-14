"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"翻回忆"（阶段 11.4）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_recall.py

先放两段回忆（一段的对话记录还在），然后：
  1. 系统提示词里有没有最近几段的摘要、"翻回忆"的工具有没有交给模型；
  2. 模型按词查（recall_chats query）→ 不用问、找到那一段；
  3. 模型按对话编号看原话（recall_chats chat_id）→ 不用问、交回当时的问答。
"""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import claude_brain, protocol, recollections

TOOL = recollections.RECALL_CHATS_TOOL


class RecallApi(FakeApi):
    def __init__(self, steps):
        super().__init__(steps)
        self.systems = []

    def handle(self, path, body):
        system = json.dumps(body.get("system", ""), ensure_ascii=False)
        if "芸芸" in system:
            self.systems.append(system)
        return super().handle(path, body)


def write_chat(session_id, question, answer):
    folder = claude_brain.CONFIG_DIR / "projects" / "old-project"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "user", "sessionId": session_id, "cwd": "", "timestamp": "2026-10-12T10:00:00Z",
              "promptSource": "sdk", "message": {"role": "user", "content": question}},
             {"type": "assistant", "sessionId": session_id, "cwd": "", "timestamp": "2026-10-12T10:00:05Z",
              "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]}}]
    (folder / f"{session_id}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                                encoding="utf-8")


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-recall-"))
    api = RecallApi([("tool", TOOL, {"query": "答辩"}), ("tool", TOOL, {"chat_id": "thesis-1"}), ("text", "想起来了")])
    server = serve(api)
    events = []
    brain = make_brain(server, tmp, "", events.append)   # 这里才把数据目录（回忆、对话记录）换到临时文件夹
    recollections.remember("thesis-1", "2026-10-12T10:00:00", "聊答辩准备，导师要求周五交初稿", ["答辩", "论文"])
    recollections.remember("pytest-2", "2026-10-13T10:00:00", "修好 pytest 的报错", ["pytest"])
    write_chat("thesis-1", "我下周要答辩，好紧张", "别紧张，我们先列个提纲吧。")
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("我上次说的答辩那件事，你还记得吗？"), 60)
        system = api.systems[0] if api.systems else ""
        decisions = [(e.tool.split("__")[-1], e.decision) for e in events if isinstance(e, protocol.ToolDecision)]
        found, said = api.result_of(0)[1], api.result_of(1)[1]
        print("1. 提示词里的摘要：", [s for s in ("2026-10-13 修好 pytest 的报错", "2026-10-12 聊答辩准备") if s in system],
              "；工具：", TOOL in (api.offered_tools or []))
        print("2. 按词查：", found[:80])
        print("3. 看原话：", said[:120].replace("\n", " | "))
        print("   操作记录：", decisions)
        checks["提示词和工具"] = "2026-10-12 聊答辩准备" in system and TOOL in (api.offered_tools or [])
        checks["按词查"] = "聊答辩准备" in found and "pytest" not in found
        checks["看原话"] = "我下周要答辩" in said and "先列个提纲" in said
        checks["不用问"] = decisions == [("recall_chats", "auto"), ("recall_chats", "auto")]
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 翻回忆能用（摘要进提示词、按词查、看原话，都不用问）" if all(checks.values()) and len(checks) == 4
          else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
