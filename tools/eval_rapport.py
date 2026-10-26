"""开发用：用真的模型（Haiku）试"每日回顾"（阶段 12.2）。**要花钱（约 2 美分），跑之前先问用户。**

    .\\.venv\\Scripts\\python tools\\eval_rapport.py

三天编出来的对话（不是你真实的对话）一次回顾完：看日记写得像不像芸芸、摘要和关键词、相处笔记（同样的事两天出现会不会生效、
"你明确说过的"会不会马上生效）、"我是谁"的提议是不是"长久的倾向"而不是一时的心情（三天都有依据才改）。
用设置里存的 Claude Key（不显示）；原文存档、日记、我是谁、相处笔记、回忆都写在临时文件夹里，不碰你真实的。
"""
import asyncio
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant import archive, diary, rapport, recollections, reflect, transcripts, whoami  # noqa: E402
from yunyun.assistant.settings import AssistantSettings, get_api_key  # noqa: E402

DAYS = [   # (几天前, 对话编号, [(问, 答)])
    (3, "talk-1", [
        ("帮我看看这个 pytest 报错，我今天好累啊，已经调了一下午了",
         "辛苦了，先喝口水。报错是因为 fixture 名字拼错了：第 12 行写的是 tmp_pth，改成 tmp_path 就好。"),
        ("谢谢。回答能不能再短一点，直接告诉我改哪就行", "好，以后直接说改哪。"),
        ("还有，别叫我主人了，叫我小陈就行", "知道了，小陈。"),
    ]),
    (2, "talk-2", [
        ("又报错了……今天真是不顺，有点烦", "辛苦了，别急。这次是导入路径的问题：把 from src.calc 改成 from calc。"),
        ("嗯，简短点就好，谢谢。对了你觉得半夜写代码是不是很蠢", "不蠢，安静的时候我也最能帮上忙。不过别熬太晚。"),
    ]),
    (1, "talk-3", [
        ("周五要交论文初稿，今晚又要熬夜了", "那我陪你。先把第三章的提纲列出来？"),
        ("好，你陪着我就安心多了", "嗯，我在。先写引言那段，写完给我看。"),
    ]),
]


async def main():
    key = get_api_key("anthropic")
    if not key:
        sys.exit("设置 → 助手里还没有存 Claude 的 Key")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-review-"))
    archive.archive_path = lambda: tmp / "archive.sqlite"
    diary.diary_path = lambda: tmp / "diary.json"
    whoami.whoami_path = lambda: tmp / "whoami.json"
    rapport.rapport_path = lambda: tmp / "rapport.json"
    recollections.recollections_path = lambda: tmp / "recollections.json"
    transcripts.list_transcripts = lambda projects_dir: []
    today = datetime.now().replace(hour=21, minute=0, second=0, microsecond=0)
    for days_ago, session, pairs in DAYS:
        for i, (question, answer) in enumerate(pairs):
            at = (today - timedelta(days=days_ago, minutes=len(pairs) - i)).isoformat(timespec="seconds")
            archive.record(session, question, answer, at=at)
    replies, events = [], []

    async def ask(provider, api_key, model, system, user):
        result = await reflect.complete(provider, api_key, model, system, user)
        replies.append(result[0])
        return result

    try:
        await reflect.reflect_recent(AssistantSettings("anthropic"), key, "zh", events.append, projects_dir="unused",
                                     ask=ask)
        for i, reply in enumerate(replies):
            print(f"--- 第 {i + 1} 天：模型的回答\n{reply}")
        notes = rapport.load()
        who = whoami.load()
        print("\n日记：", diary.load().entries)
        print("相处笔记 生效的：", rapport.active_lines(notes), " 观察中：", [n.text for n in rapport.candidates(notes)])
        print("我是谁：", whoami.text_of(who) or "（空）")
        print("我是谁 观察中：", [(c.text, c.replaces, len(c.days)) for c in who.observing])
        print("回忆：", [(r.summary, r.keywords) for r in recollections.load()])
        print(f"一共花了 ${sum(e.cost_usd for e in events):.4f}（{len(events)} 次）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
