"""开发用：用真的模型（Haiku）试"回想"（阶段 11.3）。**要花钱（约 1 美分），跑之前先问用户。**

    .\\.venv\\Scripts\\python tools\\eval_rapport.py

两段编出来的对话（不是你真实的对话）依次回想：看模型给的 JSON 对不对、记的是不是"怎么相处"、
同样的事出现在两段对话里会不会生效、"你明确说过的"会不会马上生效、摘要和关键词（回忆，11.4）写得怎么样。
用设置里存的 Claude Key（不显示）；相处笔记和回忆写在临时文件夹里，不碰你真实的。
"""
import asyncio
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant import rapport, recollections, reflect, transcripts  # noqa: E402
from yunyun.assistant.settings import AssistantSettings, get_api_key  # noqa: E402
from yunyun.assistant.transcripts import Transcript, TranscriptTurn  # noqa: E402

NOW = datetime.now()
TALKS = [
    ("talk-1", 3, [
        ("帮我看看这个 pytest 报错，我今天好累啊，已经调了一下午了",
         "辛苦了，先喝口水。报错是因为 fixture 名字拼错了：第 12 行写的是 tmp_pth，改成 tmp_path 就好。"),
        ("谢谢。回答能不能再短一点，直接告诉我改哪就行",
         "好，以后直接说改哪。"),
        ("还有，别叫我主人了，叫我小陈就行",
         "知道了，小陈。"),
    ]),
    ("talk-2", 1, [
        ("又报错了……今天真是不顺，有点烦",
         "辛苦了，别急。这次是导入路径的问题：把 from src.calc 改成 from calc。"),
        ("嗯，简短点就好，谢谢",
         "好。"),
    ]),
]


def make_talks():
    result = []
    for session, hours_ago, pairs in TALKS:
        when = NOW - timedelta(hours=hours_ago)
        result.append(Transcript(session, "", [TranscriptTurn(when, q, a) for q, a in pairs], updated=when))
    return result


async def main():
    key = get_api_key("anthropic")
    if not key:
        sys.exit("设置 → 助手里还没有存 Claude 的 Key")
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-rapport-"))
    rapport.rapport_path = lambda: tmp / "rapport.json"
    recollections.recollections_path = lambda: tmp / "recollections.json"
    talks = make_talks()
    replies, events = [], []

    async def ask(provider, api_key, model, system, user):
        result = await reflect.complete(provider, api_key, model, system, user)
        replies.append(result[0])
        return result

    try:
        for talk in sorted(talks, key=lambda t: t.updated):   # 按时间先后一段一段回想（和真用时一样）
            transcripts.list_transcripts = lambda projects_dir, talk=talk: [talk]
            await reflect.reflect_recent(AssistantSettings("anthropic"), key, "zh", events.append,
                                         projects_dir="unused", ask=ask)
            print(f"--- 回想 {talk.session_id}：模型的回答\n{replies[-1] if replies else '（没调模型）'}")
        data = rapport.load()
        print("\n生效的：", rapport.active_lines(data))
        print("观察中：", [n.text for n in rapport.candidates(data)])
        print("回忆：", [(r.summary, r.keywords) for r in recollections.load()])
        print(f"一共花了 ${sum(e.cost_usd for e in events):.4f}（{len(events)} 次）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
