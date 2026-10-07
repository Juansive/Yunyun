"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"停止"能不能马上停下来。不花钱。

    .\\.venv\\Scripts\\python tools\\try_stop.py

剧本：
  第 1 问：执行 sleep 30（自动允许），2 秒后停止 → 多久停下？sleep 进程还在不在？
  第 2 问：想新建文件，确认请求不回答，1 秒后停止 → 会不会卡住？文件不能被写出来
  第 3 问：普通回答 → 停过之后还能接着聊吗？
"""
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from fake_anthropic_api import SHELL, FakeApi, make_brain, serve
from yunyun.assistant import protocol

SLEEP_EXE = "sleep.exe"   # Git Bash 的 sleep；没有 Git Bash 的电脑上 Claude Code 用 PowerShell，就看不到它


def count_processes(name):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    return sum(1 for line in out.splitlines() if line.lower().startswith(f'"{name.lower()}"'))


async def timed_stop(brain, question, before_stop, delay):
    """问一句，delay 秒后（或 before_stop() 成立后）停止；返回 (从停止到这一轮结束多少秒, 停之前的状态)。"""
    if "--debug" in sys.argv:
        print(f"{time.monotonic():.2f} 开始问：{question}")
    task = asyncio.create_task(brain.ask(question))
    end = time.monotonic() + 10
    while not before_stop() and time.monotonic() < end:
        await asyncio.sleep(0.05)
    await asyncio.sleep(delay)
    snapshot = count_processes(SLEEP_EXE)
    if "--debug" in sys.argv:
        from yunyun.assistant import proctree
        tree = {pid: (ppid, name) for pid, ppid, name in proctree._all_processes()}
        mine = [(pid, ppid, name) for pid, (ppid, name) in tree.items()
                if name.lower() in ("claude.exe", "bash.exe", "sleep.exe", "conhost.exe", "git.exe")]
        print("  进程：", mine, " 我的进程号：", __import__("os").getpid())
        print("  找到的命令进程：", proctree.command_processes(__import__("os").getpid()))
    stopped_at = time.monotonic()
    await brain.interrupt()
    await asyncio.wait_for(task, 30)
    return time.monotonic() - stopped_at, snapshot


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-stop-"))
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("x = 1\n", encoding="utf-8")
    api = FakeApi([
        ("tool", SHELL, {"command": "sleep 30", "description": "wait"}),
        ("tool", "Write", {"file_path": str(proj / "b.py"), "content": "x = 2\n"}),   # 新文件：不用先读
        ("text", "我还在～"),
    ])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, waiting = [], []
    brain = None
    answer_commands = True   # 第 1 问的命令自动允许；第 2 问的确认不回答

    def emit(event):
        events.append(event)
        if "--debug" in sys.argv:
            print(f"  {time.monotonic():.2f} 事件 {event}")
        if isinstance(event, protocol.PermissionRequest):
            waiting.append(event)
            if answer_commands:
                loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))

    brain = make_brain(server, tmp, proj, emit)
    before = count_processes(SLEEP_EXE)
    try:
        await asyncio.wait_for(brain.start(), 60)
        started = lambda: any(isinstance(e, protocol.ToolStarted) for e in events)   # noqa: E731
        took1, during = await timed_stop(brain, "第 1 问", started, 2.0)
        await asyncio.sleep(1.0)
        after = count_processes(SLEEP_EXE)
        finished1 = [e for e in events if isinstance(e, protocol.TurnFinished)][-1]

        answer_commands = False
        events.clear()
        took2, _ = await timed_stop(brain, "第 2 问", lambda: waiting[1:], 1.0)
        finished2 = [e for e in events if isinstance(e, protocol.TurnFinished)][-1]
        file_after = (proj / "b.py").exists()

        events.clear()
        await asyncio.wait_for(brain.ask("第 3 问"), 30)
        reply = "".join(e.text for e in events if isinstance(e, protocol.TextDelta))
    finally:
        await brain.close()
        server.shutdown()

    print(f"第 1 问（命令跑着时停止）：{took1:.2f} 秒停下，stopped={finished1.stopped}；"
          f"sleep 进程：开始前 {before} 个，停之前 {during} 个，停之后 {after} 个")
    print(f"第 2 问（等确认时停止）：{took2:.2f} 秒停下，stopped={finished2.stopped}，b.py 写出来了没有：{file_after}")
    print(f"第 3 问（停过之后）：回答 {reply!r}")
    if "--debug" in sys.argv:
        print("假接口收到的工具调用：", [(c[0], c[1]) for c in api.calls])
        print("假接口收到的 tool_result：", api.results)
    ok = (took1 < 5 and finished1.stopped and after == before and took2 < 5 and finished2.stopped
          and not file_after and reply == "我还在～")
    print("结论：", "✓ 都能马上停下，命令进程也结束了" if ok else "✗ 和预期不一致，看上面的明细")
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
