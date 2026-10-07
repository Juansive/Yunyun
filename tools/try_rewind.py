"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证"撤销这次改动"（文件检查点 + rewind_files）能恢复什么。不花钱。

    .\\.venv\\Scripts\\python tools\\try_rewind.py [--keep]     # --keep：留下临时文件夹看看

剧本（确认请求一律自动允许）：
  第 1 问：读 hello.py，把 hi 改成 hello
  第 2 问：新建 new.py，把 hello 改成 hey，再用命令生成 made.txt（命令改的文件 Claude Code 不备份）
  撤销第 2 问 → 撤销第 1 问 → 用一个不存在的检查点撤销（应该失败）
  第 3 问：撤销之后直接改 hello.py（看 Claude Code 会不会因为"文件变了"而拒绝）
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from fake_anthropic_api import SHELL, FakeApi, make_brain, serve
from yunyun.assistant import protocol


def state(proj):
    hello = proj / "hello.py"
    return {
        "hello.py": hello.read_text(encoding="utf-8").strip() if hello.exists() else "（没有）",
        "new.py": (proj / "new.py").exists(),
        "made.txt": (proj / "made.txt").exists(),
    }


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-rewind-"))
    proj = tmp / "proj"
    proj.mkdir()
    (proj / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    hello = str(proj / "hello.py")
    api = FakeApi([
        ("tool", "Read", {"file_path": hello}),
        ("tool", "Edit", {"file_path": hello, "old_string": "print('hi')", "new_string": "print('hello')"}),
        ("text", "第 1 问改好了"),
        ("tool", "Write", {"file_path": str(proj / "new.py"), "content": "x = 1\n"}),
        ("tool", "Edit", {"file_path": hello, "old_string": "print('hello')", "new_string": "print('hey')"}),
        ("tool", SHELL, {"command": "echo made > made.txt", "description": "make a file"}),
        ("text", "第 2 问改好了"),
        ("tool", "Edit", {"file_path": hello, "old_string": "print('hi')", "new_string": "print('yo')"}),
        ("text", "第 3 问改好了"),
    ])
    server = serve(api)
    loop = asyncio.get_running_loop()
    turns, rewound = [], []
    brain = None

    def emit(event):
        if isinstance(event, protocol.PermissionRequest):
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))
        elif isinstance(event, protocol.TurnFinished):
            turns.append(event)
        elif isinstance(event, protocol.Rewound):
            rewound.append(event)
        elif isinstance(event, protocol.Error):
            print("出错：", event)

    brain = make_brain(server, tmp, proj, emit)
    report = []
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("第 1 问"), 60)
        report.append(("第 1 问之后", state(proj)))
        await asyncio.wait_for(brain.ask("第 2 问"), 60)
        report.append(("第 2 问之后", state(proj)))
        await brain.rewind(turns[1].checkpoint)
        report.append(("撤销第 2 问", state(proj)))
        await brain.rewind(turns[0].checkpoint)
        report.append(("撤销第 1 问", state(proj)))
        await brain.rewind("00000000-0000-0000-0000-000000000000")
        await asyncio.wait_for(brain.ask("第 3 问"), 60)
        report.append(("第 3 问（撤销后直接改）", state(proj)))
    finally:
        await brain.close()
        server.shutdown()

    for turn in turns:
        print(f"一轮结束：checkpoint={turn.checkpoint[:8] or '（空）'}… 改了 {[Path(f).name for f in turn.files]}")
    for label, files in report:
        print(f"  {label:16} {files}")
    print("撤销结果：", [(r.ok, r.detail[:80]) for r in rewound])
    last_results = [result for result in api.results if result[0] == api.calls[-1][0]]
    print("第 3 问那次改动 Claude Code 的回复：", last_results[0][1:] if last_results else "（没有）")
    expected = [
        {"hello.py": "print('hello')", "new.py": False, "made.txt": False},
        {"hello.py": "print('hey')", "new.py": True, "made.txt": True},
        {"hello.py": "print('hello')", "new.py": False, "made.txt": True},
        {"hello.py": "print('hi')", "new.py": False, "made.txt": True},
    ]
    ok = [files for _, files in report[:4]] == expected and [r.ok for r in rewound] == [True, True, False]
    print("结论：", "✓ 和预期一致（命令生成的 made.txt 撤销不了）" if ok else "✗ 和预期不一致，看上面的明细")
    if "--keep" in sys.argv:
        print("临时文件夹（留着看）：", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
