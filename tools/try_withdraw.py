"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"这句不是跟你说的"（7.9 ⑤：撤回最后一问）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_withdraw.py

  1. 一段对话问两句："第一问：记住 42"、"第二问：你去买点菜吧"（误收的那句）；关掉大脑
  2. 接着聊并撤回最后一问（withdraw=True）："第三问" → 交给模型的有第一问、没有第二问？会话编号变不变？
  3. 之后再普通地接着聊（历史对话"接着聊"）："第四问" → 第二问还在不在？
  4. 只有一问的对话撤回 → 开一段新对话（什么都不带）；指纹对不上（最后一问不是要撤回的那一问）→ 照常接着聊、什么都不扔
  5. 芸芸自己读记录（历史对话、回想、翻回忆，transcripts.read_transcript）：撤回的那一问没有了；
     平常的对话（用了工具、整理过对话）一条都不少（_withdrawn 是空的）
"""
import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from try_compact import CompactApi
from yunyun.assistant import protocol, transcripts


async def run(server, tmp, workspace, questions, resume="", withdraw="", compact_tokens=0):
    events, brain = [], None
    loop = asyncio.get_running_loop()

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 改文件要确认：都允许
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))
    brain = make_brain(server, tmp, workspace, emit, resume=resume, withdraw=withdraw, compact_tokens=compact_tokens)
    try:
        await asyncio.wait_for(brain.start(), 60)
        for question in questions:
            await asyncio.wait_for(brain.ask(question), 60)
    except Exception as e:   # 启动失败（比如 Claude Code 拒绝撤回）：记下来看
        events.append(protocol.Error("other", f"{type(e).__name__}: {e}"[:300]))
    finally:
        await brain.close()
    finished = [e for e in events if isinstance(e, protocol.TurnFinished)]
    errors = [e for e in events if isinstance(e, protocol.Error)]
    return (finished[-1].session_id if finished else ""), errors


def seen(api, word):
    return any(word in prompt for prompt in api.prompts_seen[-1])


def record(tmp, session):
    path = next((tmp / "claude" / "projects").glob(f"*/{session}.jsonl"))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    transcript = transcripts.read_transcript(str(path))
    return rows, [t.question for t in transcript.turns] if transcript else []


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-withdraw-"))
    proj = tmp / "proj"
    proj.mkdir()
    api = FakeApi([("text", "好的，记住了 42"), ("text", "好呀，我去买菜"), ("text", "是 42"), ("text", "记得，42"),
                   ("text", "你好"), ("text", "新对话"), ("text", "甲答"), ("text", "乙答"), ("text", "丙答")])
    server = serve(api)
    try:
        session, errors = await run(server, tmp, proj, ["第一问：记住 42", "第二问：你去买点菜吧"])
        print(f"1. 问了两句：会话编号 {session[:8]}…，出错 {errors}")

        second, errors = await run(server, tmp, proj, ["第三问：刚才的数字是几？"], resume=session,
                                   withdraw=transcripts.question_digest("第二问：你去买点菜吧"))
        dropped = seen(api, "第一问") and not seen(api, "第二问")
        print(f"2. 撤回后接着问：会话编号 {second[:8] if second else None}…（{'没变' if second == session else '变了'}），"
              f"出错 {errors}；交给模型的：{api.prompts_seen[-1]}")

        third, errors = await run(server, tmp, proj, ["第四问：还记得吗？"], resume=second or session)
        stays = seen(api, "第一问") and seen(api, "第三问") and not seen(api, "第二问")
        print(f"3. 之后再接着聊：出错 {errors}；交给模型的：{api.prompts_seen[-1]}")

        only, _ = await run(server, tmp, "", ["只有这一问"])
        fresh, errors = await run(server, tmp, "", ["新的一问"], resume=only,
                                  withdraw=transcripts.question_digest("只有这一问"))
        clean = not seen(api, "只有这一问")
        print(f"4. 只有一问时撤回：会话编号 {'新的' if fresh != only else '没变'}，出错 {errors}；交给模型的：{api.prompts_seen[-1]}")
        other, _ = await run(server, tmp, "", ["甲：先问一句", "乙：再问一句"])
        await run(server, tmp, "", ["丙"], resume=other, withdraw=transcripts.question_digest("不是这句"))
        kept = seen(api, "甲") and seen(api, "乙")
        print(f"4b. 指纹对不上：{'照常接着聊、什么都没扔' if kept else '扔了（不对）'}；交给模型的：{api.prompts_seen[-1]}")

        rows, questions = record(tmp, session)
        history = questions == ["第一问：记住 42", "第三问：刚才的数字是几？", "第四问：还记得吗？"]
        print(f"5a. 芸芸读记录：{questions}（文件里一共 {len(rows)} 行，扔掉 {len(transcripts._withdrawn(rows))} 行）")
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    # 平常的对话：用了工具（读 + 改文件，要确认）、整理过对话——一条都不能少
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-withdraw-"))
    proj = tmp / "proj"
    proj.mkdir()
    hello = proj / "hello.py"
    hello.write_text("print('hi')\n", encoding="utf-8")
    api = CompactApi([("tool", "Read", {"file_path": str(hello)}),
                      ("tool", "Edit", {"file_path": str(hello), "old_string": "print('hi')", "new_string": "print('hey')"}),
                      ("text", "改好了"), ("text", "第 2 答"), ("text", "第 3 答")],
                     usage={"input_tokens": 30000, "output_tokens": 5})
    server = serve(api)
    try:
        session, errors = await run(server, tmp, proj, ["第 1 问：改 hello", "第 2 问"], compact_tokens=20000)
        await run(server, tmp, proj, ["第 3 问"], resume=session)
        rows, questions = record(tmp, session)
        intact = not transcripts._withdrawn(rows) and questions == ["第 1 问：改 hello", "第 2 问", "第 3 问"]
        print(f"5b. 平常的对话（工具 + 整理 + 接着聊）：出错 {errors}；读出 {questions}，"
              f"扔掉 {len(transcripts._withdrawn(rows))} 行（应该是 0）")
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    ok = dropped and stays and clean and kept and history and intact
    print("结论：", "✓ 撤回的那一问交给模型时没有了，之后接着聊、芸芸读记录也没有；对不上就不扔；平常的对话一条不少" if ok
          else f"✗ 撤回后={dropped} 之后接着聊={stays} 只有一问={clean} 对不上={kept} 读记录={history} 平常的对话={intact}")


if __name__ == "__main__":
    asyncio.run(main())
