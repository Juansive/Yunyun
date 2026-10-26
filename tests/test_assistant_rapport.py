"""相处笔记（assistant/rapport.py，阶段 11.3；12.2 起按日子算）：两天出现才生效、明确说过的马上生效、上限、过期、看不懂的跳过。"""
from yunyun.assistant import rapport
from yunyun.assistant.rapport import Rapport

NOW = "2026-10-14T20:00:00"


def apply(r, changes, session="2026-10-13", now=NOW):
    return rapport.apply(r, changes, session, now)


def test_a_note_needs_two_different_days_unless_the_user_said_it():
    r = Rapport()
    out = apply(r, {"add": [{"text": "累的时候先安慰，不要调侃"}, {"text": "喜欢被叫主人", "explicit": True}]})
    assert (out.added, out.activated) == (2, 1)                                     # 明确说过的马上生效
    assert rapport.active_lines(r) == ["喜欢被叫主人"]
    assert [n.text for n in rapport.candidates(r)] == ["累的时候先安慰，不要调侃"]
    assert apply(r, {"seen": ["r1"]}).activated == 0                                # 同一天再说一次：不算
    out = apply(r, {"seen": ["r1"]}, session="2026-10-14")
    assert out.activated == 1 and len(rapport.active_lines(r)) == 2
    assert r.notes[0].sessions == ["2026-10-13", "2026-10-14"]


def test_revise_counts_as_seen_and_drop_removes():
    r = Rapport()
    apply(r, {"add": [{"text": "回答短一点"}, {"text": "不喜欢冷笑话"}]})
    out = apply(r, {"revise": [{"id": "r1", "text": "回答尽量短，先给结论"}], "drop": ["r2"]}, session="2026-10-14")
    assert out.activated == 1 and rapport.active_lines(r) == ["回答尽量短，先给结论"]
    assert [n.id for n in r.notes] == ["r1"]


def test_garbage_from_the_model_is_skipped():
    r = Rapport()
    out = apply(r, {"seen": ["r99", None], "revise": ["x", {"id": "r99", "text": "y"}], "drop": "all",
                    "add": ["字符串", {"text": "  "}, {"text": "好" * 200}, {"text": "好" * 200}]})
    assert out.added == 1 and len(r.notes[0].text) == rapport.MAX_TEXT             # 太长截断、重复的不加
    assert apply(r, "not a dict", session="s9").added == 0
    assert apply(r, {"add": [{"text": f"第{i}条"} for i in range(9)]}, session="s3").added == rapport.MAX_ADDS


def test_old_candidates_expire_and_limits_hold():
    r = Rapport()
    apply(r, {"add": [{"text": "很久以前的观察"}]}, now="2026-08-01T10:00:00")
    apply(r, {"add": [{"text": "新的观察"}]})
    assert [n.text for n in r.notes] == ["新的观察"]                                # 30 天没再出现：扔掉
    for i in range(20):                                                            # 生效的超过上限：留最近的
        apply(r, {"add": [{"text": f"说过的{i}", "explicit": True}]}, session=f"x{i}", now=f"2026-10-14T20:{i:02d}:00")
    assert len(rapport.active_lines(r)) == rapport.MAX_ACTIVE and "说过的19" in rapport.active_lines(r)
    for i in range(40):
        apply(r, {"add": [{"text": f"观察{i}"}]}, session=f"c{i}", now=f"2026-10-14T21:{i:02d}:00")
    assert len(rapport.candidates(r)) == rapport.MAX_CANDIDATES


def test_saved_to_disk_and_edited_in_settings():
    r = Rapport()
    apply(r, {"add": [{"text": "喜欢被叫主人", "explicit": True}, {"text": "观察中"}]})
    rapport.save(r)
    loaded = rapport.load()
    assert loaded == r and loaded.notes[0].active
    assert rapport.edit("r1", "  喜欢被叫 主人大人 ") and rapport.load().notes[0].text == "喜欢被叫 主人大人"
    assert not rapport.edit("r9", "x") and not rapport.edit("r1", "  ")
    assert rapport.delete("r2") and not rapport.delete("r2")
    rapport.clear()
    assert rapport.load().notes == []


def test_broken_file_is_treated_as_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    assert rapport.load(path) == Rapport()
    path.write_text('{"notes": [{"id": "r1"}, "x", {"id": "r2", "text": "ok", "sessions": "s"}], "reflected": []}',
                    encoding="utf-8")
    loaded = rapport.load(path)
    assert [(n.id, n.sessions) for n in loaded.notes] == [("r2", [])]
