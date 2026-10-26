"""日记（assistant/diary.py）和"我是谁"（assistant/whoami.py），阶段 12.2：慢慢改、旧版、设置里改和退回。"""
from yunyun.assistant import diary, whoami
from yunyun.assistant.whoami import WhoAmI


def propose(who, day, *items, seen=()):
    return whoami.apply(who, {"seen": list(seen), "propose": list(items)}, day)


def test_add_rewrite_and_remove_each_need_three_days():
    who = WhoAmI()
    assert propose(who, "2026-10-01", {"text": "我喜欢夜里陪人写代码"}) == 0
    assert propose(who, "2026-10-01", {"text": "我喜欢夜里陪人写代码"}) == 0        # 同一天再提：不算
    assert propose(who, "2026-10-02", {"text": "我喜欢夜里陪人写代码"}) == 0        # 一样的提议：算又出现了一次
    assert propose(who, "2026-10-03", seen=["k1"]) == 1
    assert [(x.id, x.text) for x in who.lines] == [("s1", "我喜欢夜里陪人写代码")] and who.observing == []
    for day in ("2026-10-04", "2026-10-05", "2026-10-06"):
        propose(who, day, {"text": "我喜欢白天陪人写代码", "replaces": "s1"})
    assert [x.text for x in who.lines] == ["我喜欢白天陪人写代码"]
    for day in ("2026-10-07", "2026-10-08", "2026-10-09"):
        propose(who, day, {"text": "", "replaces": "s1"})
    assert who.lines == [] and [v["text"] for v in who.versions] == ["", "我喜欢夜里陪人写代码", "我喜欢白天陪人写代码"]


def test_at_most_two_changes_a_day_and_proposals_expire():
    who = WhoAmI()
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        propose(who, day, {"text": "一"}, {"text": "二"}, {"text": "三"})
    assert len(who.lines) == whoami.MAX_PER_DAY and len(who.observing) == 1       # 第三条明天再说
    assert propose(who, "2026-10-04") == 1 and len(who.lines) == 3
    propose(who, "2026-10-05", {"text": "很久以前的想法"})
    propose(who, "2026-11-20", {"text": "新想法"})
    assert [c.text for c in who.observing] == ["新想法"]                             # 30 天没再出现：扔掉
    propose(who, "2026-11-20", {"text": "x", "replaces": "s99"}, "garbage")          # 改一句没有的：当成加一句
    assert [c.replaces for c in who.observing] == ["", ""]


def test_settings_edit_and_restore():
    who = WhoAmI()
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        propose(who, day, {"text": "我喜欢猫"})
    propose(who, "2026-10-04", {"text": "我更喜欢狗", "replaces": "s1"})
    whoami.save(who)
    whoami.set_text("我喜欢猫\n  \n我也喜欢下雨天")                                  # 没改的那句编号不变
    who = whoami.load()
    assert [(x.id, x.text) for x in who.lines] == [("s1", "我喜欢猫"), ("s2", "我也喜欢下雨天")]
    assert len(who.observing) == 1                                                  # 改 s1 的提议还对得上
    whoami.set_text("我也喜欢下雨天")
    assert whoami.load().observing == []                                            # s1 没了：改它的提议扔掉
    assert whoami.restore(2) and whoami.text_of(whoami.load()) == "我喜欢猫\n我也喜欢下雨天"
    assert not whoami.restore(99)
    whoami.clear()
    assert whoami.load() == WhoAmI()


def test_whoami_lines_for_review():
    who = WhoAmI()
    propose(who, "2026-10-01", {"text": "我喜欢猫"})
    now, pending = whoami.lines_for_review(who)
    assert "empty" in now and pending == "[k1] add: 我喜欢猫 (evidence on 1 of 3 days)"


def test_diary_write_latest_delete_and_broken_file(tmp_path):
    book = diary.Diary()
    diary.write(book, "2026-10-24", "  第一天  ")
    diary.write(book, "2026-10-25", "第二天" * 500)
    diary.write(book, "2026-10-26", "   ")
    assert book.entries["2026-10-24"] == "第一天" and len(book.entries["2026-10-25"]) == diary.MAX_ENTRY
    assert diary.latest(book, "2026-10-26")[0] == "2026-10-25" and diary.latest(book, "2026-10-24") is None
    book.reviewed = "2026-10-25"
    diary.save(book)
    assert diary.load() == book
    assert diary.delete("2026-10-24") and not diary.delete("2026-10-24")
    diary.clear()
    assert diary.load().entries == {} and diary.load().reviewed == "2026-10-25"    # 回顾到哪天还记着
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert diary.load(bad) == diary.Diary() and whoami.load(bad) == WhoAmI()
