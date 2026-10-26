"""助手的历史对话（pet/assistant_history.py）和 Claude Code 旧记录的清理（claude_brain.prune_old_sessions 在大脑测试里）。

conftest 已经把 history_path、claude_data_dir 换到了临时目录。
"""
import json
import os
import time
from datetime import datetime, timedelta

from yunyun.assistant.session import Action
from yunyun.pet import assistant_history
from yunyun.pet.assistant_deck import AssistantCard

NOW = datetime(2026, 10, 8, 12, 0)


def turn(question, days_ago=0, **kw):
    return AssistantCard("turn", NOW - timedelta(days=days_ago), question, f"答：{question}", question, "$0.01", **kw)


def divider(days_ago=0, folder=""):
    return AssistantCard("divider", NOW - timedelta(days=days_ago), folder)


def test_cards_survive_a_round_trip():
    action = Action(NOW, "Bash", "python -m unittest -v", "remembered", True, "toolu_1")
    card = turn("修 bug", files=("F:\\proj\\calc.py",), checkpoint="cp-1", generation=7, undone=True,
                actions=(action,), session_id="sess-1", workspace="F:\\proj")
    assistant_history.save([divider(folder="proj"), card], now=NOW)
    loaded = assistant_history.load(now=NOW)
    assert [c.kind for c in loaded] == ["turn"]                # 分隔卡不存（卡片堆只放一段对话，用不着了）
    back = loaded[0]
    assert back.card_id == card.card_id
    assert (back.question, back.answer, back.files, back.checkpoint, back.undone, back.session_id, back.workspace) == (
        "修 bug", "答：修 bug", ("F:\\proj\\calc.py",), "cp-1", True, "sess-1", "F:\\proj")
    assert back.generation == 0                       # 撤销用的会话号不存：重启后都对不上了
    assert [(a.time, a.tool, a.detail, a.decision, a.ok) for a in back.actions] == [
        (NOW, "Bash", "python -m unittest -v", "remembered", True)]
    assert not assistant_history.history_path().with_suffix(".tmp").exists()   # 临时文件换掉了


def test_only_the_last_30_days_are_kept():
    cards = [turn("很久以前", days_ago=40), divider(days_ago=35), turn("一个月前", days_ago=31),
             divider(days_ago=29), turn("上周", days_ago=7), turn("今天")]
    assistant_history.save(cards, now=NOW)
    assert [c.question for c in assistant_history.load(now=NOW)] == ["上周", "今天"]


def test_saving_merges_instead_of_replacing():
    """卡片堆里只是当前这段对话：存的时候不能把别的对话冲掉；同一张卡片（撤销后）更新，不会存两份。"""
    old = turn("昨天那段", days_ago=1, session_id="a")
    assistant_history.save([old], now=NOW)
    current = turn("今天这段", session_id="b")
    assistant_history.save([current], now=NOW)
    current.undone = True
    assistant_history.save([current], now=NOW)
    loaded = assistant_history.load(now=NOW)
    assert [(c.question, c.undone) for c in loaded] == [("昨天那段", False), ("今天这段", True)]


def test_dividers_are_tidied():
    cards = [divider(), divider(folder="a"), turn("一"), divider(), divider(folder="b"), turn("二"), divider()]
    tidy = assistant_history.prune(cards, now=NOW)
    assert [(c.kind, c.question) for c in tidy] == [
        ("divider", "a"), ("turn", "一"), ("divider", "b"), ("turn", "二"), ("divider", "")]   # 连着的只留后一张
    assert assistant_history.prune([divider(), divider()], now=NOW) == []   # 只剩分隔卡：没东西了


def test_missing_or_broken_file_means_no_history():
    assert assistant_history.load() == []
    assistant_history.history_path().write_text("{坏掉的", encoding="utf-8")
    assert assistant_history.load() == []


def test_a_broken_card_is_skipped():
    good = turn("好的")
    assistant_history.save([good], now=NOW)
    data = json.loads(assistant_history.history_path().read_text(encoding="utf-8"))
    data["cards"].insert(0, {"kind": "turn"})          # 缺了时间
    assistant_history.history_path().write_text(json.dumps(data), encoding="utf-8")
    assert [c.question for c in assistant_history.load(now=NOW)] == ["好的"]


def test_clear_deletes_cards_and_claude_code_records():
    assistant_history.save([turn("一")], now=NOW)
    claude = assistant_history.claude_data_dir()
    for name in ("projects/p", "file-history/s", "backups"):
        (claude / name).mkdir(parents=True)
        (claude / name / "x.json").write_text("{}", encoding="utf-8")
    from yunyun.assistant import rapport, recollections
    recollections.remember("s1", datetime.now().isoformat(timespec="seconds"), "聊答辩准备")
    data = rapport.Rapport()
    rapport.apply(data, {"add": [{"text": "喜欢被叫主人", "explicit": True}]}, "2026-10-14")
    rapport.save(data)
    assistant_history.clear()
    assert not assistant_history.history_path().exists()
    assert not (claude / "projects").exists() and not (claude / "file-history").exists()
    assert (claude / "backups" / "x.json").exists()    # Claude Code 自己的设置备份不动
    assert [r.summary for r in recollections.load()] == ["聊答辩准备"]   # 回忆 12.5 起算人设记忆：不删
    assert rapport.active_lines(rapport.load()) == ["喜欢被叫主人"]   # 相处笔记不是对话内容：不动


# ---------- 一段段对话：存下的卡片 + 从 Claude Code 记录里找回来的 ----------

def transcript(session_id, cwd, question, answer, ts, cost=None):
    lines = [
        {"type": "user", "sessionId": session_id, "cwd": cwd, "timestamp": ts, "promptSource": "sdk",
         "message": {"role": "user", "content": question}},
        {"type": "assistant", "sessionId": session_id, "cwd": cwd, "timestamp": ts,
         "message": {"role": "assistant", "content": [{"type": "text", "text": answer}]}},
    ]
    if cost is not None:
        lines.append({"type": "cost-state", "sessionId": session_id, "totalCostUSD": cost})
    folder = assistant_history.claude_data_dir() / "projects" / "some-project"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines),
                                                encoding="utf-8")


def iso(dt):
    return dt.astimezone().astimezone(tz=None).isoformat()


def test_conversations_merge_saved_cards_and_recovered_ones(monkeypatch):
    from yunyun.assistant import transcripts
    monkeypatch.setattr(transcripts, "_cache", {})
    today = datetime.now().replace(microsecond=0)
    yesterday, long_ago = today - timedelta(days=1), today - timedelta(days=40)
    # 今天存下的卡片（会话 a），Claude Code 那边也有它的记录：用卡片（内容更全），花费用记录里的
    saved = [AssistantCard("turn", today, "今天的问题", "今天的回答", "今天的回答", "$0.01", session_id="a",
                           workspace="F:\\proj")]
    assistant_history.save(saved)
    transcript("a", "F:\\proj", "今天的问题", "记录里的回答", iso(today), cost=0.02)
    # 昨天的对话：芸芸没存卡片，只有 Claude Code 的记录（只聊天）
    transcript("b", str(assistant_history.chat_only_dir()), "昨天的问题", "**昨天**的回答", iso(yesterday), cost=0.005)
    # 40 天前的：超过保留期，不列
    transcript("c", "F:\\old", "很久以前", "……", iso(long_ago))
    found = assistant_history.conversations()
    assert [c.session_id for c in found] == ["a", "b"]                     # 最新的在前
    a, b = found
    assert (a.title, a.cards[0].answer, a.workspace, a.cost_usd, a.recovered) == (
        "今天的问题", "今天的回答", "F:\\proj", 0.02, False)
    assert (b.title, b.workspace, b.cost_usd, b.recovered) == ("昨天的问题", "", 0.005, True)   # 只聊天
    assert b.cards[0].summary == "昨天的回答"                             # 卡片上不显示 Markdown 符号


def test_cards_without_session_id_join_the_previous_conversation():
    cards = [AssistantCard("turn", NOW, "一", session_id="a"), AssistantCard("turn", NOW, "出错的一问"),
             AssistantCard("divider", NOW), AssistantCard("turn", NOW, "没有编号也没有前一张")]
    groups = assistant_history._group_cards(cards)
    assert {sid: [c.question for c in cs] for sid, cs in groups.items()} == {"a": ["一", "出错的一问"]}


def test_old_claude_code_records_are_pruned(tmp_path):
    from yunyun.assistant import claude_brain
    config = tmp_path / "claude"
    old, new, backup = config / "projects/p/old.jsonl", config / "file-history/s/new.bak", config / "backups/b.json"
    for path in (old, new, backup):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    month_ago = time.time() - 31 * 86400
    for path in (old, backup):
        os.utime(path, (month_ago, month_ago))
    assert claude_brain.prune_old_sessions(config, 30) == 1
    assert not old.exists() and not old.parent.exists()   # 空了的文件夹也删掉
    assert new.exists() and backup.exists()               # 新的留着；backups 不归它管
