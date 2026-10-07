"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证权限关卡（claude_brain.PermissionGate）真的管住了每一次工具调用。

    .\\.venv\\Scripts\\python tools\\try_permissions.py [--keep]     # --keep：留下临时文件夹看看

**不花钱、不联网**：本机起一个假的 /v1/messages，按剧本让"模型"一步步调用工具；ClaudeBrain 的服务商地址指到它，Key 是假的。
所有东西都在一个新的临时文件夹里：工作文件夹 proj\\（有 hello.py）、外面的 other\\（有 secret.txt）、Claude Code 的数据目录。
不碰芸芸项目、不碰你的文件、不读你的 Key。

确认请求由脚本按剧本自动回答（模拟用户点按钮），最后打印每一步：有没有问、怎么答的、Claude Code 实际做了什么。
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fake_anthropic_api import SHELL, FakeApi, make_brain, serve  # noqa: E402
from yunyun.assistant import protocol  # noqa: E402

async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-permissions-"))
    proj, other = tmp / "proj", tmp / "other"
    proj.mkdir()
    other.mkdir()
    (proj / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    (other / "secret.txt").write_text("不该被读到的内容\n", encoding="utf-8")

    # 剧本：(步骤说明, 剧本里的调用, 期望：问不问, 问的话怎么答)
    plan = [
        ("读工作文件夹里的文件", ("tool", "Read", {"file_path": str(proj / "hello.py")}), None),
        ("读外面的文件", ("tool", "Read", {"file_path": str(other / "secret.txt")}), "deny"),
        ("改工作文件夹里的文件", ("tool", "Edit", {"file_path": str(proj / "hello.py"), "old_string": "print('hi')",
                                         "new_string": "print('hello')"}), "allow"),
        ("执行命令（选这类都允许）", ("tool", SHELL, {"command": "echo first", "description": "say"}), "remember"),
        ("同一类命令", ("tool", SHELL, {"command": "echo second", "description": "say"}), None),
        ("危险命令", ("tool", SHELL, {"command": "rm hello.py", "description": "delete"}), "deny"),
        ("写外面的文件", ("tool", "Write", {"file_path": str(other / "new.txt"), "content": "x"}), "deny"),
        ("搜索工作文件夹", ("tool", "Glob", {"pattern": "**/*.py"}), None),
        ("任务工具（没开）", ("tool", "Task", {"description": "x", "prompt": "y", "subagent_type": "general-purpose"}),
         None),
    ]
    api = FakeApi([step for _, step, _ in plan] + [("text", "做完了")])
    server = serve(api)

    loop = asyncio.get_running_loop()
    requests = []   # (第几次请求, 事件, 回答)
    answers = iter(answer for _, _, answer in plan if answer)
    brain = None

    def emit(event):
        if isinstance(event, protocol.PermissionRequest):
            answer = next(answers, "deny")
            reply = protocol.PermissionReply(event.request_id, answer != "deny", "剧本：拒绝" if answer == "deny" else "",
                                             answer == "remember")
            requests.append((event, answer))
            loop.call_soon(brain.reply_permission, reply)
        elif isinstance(event, protocol.Error):
            print("出错：", event)

    brain = make_brain(server, tmp, proj, emit)
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("按剧本做"), 120)
    finally:
        await brain.close()
        server.shutdown()

    expected_asks = [answer for _, _, answer in plan if answer]
    print("结论：", "✓ 和规则一致" if [answer for _, answer in requests] == expected_asks
          and (proj / "hello.py").read_text(encoding="utf-8").strip() == "print('hello')"
          and not (other / "new.txt").exists() else "✗ 和规则不一致，看下面的明细")
    print("Claude Code 给模型的工具：", api.offered_tools)
    print("别的请求：", sorted(set(api.others)))
    print("确认请求：")
    for event, answer in requests:
        print(f"  {event.tool:10} kind={event.kind:14} 都允许={'可' if event.can_remember else '否'} "
              f"危险={'是' if event.danger else '否'} scope={event.scope!r} → 剧本答：{answer}")
    print("每一步的结果（Claude Code 发回模型的 tool_result）：")
    results = {tool_id: (err, text) for tool_id, err, text in api.results}
    for (label, _, _), (tool_id, tool, _) in zip(plan, api.calls):
        err, text = results.get(tool_id, (None, "（没有结果）"))
        print(f"  {label:14} {tool:10} {'出错/拒绝' if err else '成功'}：{text[:90]!r}")
    print("磁盘上：hello.py =", (proj / "hello.py").read_text(encoding="utf-8").strip() if (proj / "hello.py").exists()
          else "（被删了！）", "；other/new.txt 存在：", (other / "new.txt").exists())
    stderr = list(brain._stderr)
    if stderr:
        print("Claude Code 的错误输出（最后几行）：", " / ".join(stderr[-5:]))
    if "--keep" in sys.argv:
        print("临时文件夹（留着看）：", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
