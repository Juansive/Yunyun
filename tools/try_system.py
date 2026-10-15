"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证电源和程序的三个工具（阶段 11.6e）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_system.py

进程表、锁屏 / 关机 / 结束程序全是假的（这个脚本里换掉了 processes 碰 Windows 的几处），绝不真的关机、结束你的程序。
剧本：列出程序（不用问）→ 锁屏（不用问）→ 关机（问：标红、不给都允许；这次拒绝）→ 结束记事本（问：允许）→
结束 explorer.exe（系统的：规则直接拒绝）。Claude 官方、DeepSeek 两种样子各来一遍。
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import processes, protocol
from yunyun.assistant.providers import PROVIDERS

POWER, LIST, END = protocol.POWER_TOOL, protocol.LIST_PROCESSES_TOOL, protocol.END_PROCESS_TOOL
FAKE = [(4, 0, "System", 2**20, 0), (800, 4, "explorer.exe", 200 * 2**20, 1), (3001, 800, "Notepad.exe", 30 * 2**20, 1),
        (3002, 800, "Notepad.exe", 25 * 2**20, 1), (2001, 800, "msedge.exe", 900 * 2**20, 1)]


async def run(label, provider=None):
    done = []
    processes._snapshot = lambda: [p[:3] for p in FAKE]
    processes._memory = lambda pid: next((p[3] for p in FAKE if p[0] == pid), 0)
    processes._session = lambda pid: next((p[4] for p in FAKE if p[0] == pid), 0)
    processes._terminate = lambda pid: done.append(("end", pid)) or True
    processes._apply_power = lambda action, comment="": done.append((action,)) or (True, "")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-system-"))
    api = FakeApi([("tool", LIST, {}), ("tool", POWER, {"action": "lock"}), ("tool", POWER, {"action": "shutdown"}),
                   ("tool", END, {"name": "Notepad.exe"}), ("tool", END, {"name": "explorer.exe"}), ("text", "好了")])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, brain = [], None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 关机：拒绝；结束记事本：允许
            allow = event.kind == "end_process"
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, allow))

    brain = make_brain(server, tmp, "", emit, provider=provider)
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("看看谁在吃内存，然后关机"), 90)
        decisions = [e.decision for e in events if isinstance(e, protocol.ToolDecision)]
        asks = [(e.kind, e.scope, e.element, e.danger, e.can_remember) for e in events
                if isinstance(e, protocol.PermissionRequest)]
        results = [api.result_of(i) for i in range(5)]
        print(f"\n===== {label} =====")
        print("1. 交给模型的工具：", [t.split("__")[-1] for t in api.offered_tools or [] if t in (POWER, LIST, END)])
        print("2. 操作记录：", decisions, " 问了的：", asks)
        print("3. 假的做了：", done)
        for i, (error, text) in enumerate(results):
            print(f"   结果 {i + 1}（出错={error}）：{text[:120]!r}")
        checks["工具在"] = all(t in (api.offered_tools or []) for t in (POWER, LIST, END))
        checks["问不问"] = decisions == ["auto", "auto", "denied", "allowed", "blocked"]
        checks["关机、结束程序：标红、不给都允许"] = asks == [("power", "shutdown", "", True, False),
                                                 ("end_process", "Notepad.exe", "Notepad.exe ×2", True, False)]
        checks["做了的"] = done == [("lock",), ("end", 3001), ("end", 3002)]
        checks["列出和原因交给模型"] = ("msedge.exe · 900 MB" in results[0][1] and results[4][0]
                                  and "part of Windows" in results[4][1])
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    print("；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    return all(checks.values()) and len(checks) == 5


async def main():
    ok = await run("Claude 官方的样子")
    ok = await run("DeepSeek 的样子", PROVIDERS["deepseek"]) and ok
    print("\n结论：", "✓ 电源和程序的工具能用（列出、锁屏不用问；关机、结束程序每次问、标红；系统进程不结束）"
          if ok else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
