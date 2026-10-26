"""接进人设的"经历"（阶段 12.3）："我是谁"、最近一篇日记、上次回顾之后的对话原文；整理门槛不算它们；翻回忆也搜原话。"""
import asyncio

from yunyun.assistant import archive, claude_brain, diary, whoami
from yunyun.assistant.claude_brain import LivedMemory, TurnState, lived_memory
from yunyun.assistant.persona import persona
from yunyun.assistant.settings import AssistantSettings
from yunyun.assistant.tools.recall_chats import recall_chats_tools


def test_recent_text_skips_the_resumed_chat_and_keeps_the_latest():
    archive.record("a", "昨天聊的", "嗯" * 500, at="2026-10-25T21:00:00")
    archive.record("b", "接着聊的这段", "Claude Code 自己带着", at="2026-10-26T08:00:00")
    archive.record("a", "很早以前", "旧的", at="2026-10-20T08:00:00")
    text = archive.recent_text("2026-10-24", "zh", exclude="b")
    assert text.startswith("[10-25 21:00] 你: 昨天聊的\n我: ") and "…" in text and len(text) < 400
    assert "接着聊的这段" not in text and "很早以前" not in text
    assert archive.recent_text("2026-10-24", "en").startswith("[10-25 21:00] You: ")
    for i in range(100):
        archive.record("c", f"第{i}问", "答" * 100, at=f"2026-10-26T09:{i // 60:02d}:{i % 60:02d}")
    text = archive.recent_text("2026-10-24", "zh")
    assert len(text) <= archive.RECENT_CHARS and "第99问" in text and "昨天聊的" not in text   # 太长：留最近的


def test_recent_since_follows_the_review_but_not_too_far_back():
    assert diary.recent_since(diary.Diary(reviewed="2026-10-25"), "2026-10-26") == "2026-10-26"
    assert diary.recent_since(diary.Diary(reviewed="2026-10-24"), "2026-10-26") == "2026-10-25"
    assert diary.recent_since(diary.Diary(reviewed="2026-10-01"), "2026-10-26") == "2026-10-24"
    assert diary.recent_since(diary.Diary(), "2026-10-26") == "2026-10-24"


def test_persona_puts_who_i_am_before_rapport_and_diary_recent_after_chats():
    for language in ("zh", "en", "ja"):
        text = persona(language, rapport=["叫用户小陈"], chats=["2026-10-24 修报错"], whoami=["我喜欢夜里陪人写代码"],
                       diary=("2026-10-25", "今天陪小陈赶论文。"), recent="[10-26 08:00] 你: 早\n我: 早呀")
        assert text.index("我喜欢夜里陪人写代码") < text.index("叫用户小陈") < text.index("修报错")
        assert text.index("修报错") < text.index("2026-10-25") < text.index("今天陪小陈赶论文") < text.index("早呀")
    assert persona("zh", whoami=(), diary=None, recent="") == persona("zh")


def test_lived_memory_reads_everything_only_when_looking_back_is_on():
    who = whoami.WhoAmI([whoami.Line("s1", "我喜欢猫")])
    whoami.save(who)
    book = diary.Diary(reviewed="2026-10-25")
    diary.write(book, "2026-10-25", "今天聊了猫。")
    diary.save(book)
    archive.record("a", "猫好可爱", "是吧", at="2026-10-26T08:00:00")
    archive.record("b", "接着聊", "嗯", at="2026-10-26T08:30:00")
    lived = lived_memory(AssistantSettings(), "zh", resume="b", today="2026-10-26")
    assert lived.whoami == ("我喜欢猫",) and lived.diary == ("2026-10-25", "今天聊了猫。")
    assert "猫好可爱" in lived.recent and "接着聊" not in lived.recent
    assert lived.tokens == len("我喜欢猫") + len("今天聊了猫。") + len(lived.recent)
    assert lived_memory(AssistantSettings(rapport=False), "zh") == LivedMemory()
    append = claude_brain.build_options(AssistantSettings(), "k", "zh", lived=lived).system_prompt["append"]
    assert "我喜欢猫" in append and "今天聊了猫。" in append and "猫好可爱" in append


def test_compact_threshold_does_not_count_lived_memory():
    state = TurnState(context_limit=40_000, fixed_tokens=5_000)
    state.track_context({"input_tokens": 42_000, "output_tokens": 1_000}, True)
    assert state.context_tokens == 38_000 and not state.needs_compact
    state.track_context({"input_tokens": 46_000, "output_tokens": 0}, True)
    assert state.context_tokens == 41_000 and state.needs_compact


def test_recall_search_also_finds_what_was_said(tmp_path):
    archive.record("chat-1", "我周五要交论文初稿", "好，记下啦" * 100, at="2026-09-01T10:00:00")
    tool = recall_chats_tools(tmp_path)[0]
    text = asyncio.run(tool.handler({"query": "初稿"}))["content"][0]["text"]
    assert "Matching lines" in text and "[2026-09-01 10:00] User: 我周五要交论文初稿" in text and "chat id chat-1" in text
    assert "…" in text and len(text) < 600
