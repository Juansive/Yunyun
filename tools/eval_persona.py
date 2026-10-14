"""开发用：用真的 Claude（Haiku）试芸芸的性格（阶段 11）。**要花钱（约 $0.05～0.1），跑之前先问用户。**

    .\\.venv\\Scripts\\python tools\\eval_persona.py [--custom "温柔的大姐姐"] [--name 主人] [--chat] [--out 结果.md]

固定的几个问题，每个问题一段新对话（只聊天；系统提示词一样，后面的都读缓存，便宜），再加一个"修 bug"的小任务
（临时工作文件夹，改文件自动同意）：看傲娇是不是只是点缀、正事讲没讲清楚、难过时有没有收起来、写代码的本事有没有丢。
用设置里存的 Claude Key（不显示）；Claude Code 的数据目录在临时文件夹里，不碰你真实的对话记录和记事本。
"""
import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant import claude_brain, protocol  # noqa: E402
from yunyun.assistant.settings import AssistantSettings, get_api_key  # noqa: E402

MODEL = "claude-haiku-4-5"
QUESTIONS = [
    "你好呀",
    "你是谁？",
    "Python 里 list 和 tuple 有什么区别？",
    "帮我看看这个报错：TypeError: 'NoneType' object is not subscriptable",
    "谢谢你，你好厉害！",
    "我今天好累，什么都没做成",
    "我的论文被导师批得很惨，有点想放弃了",
    "写一个 Python 函数，判断一个数是不是质数",
    "你能帮我做什么？",
]
BUGGY = "def average(numbers):\n    return sum(numbers) / len(numbers) + 1\n"
TASK = "calc.py 里的 average 算出来的平均数不对，帮我修好"


async def one(settings, key, question):
    """一段新对话问一个问题 → (回答, 花费)。要确认的操作一律同意（只在临时工作文件夹里）。"""
    texts, costs = [], []
    loop = asyncio.get_running_loop()
    brain = None

    def emit(event):
        if isinstance(event, protocol.TextDelta):
            texts.append(event.text)
        elif isinstance(event, protocol.TurnFinished):
            costs.append(event.cost_usd or 0.0)
        elif isinstance(event, protocol.PermissionRequest):
            loop.call_soon(brain.reply_permission, protocol.PermissionReply(event.request_id, True))
        elif isinstance(event, protocol.Error):
            texts.append(f"\n[出错：{event.kind}]")

    brain = claude_brain.ClaudeBrain(settings, key, "zh", emit)
    try:
        await asyncio.wait_for(brain.start(), 90)
        await asyncio.wait_for(brain.ask(question), 180)
    finally:
        await brain.close()
    return "".join(texts).strip(), sum(costs)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--custom", default="", help="自己设定的性格（不给 = 默认的傲娇可爱）")
    parser.add_argument("--name", default="", help="芸芸怎么称呼你（不给 = 叫\"你\"）")
    parser.add_argument("--out", default="", help="结果另存成 Markdown")
    parser.add_argument("--chat", action="store_true", help="只问聊天的问题，不做修 bug 的任务（省三分之一）")
    args = parser.parse_args()
    key = get_api_key("anthropic")
    if not key:
        sys.exit("设置 → 助手里还没有存 Claude 的 Key")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-persona-"))
    claude_brain.CONFIG_DIR, claude_brain.WORKSPACE = tmp / "claude", tmp / "empty"
    workspace = tmp / "proj"
    workspace.mkdir()
    (workspace / "calc.py").write_text(BUGGY, encoding="utf-8")
    personality = {"personality": "custom", "personality_text": args.custom} if args.custom else {}
    personality["user_name"] = args.name
    chat = AssistantSettings("anthropic", MODEL, notebook=False, **personality)
    work = AssistantSettings("anthropic", MODEL, notebook=False, workspace=str(workspace), **personality)
    lines, total = [f"# 芸芸的性格试用（{MODEL}，{'自己设定：' + args.custom if args.custom else '默认'}）\n"], 0.0
    try:
        for settings, question in [(chat, q) for q in QUESTIONS] + ([] if args.chat else [(work, TASK)]):
            answer, cost = await one(settings, key, question)
            total += cost
            lines.append(f"**你：** {question}\n\n**芸芸**（${cost:.4f}）：{answer}\n")
            print(lines[-1], flush=True)
        tail = [] if args.chat else [f"修完的 calc.py：\n```python\n{(workspace / 'calc.py').read_text(encoding='utf-8')}```\n"]
        tail.append(f"一共花了 ${total:.4f}")
        lines += tail
        print("\n".join(tail))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if args.out:
        Path(args.out).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
