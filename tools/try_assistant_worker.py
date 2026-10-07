"""助手模式 0.7 的"助手子进程"（worker）演示。由 tools/try_assistant_ipc.py 启动，不要直接运行。

方案 B（docs/助手模式计划.md 的 0.12）：芸芸本身不 import Agent SDK，要用助手时启动这个子进程。
它只用 asyncio，不用 Qt；和芸芸之间用标准输入 / 输出传 JSON，一行一条：

    芸芸 → worker：{"cmd": "ask", "text": ...}  {"cmd": "interrupt"}
                  {"cmd": "permission", "id": 1, "allow": true, "reason": ""}  {"cmd": "quit"}
    worker → 芸芸：{"ev": "ready", ...}  {"ev": "text", "text": ...}  {"ev": "tool", "name": ...}
                  {"ev": "permission", "id": 1, "tool": ..., "input": {...}}  {"ev": "turn_end", ...}  {"ev": "error", ...}

标准输入读到头（芸芸退出 / 崩溃，管道断了）就自己退出；它一退出，claude.exe 也会退出（0.4 测过）。

参数：
    --hide-console   启动 claude.exe 时加 CREATE_NO_WINDOW（"不建控制台窗口"）
"""
import asyncio
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import keyring

from procutil import clean_inherited_env, own_console   # 同一个文件夹里的 tools/procutil.py

SERVICE, PROVIDER = "yunyun-assistant", "anthropic"   # 和 tools/assistant_key.py 一致
MODEL = "claude-haiku-4-5"   # 测试一律用最便宜的模型（用户要求）；Haiku 4.5 不支持 effort 参数，下面不传
MAX_BUDGET_USD = 0.05
PERSONA = "你是芸芸，一个住在用户桌面上的桌宠助手。用中文回答，语气亲切、简洁。"


def hide_child_consoles():
    """让 SDK 启动的所有子进程（claude.exe，还有它检查版本时的那一次）都不建控制台窗口。

    SDK 用 anyio.open_process 启动 claude.exe，没有给我们改启动参数的地方，所以在这个进程里把它包一层。
    claude.exe 再启动的 git.exe 会继承这个"看不见的控制台"，也就不会弹窗口。
    """
    import anyio
    original = anyio.open_process

    async def open_process_hidden(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return await original(*args, **kwargs)

    anyio.open_process = open_process_hidden


async def run(hide_console, emit, stdin):
    if hide_console:
        hide_child_consoles()
    start = time.perf_counter()
    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow, PermissionResultDeny,
        ResultMessage, StreamEvent, ToolUseBlock, create_sdk_mcp_server, tool,
    )
    import_s = time.perf_counter() - start

    clean_inherited_env()
    key = keyring.get_password(SERVICE, PROVIDER)
    if not key:
        emit({"ev": "error", "message": "还没有保存 Key：先运行 tools/assistant_key.py"})
        return

    loop = asyncio.get_running_loop()
    commands = asyncio.Queue()

    def read_stdin():
        """后台线程：一行行读芸芸发来的命令，交给 asyncio 那边（Windows 上 asyncio 读不了这种管道，所以用线程）。"""
        for line in stdin:
            if line.strip():
                loop.call_soon_threadsafe(commands.put_nowait, json.loads(line))
        loop.call_soon_threadsafe(commands.put_nowait, {"cmd": "eof"})   # 管道断了：芸芸不在了

    threading.Thread(target=read_stdin, daemon=True).start()

    # "后台等你点按钮"：Claude 要用工具 → 发确认请求给芸芸 → 在这里等，芸芸那边点了再继续
    pending = {}
    ids = itertools.count(1)

    async def can_use_tool(tool_name, tool_input, context):
        request_id = next(ids)
        future = loop.create_future()
        pending[request_id] = future
        emit({"ev": "permission", "id": request_id, "tool": tool_name, "input": tool_input})
        allow, reason = await future
        if allow:
            return PermissionResultAllow()
        return PermissionResultDeny(message=reason or "用户拒绝了")

    @tool("say_hi", "演示用：让芸芸跟某个人打招呼，返回一句招呼语。", {"name": str})
    async def say_hi(args):
        return {"content": [{"type": "text", "text": f"芸芸向{args['name']}挥了挥爪子～"}]}

    tmp = Path(tempfile.mkdtemp(prefix="yunyun-ipc-"))
    (tmp / "work").mkdir()
    (tmp / "claude-config").mkdir()
    options = ClaudeAgentOptions(
        model=MODEL,
        tools=[],                                   # 不要任何内置工具（读写文件、命令、网页都没有）
        mcp_servers={"yunyun": create_sdk_mcp_server(name="yunyun", version="0.1.0", tools=[say_hi])},
        permission_mode="default",                  # 没有允许规则的工具都要问 → 走 can_use_tool
        can_use_tool=can_use_tool,
        setting_sources=[],
        strict_mcp_config=True,
        system_prompt={"type": "preset", "preset": "claude_code", "append": PERSONA},
        include_partial_messages=True,
        max_budget_usd=MAX_BUDGET_USD,
        cwd=str(tmp / "work"),
        env={
            "ANTHROPIC_API_KEY": key,
            "CLAUDE_CONFIG_DIR": str(tmp / "claude-config"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
    )

    async def run_turn(text):
        start = time.perf_counter()
        try:
            await client.query(text)
            async for msg in client.receive_response():
                if isinstance(msg, StreamEvent):
                    event, delta = msg.event, msg.event.get("delta") or {}
                    if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        emit({"ev": "text", "text": delta.get("text", "")})
                elif isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, ToolUseBlock):
                            emit({"ev": "tool", "name": block.name})
                elif isinstance(msg, ResultMessage):
                    emit({"ev": "turn_end", "subtype": msg.subtype, "cost": msg.total_cost_usd,
                          "seconds": time.perf_counter() - start})
        except Exception as e:   # 演示脚本：把错误告诉芸芸那边
            emit({"ev": "error", "message": f"{type(e).__name__}: {e}"})

    client = ClaudeSDKClient(options)
    start = time.perf_counter()
    await client.connect()
    has_console, console_visible = own_console()
    emit({"ev": "ready", "pid": os.getpid(), "import_s": round(import_s, 2),
          "connect_s": round(time.perf_counter() - start, 2),
          "has_console": has_console, "console_visible": console_visible})

    turn = None
    try:
        while True:
            cmd = await commands.get()
            kind = cmd.get("cmd")
            if kind == "ask" and (turn is None or turn.done()):
                turn = asyncio.create_task(run_turn(cmd["text"]))
            elif kind == "interrupt":
                await client.interrupt()
            elif kind == "permission":
                future = pending.pop(cmd.get("id"), None)
                if future and not future.done():
                    future.set_result((bool(cmd.get("allow")), cmd.get("reason", "")))
            elif kind in ("quit", "eof"):
                break
    finally:
        for future in pending.values():   # 还在等确认的，一律当作拒绝，不能卡住
            if not future.done():
                future.set_result((False, "助手关闭了"))
        if turn and not turn.done():
            turn.cancel()
        await client.disconnect()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    # pythonw 没有控制台时 sys.stdout 可能是 None；芸芸给了管道，就直接用文件描述符 0 / 1
    out = sys.stdout.buffer if sys.stdout else os.fdopen(1, "wb")
    stdin = sys.stdin.buffer if sys.stdin else os.fdopen(0, "rb")
    sys.stdout = sys.stderr   # 别的代码 print 的东西去 stderr，别弄乱和芸芸之间的 JSON

    def emit(obj):
        out.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()

    try:
        asyncio.run(run("--hide-console" in sys.argv, emit, stdin))
    except Exception as e:
        emit({"ev": "error", "message": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
