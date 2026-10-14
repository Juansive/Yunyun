"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证提醒的三个工具（阶段 11.5）。不花钱。

    .\\.venv\\Scripts\\python tools\\try_reminders.py

模型依次：设一个 25 分钟后的提醒、设一个每天 15:00 的、看提醒、取消第一个。
看：工具交给了模型没有、都不用问、提醒文件里对不对、每次改了都发了 RemindersChanged（芸芸据此重新排时间）。
"""
import asyncio
import shutil
import tempfile
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import protocol
from yunyun.notify import reminders

SET, LIST, CANCEL = protocol.SET_REMINDER_TOOL, protocol.LIST_REMINDERS_TOOL, protocol.CANCEL_REMINDER_TOOL


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-reminders-"))
    reminders.reminders_path = lambda: tmp / "reminders.json"   # 不碰你真的提醒
    api = FakeApi([("tool", SET, {"text": "休息一下", "in_minutes": 25}),
                   ("tool", SET, {"text": "喝水", "at": "15:00", "repeat": "daily"}),
                   ("tool", LIST, {}), ("tool", CANCEL, {"id": "t1"}), ("text", "好了")])
    server = serve(api)
    events = []
    brain = make_brain(server, tmp, "", events.append)
    checks = {}
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("25 分钟后叫我休息，另外每天下午三点提醒我喝水"), 60)
        decisions = [(e.tool.split("__")[-1], e.decision) for e in events if isinstance(e, protocol.ToolDecision)]
        changed = sum(isinstance(e, protocol.RemindersChanged) for e in events)
        left = [(r.id, r.text, r.repeat) for r in reminders.load()]
        print("1. 交给模型的提醒工具：", [t for t in api.offered_tools or [] if "reminder" in t])
        print("2. 操作记录：", decisions)
        print("3. 工具结果：", [api.result_of(i)[1][:60] for i in range(4)])
        print("4. 剩下的提醒：", left, "；通知芸芸", changed, "次")
        checks["工具在"] = all(t in (api.offered_tools or []) for t in (SET, LIST, CANCEL))
        checks["不用问"] = [d for _, d in decisions] == ["auto"] * 4
        checks["设和取消"] = left == [("t2", "喝水", "daily")] and api.result_of(2)[1].startswith("Now is")
        checks["通知芸芸"] = changed == 3
    finally:
        await brain.close()
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 提醒的工具能用（设、看、取消，都不用问，改了会告诉芸芸）" if all(checks.values()) and len(checks) == 4
          else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
