"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"接着聊"（resume = 会话编号）怎么表现。不花钱。

    .\\.venv\\Scripts\\python tools\\try_resume.py

  1. 第一段对话："第一问"，记下会话编号；关掉大脑
  2. 同一个工作文件夹、resume=那个编号："第二问" → 前面的对话有没有交给模型？会话编号变不变？花费怎么算？
  3. 换一个工作文件夹再 resume → 还能接上吗？
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import protocol


async def run_turn(server, tmp, workspace, question, resume=""):
    events = []
    brain = make_brain(server, tmp, workspace, events.append, resume=resume)
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask(question), 60)
    except Exception as e:   # 启动失败之类：记下来看
        events.append(protocol.Error("other", f"{type(e).__name__}: {e}"))
    finally:
        await brain.close()
    finished = next((e for e in events if isinstance(e, protocol.TurnFinished)), None)
    errors = [e for e in events if isinstance(e, protocol.Error)]
    return finished, errors


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-resume-"))
    proj, other = tmp / "proj", tmp / "other"
    proj.mkdir()
    other.mkdir()
    api = FakeApi([("text", "好的，记住了 42"), ("text", "是 42"), ("text", "换了文件夹也记得 42")])
    server = serve(api)
    try:
        first, errors1 = await run_turn(server, tmp, proj, "第一问：记住 42")
        session = first.session_id if first else ""
        print(f"第 1 段：会话编号 {session[:8]}…，花费 {first.cost_usd if first else None}，出错 {errors1}")
        print("   交给模型的提问：", api.prompts_seen[-1])

        second, errors2 = await run_turn(server, tmp, proj, "第二问：刚才的数字是几？", resume=session)
        print(f"接着聊（同一个文件夹）：会话编号 {second.session_id[:8] if second else None}…"
              f"（{'没变' if second and second.session_id == session else '变了'}），"
              f"这一轮花费 {second.cost_usd if second else None}，一共 {second.session_cost_usd if second else None}，"
              f"出错 {errors2}")
        print("   交给模型的提问：", api.prompts_seen[-1])
        resumed_ok = "第一问：记住 42" in api.prompts_seen[-1]

        third, errors3 = await run_turn(server, tmp, other, "第三问：换了文件夹还记得吗？", resume=session)
        print(f"接着聊（换了文件夹）：会话编号 {third.session_id[:8] if third else None}…，出错 {errors3}")
        print("   交给模型的提问：", api.prompts_seen[-1])
        other_ok = "第一问：记住 42" in api.prompts_seen[-1]

        files = sorted(p.relative_to(tmp / "claude" / "projects").as_posix()
                       for p in (tmp / "claude" / "projects").glob("*/*.jsonl"))
        print("对话记录文件：", [f.split("/")[0][-12:] + "/" + f.split("/")[1][:8] for f in files])
        print("结论：", "✓ 同一个文件夹能接上" if resumed_ok else "✗ 同一个文件夹没接上",
              "；换了文件夹" + ("也能接上" if other_ok else "接不上（要回到原来的文件夹）"))
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
