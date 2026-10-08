"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证芸芸的记事本（阶段 10 的 B，芸芸自己的工具 = 进程内 MCP）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_notebook.py

  1. 在项目 proj 里："记住我喜欢简短的回复；这个项目测试用 unittest" → 模型调用两次 remember（关于你 / 这个项目）。
     工具出现在发给模型的工具列表里吗？过不过芸芸的权限关卡（不用问）？记事本里有没有这两条？这一轮报"记了 2 条"吗？
  2. 新开一段对话（还是 proj）：系统提示词里有没有目录？recall 读细节、forget 删掉"关于你"那一条。
  3. 换到另一个项目 other：目录里没有 proj 的记忆；recall / forget 碰不到 proj 的那一条。
  4. 照 DeepSeek 的样子（别家服务商）记一条。
  2026-10-08 摸底：工具名是 mcp__yunyun__remember；tools= 只管内置工具；DeepSeek 时工具结果后面还跟着一条 system 消息。
"""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import memory, protocol
from yunyun.assistant.providers import PROVIDERS

REMEMBER, RECALL, FORGET = memory.REMEMBER_TOOL, memory.RECALL_TOOL, memory.FORGET_TOOL


class NotebookApi(FakeApi):
    """记下每次主循环请求的系统提示词；工具结果在整段对话里找（别家服务商时结果后面还跟着一条 system 消息）。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.systems = []
        self.tool_results = []   # (tool_use_id, 出错没有, 第一行)：整段对话里的，不重复

    def handle(self, path, body):
        system = json.dumps(body.get("system", ""), ensure_ascii=False)
        if "芸芸" in system:
            self.systems.append(system)
        for message in body.get("messages") or []:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    result = (block.get("tool_use_id"), bool(block.get("is_error")), str(content).split("\n")[0][:80])
                    if result not in self.tool_results:
                        self.tool_results.append(result)
        return super().handle(path, body)


async def conversation(tmp, workspace, steps, question, provider=None):
    api = NotebookApi(steps)
    server = serve(api)
    events = []
    brain = make_brain(server, tmp, workspace, events.append, provider=provider, notebook=True)
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask(question), 60)
    finally:
        await brain.close()
        server.shutdown()
    finished = next((e for e in events if isinstance(e, protocol.TurnFinished)), None)
    decisions = [(e.tool.split("__")[-1], e.decision, e.detail) for e in events if isinstance(e, protocol.ToolDecision)]
    errors = [e for e in events if isinstance(e, protocol.Error)]
    return api, finished, decisions, errors


def titles():
    return [(m.id, m.scope, Path(m.workspace).name, m.title) for m in memory.load()]


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-notebook-"))
    proj, other = tmp / "proj", tmp / "other"
    proj.mkdir()
    other.mkdir()
    checks = {}
    try:
        print("1. 在 proj 里记两条")
        api, finished, decisions, errors = await conversation(tmp, proj, [
            ("tool", REMEMBER, {"title": "用户喜欢简短的回复", "scope": "user"}),
            ("tool", REMEMBER, {"title": "测试用 python -m unittest -v", "content": "在项目根目录跑", "scope": "project"}),
            ("text", "记住了"),
        ], "记住：我喜欢简短的回复；这个项目测试用 unittest")
        offered = [t for t in api.offered_tools or [] if t.startswith("mcp__")]
        print("   发给模型的芸芸工具：", offered)
        print("   操作记录：", decisions, "；出错：", errors)
        print("   记事本：", titles(), "；这一轮记了", finished.remembered if finished else None, "条")
        checks["记下两条"] = (set(memory.NOTEBOOK_TOOLS) <= set(offered) and len(memory.load()) == 2
                          and finished is not None and finished.remembered == 2
                          and [d[1] for d in decisions] == ["auto", "auto"])

        print("2. 新对话（还是 proj）：目录、recall、forget")
        api, finished, decisions, errors = await conversation(tmp, proj, [
            ("tool", RECALL, {"ids": ["m2"]}),
            ("tool", FORGET, {"id": "m1"}),
            ("text", "好的"),
        ], "这个项目怎么跑测试？另外忘掉我的回复偏好")
        system = api.systems[0] if api.systems else ""
        print("   系统提示词里的目录：", [line for line in ("[m1] 用户喜欢简短的回复", "[m2] 测试用 python -m unittest -v")
                                     if line in system])
        print("   工具结果：", api.tool_results, "；记事本：", titles())
        checks["目录 + 查 + 删"] = ("[m1] 用户喜欢简短的回复" in system and "[m2] 测试用" in system
                               and any("在项目根目录跑" in r[2] or "m2" in r[2] for r in api.tool_results)
                               and [m.id for m in memory.load()] == ["m2"])

        print("3. 换到 other：碰不到 proj 的记忆")
        memory.remember("用户用 Windows", scope="user")
        api, finished, decisions, errors = await conversation(tmp, other, [
            ("tool", RECALL, {"ids": ["m2"]}),
            ("tool", FORGET, {"id": "m2"}),
            ("text", "好的"),
        ], "看看记事本")
        system = api.systems[0] if api.systems else ""
        print("   目录里有 proj 的吗：", "[m2]" in system, "；有关于你的吗：", "用户用 Windows" in system)
        print("   工具结果：", api.tool_results, "；记事本：", titles())
        checks["别的项目碰不到"] = ("[m2]" not in system and "用户用 Windows" in system
                              and "m2" in [m.id for m in memory.load()]
                              and api.tool_results and api.tool_results[0][2].startswith("Nothing found") and api.tool_results[1][1])

        print("4. DeepSeek 的样子：记一条")
        api, finished, decisions, errors = await conversation(tmp, "", [
            ("tool", REMEMBER, {"title": "用户在学 Python", "scope": "user"}), ("text", "记住了")],
            "记住我在学 Python", provider=PROVIDERS["deepseek"])
        print("   操作记录：", decisions, "；记事本：", titles(), "；出错：", errors)
        checks["DeepSeek"] = finished is not None and finished.remembered == 1 and "用户在学 Python" in str(titles())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 记事本能用（记、目录、查、删，分项目，两家服务商都行）" if all(checks.values()) and len(checks) == 4
          else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
