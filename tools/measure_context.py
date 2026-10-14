"""开发用：量助手模式每一问带着多少上下文（深度升级第一步）。真的 Claude Code + 假接口，不花钱、不联网。

    .\\.venv\\Scripts\\python tools\\measure_context.py

1. 只聊天 / 选了工作文件夹 / 照 DeepSeek 的样子：第一次主循环请求里，系统提示词（Claude Code 自带的 + 芸芸的人设）、
   工具说明（每个工具多大）、第一条消息各占多少。
2. 工具结果多大：读一个大文件、一条输出很长的命令（看 Claude Code 自己截到多长）。
token 是估算的（英文和代码约 3.5 个字符一个，中日文约一个字一个，误差三成左右）；准确数要用 count_tokens 接口。
"""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import SHELL, FakeApi, make_brain, serve
from yunyun.assistant import protocol
from yunyun.assistant.providers import PROVIDERS

ROOT = Path(__file__).resolve().parents[1]
PERSONA_START = "你是芸芸"
LONG_OUTPUT = {"Bash": "yes 0123456789 | head -n 20000", "PowerShell": "1..20000 | % { '0123456789' }"}


def tokens(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    wide = sum(1 for c in text if ord(c) > 0x2E7F)   # 中日韩文字
    return round((len(text) - wide) / 3.5 + wide)


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") if b.get("type") == "text" else json.dumps(b, ensure_ascii=False)
                     for b in content if isinstance(b, dict))


class MeasureApi(FakeApi):
    """记下每次主循环请求的完整内容；"执行命令"那一步按 Claude Code 给的是 Bash 还是 PowerShell 换命令。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.bodies = []

    def handle(self, path, body):
        if "/v1/messages" in path and not path.endswith("/count_tokens") \
                and "芸芸" in json.dumps(body.get("system", ""), ensure_ascii=False):
            self.bodies.append(body)
        return super().handle(path, body)

    def prepare_step(self):
        if self.steps and self.steps[0][0] == "tool" and self.steps[0][1] == SHELL:
            shell = "Bash" if "Bash" in self.offered_tools else "PowerShell"
            self.steps[0] = ("tool", shell, {"command": LONG_OUTPUT[shell]})


async def run(tmp, workspace, steps, provider=None):
    api = MeasureApi(steps)
    server = serve(api)
    loop = asyncio.get_running_loop()
    brain = None

    def emit(event):
        if isinstance(event, protocol.PermissionRequest):
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))

    brain = make_brain(server, tmp, workspace, emit, provider=provider, notebook=True)
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("你好"), 120)
    finally:
        await brain.close()
        server.shutdown()
    return api


def fixed_cost(title, body):
    """第一次请求：系统提示词、工具说明、第一条消息各多少。返回总数。"""
    system = text_of(body.get("system") or "")
    cut = system.find(PERSONA_START)
    preset, persona = (system[:cut], system[cut:]) if cut >= 0 else (system, "")
    tools = sorted(((t.get("name"), tokens(t)) for t in body.get("tools") or []), key=lambda x: -x[1])
    first = tokens(text_of(body["messages"][0].get("content") or "")) if body.get("messages") else 0
    total = tokens(preset) + tokens(persona) + sum(n for _, n in tools) + first
    print(f"\n== {title}：第一问固定带着约 {total:,} token")
    print(f"   Claude Code 自带的系统提示词 {tokens(preset):>6,}（{len(preset):,} 字符）")
    print(f"   芸芸的人设（含记事本说明）   {tokens(persona):>6,}（{len(persona):,} 字符）")
    print(f"   工具说明 {len(tools)} 个             {sum(n for _, n in tools):>6,}")
    for name, n in tools:
        print(f"      {name:<34}{n:>6,}")
    print(f"   第一条消息（提问 + 附带提醒） {first:>6,}")
    return total


def tool_results(api):
    print("\n== 工具结果交给模型时多大")
    for (tool_id, tool, tool_input), (error, text) in zip(api.calls, (api.result_of(i) for i in range(len(api.calls)))):
        print(f"   {tool:<12}{tokens(text):>7,} token（{len(text):,} 字符）{'  出错' if error else ''}  {text[-80:]!r}")
    if api.bodies:
        last = api.bodies[-1]
        size = tokens(text_of(last.get("system") or "")) + tokens(last.get("tools") or []) + \
            sum(tokens(text_of(m.get("content") or "")) for m in last["messages"])
        print(f"   这一问最后一次请求一共约 {size:,} token（{len(api.bodies)} 次请求）")


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-context-"))
    workspace = tmp / "proj"
    workspace.mkdir()
    (workspace / "CLAUDE.md").write_text("# 示例项目\n- 测试用 pytest\n", encoding="utf-8")
    big = workspace / "big.py"
    shutil.copy(ROOT / "yunyun" / "assistant" / "claude_brain.py", big)
    try:
        chat = await run(tmp, "", [("text", "你好呀")])
        fixed_cost("只聊天（Claude 官方）", chat.bodies[0])
        work = await run(tmp, workspace, [("tool", "Read", {"file_path": str(big)}), ("tool", SHELL, {}),
                                          ("text", "好了")])
        fixed_cost("选了工作文件夹（Claude 官方）", work.bodies[0])
        tool_results(work)
        deepseek = await run(tmp, "", [("text", "你好呀")], provider=PROVIDERS["deepseek"])
        fixed_cost("只聊天（照 DeepSeek 的样子）", deepseek.bodies[0])
        print(f"\n   （读的大文件原本 {big.stat().st_size:,} 字节）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
