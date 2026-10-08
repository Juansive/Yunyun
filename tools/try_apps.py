"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证打开软件的两个工具（阶段 6）。不花钱、不读你的开始菜单、不真的打开软件。

    .\\.venv\\Scripts\\python tools\\try_apps.py

  剧本：find_app 找"计算"（不用问）→ open_app 计算器（问你，假装你点了"这次对话都允许打开它"）→ 子进程发 OpenAppRequest
  （假装芸芸打开了）→ 再打开一次计算器（不用再问）→ open_app 一个开始菜单里没有的（权限规则直接拒绝，告诉模型先找）→
  open_app 记事本（问你，假装你拒绝了）。Claude 官方、DeepSeek 两种样子各来一遍。
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import apps, protocol
from yunyun.assistant.providers import PROVIDERS

CALC = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
FAKE_APPS = [{"Name": "计算器", "AppID": CALC},
             {"Name": "记事本", "AppID": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"},
             {"Name": "Visual Studio Code", "AppID": "Microsoft.VisualStudioCode"}]


def never_open(target):
    raise AssertionError("剧本里不应该真的打开软件（打开只在芸芸进程里做）")


class ResultApi(FakeApi):
    """按工具调用的编号记下交给模型的每个工具结果：{tool_use_id: (出错没有, 文字开头)}。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.by_id = {}

    def handle(self, path, body):
        for message in body.get("messages") or []:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    text = str(content).split("<system-reminder>")[0].strip()   # Claude 官方那样会在后面附一段提醒
                    self.by_id.setdefault(block["tool_use_id"], (bool(block.get("is_error")), text[:200]))
        return super().handle(path, body)


async def run(label, provider=None):
    apps._query_start_apps = lambda: [dict(item) for item in FAKE_APPS]   # 不读你真的开始菜单
    apps._startfile = never_open
    apps.clear_cache()
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-apps-"))
    api = ResultApi([("tool", protocol.FIND_APP_TOOL, {"query": "计算"}),
                   ("tool", protocol.OPEN_APP_TOOL, {"name": "计算器"}),
                   ("tool", protocol.OPEN_APP_TOOL, {"name": "计算器"}),
                   ("tool", protocol.OPEN_APP_TOOL, {"name": "cmd.exe"}),
                   ("tool", protocol.OPEN_APP_TOOL, {"name": "记事本"}),
                   ("text", "打开了")])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, brain = [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.OpenAppRequest):         # 假装芸芸打开了
            loop.call_soon(brain.reply_bridge, protocol.OpenAppReply(event.request_id, True))
        elif isinstance(event, protocol.PermissionRequest):    # 计算器："这次对话都允许打开它"；记事本：拒绝
            allow = event.input.get("name") == "计算器"
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, allow, "", allow))

    try:
        brain = make_brain(server, tmp, "", emit, provider=provider)
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("帮我打开计算器"), 90)
    finally:
        if brain is not None:
            await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    offered = [t for t in api.offered_tools or [] if "app" in t]
    asked = [(e.kind, e.input.get("name"), e.can_remember, e.scope) for e in events
             if isinstance(e, protocol.PermissionRequest)]
    requests = [e for e in events if isinstance(e, protocol.OpenAppRequest)]
    decisions = [(e.tool.rsplit("__", 1)[-1], e.decision, e.detail) for e in events
                 if isinstance(e, protocol.ToolDecision)]
    results = [api.by_id.get(tool_id, (None, "（没交给模型）")) for tool_id, _, _ in api.calls]
    print(f"{label}：工具 {offered}\n   问了 {asked}\n   请芸芸打开 {[(r.request_id, r.name) for r in requests]}\n"
          f"   操作记录 {decisions}\n   交给模型的结果 {results}")
    return (sorted(offered) == sorted([protocol.FIND_APP_TOOL, protocol.OPEN_APP_TOOL])
            and asked == [("open_app", "计算器", True, "计算器"), ("open_app", "记事本", True, "记事本")]
            and requests == [protocol.OpenAppRequest(1, "计算器", CALC), protocol.OpenAppRequest(2, "计算器", CALC)]
            and decisions == [("find_app", "auto", "计算"), ("open_app", "remembered", "计算器"),
                              ("open_app", "granted", "计算器"), ("open_app", "blocked", "cmd.exe"),
                              ("open_app", "denied", "记事本")]
            and len(results) == 5
            and not results[0][0] and "计算器" in results[0][1]
            and not results[1][0] and "Opened" in results[1][1] and "Opened" in results[2][1]
            and results[3][0] and "find_app" in results[3][1]
            and results[4][0])


async def main():
    checks = {"Claude 官方": await run("Claude 官方"),
              "DeepSeek": await run("DeepSeek", PROVIDERS["deepseek"])}
    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 打开软件能用（找不用问，打开要问、能\"都允许\"，开始菜单里没有的直接拒绝；两家都行）"
          if all(checks.values()) else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
