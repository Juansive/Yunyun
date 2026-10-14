"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证工具结果的上限（阶段 11.2 上下文 C1）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_context.py

在一个工作文件夹里（一个 700 多行的大文件）：
  1. 读大文件不给 offset / limit → Claude Code 按 CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS 截断，并告诉模型"第几行到第几行、用 offset 读下一页"；
  2. 一条输出 2 万多字符的命令 → 按 BASH_MAX_OUTPUT_LENGTH 存成文件，只给模型看开头 2KB 和文件在哪；
  3. 读回那个存下来的文件 → 芸芸的规则不用问（permissions 的 saved_outputs）。
2026-10-14 摸底：没设上限时 700 行的文件一次 1.3 万 token、2.2 万字符的命令输出整段交给模型。
"""
import asyncio
import json
import re
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import SHELL, FakeApi, make_brain, serve
from yunyun.assistant import protocol

ROOT = Path(__file__).resolve().parent.parent
LONG_OUTPUT = {"Bash": "yes 0123456789 | head -n 2000", "PowerShell": "1..2000 | % { '0123456789' }"}
SAVED = re.compile(r"Full output saved to: (\S+)")


class ContextApi(FakeApi):
    """工具结果连同后面附的提醒一起记下（截断的说明在提醒里）；"执行命令"按 Bash / PowerShell 换写法；
    第三步读的文件按第二步的结果填。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.raw = {}

    def handle(self, path, body):
        for message in body.get("messages") or []:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    self.raw.setdefault(block["tool_use_id"], json.dumps(block.get("content"), ensure_ascii=False))
        return super().handle(path, body)

    def prepare_step(self):
        if not self.steps or self.steps[0][0] != "tool":
            return
        _, tool, tool_input = self.steps[0]
        if tool == SHELL:
            shell = "Bash" if "Bash" in self.offered_tools else "PowerShell"
            self.steps[0] = ("tool", shell, {"command": LONG_OUTPUT[shell]})
        elif tool == "Read" and tool_input.get("file_path") == "<saved>":
            found = SAVED.search(self.result_of(1)[1]) if len(self.calls) > 1 else None
            self.steps[0] = ("tool", "Read", {"file_path": found.group(1) if found else "missing.txt"})

    def raw_of(self, call_index):
        return self.raw.get(self.calls[call_index][0], "")


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-context-"))
    workspace = tmp / "proj"
    workspace.mkdir()
    big = workspace / "big.py"
    shutil.copy(ROOT / "yunyun" / "assistant" / "claude_brain.py", big)
    lines = len(big.read_text(encoding="utf-8").splitlines())
    api = ContextApi([("tool", "Read", {"file_path": str(big)}), ("tool", SHELL, {}),
                      ("tool", "Read", {"file_path": "<saved>"}), ("text", "好了")])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, brain = [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 执行命令要问：同意
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))

    brain = make_brain(server, tmp, workspace, emit)
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("看看 big.py，再跑一下命令"), 180)
        decisions = [(e.tool, e.decision) for e in events if isinstance(e, protocol.ToolDecision)]
        read, command, saved = api.raw_of(0), api.raw_of(1), api.raw_of(2)
        shown = re.search(r"showing lines 1-(\d+) of (\d+)", read)
        print(f"1. 读大文件（{lines} 行）：{len(read):,} 字符；", shown.group(0) if shown else "没有截断说明")
        checks["读大文件分页"] = bool(shown) and int(shown.group(1)) < lines and "offset=" in read
        print(f"2. 长命令输出：{len(command):,} 字符；", "存成了文件" if SAVED.search(command) else "整段交给了模型")
        checks["长输出存成文件"] = bool(SAVED.search(command)) and len(command) < 5_000
        print(f"3. 读回存下来的文件：{api.calls[2][2].get('file_path', '')[-60:]}；操作记录 {decisions}")
        checks["读回不用问"] = (len(decisions) == 3 and decisions[2] == ("Read", "auto")
                            and "0123456789" in saved and "error" not in saved[:80].lower())
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 工具结果的上限都管用（读大文件分页、长输出存文件、读回不用问）" if all(checks.values()) and len(checks) == 3
          else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
