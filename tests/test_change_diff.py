"""确认卡片里的改动对比（pet/change_diff.py）：读原文件、套上改动、逐行对比、显示。"""
import pytest

from yunyun import i18n
from yunyun.pet import change_diff
from yunyun.pet.change_diff import Change, DiffLine, change_html, diff_lines, diff_stats, proposed_change, read_text


@pytest.fixture(autouse=True)
def chinese(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")


def kinds(lines):
    return "".join(line.kind for line in lines)


# ---------- 逐行对比 ----------

def test_only_changes_and_their_context_are_shown():
    before = [f"line {i}" for i in range(1, 21)]
    after = list(before)
    after[9] = "line 10 changed"
    lines = diff_lines(before, after)
    assert kinds(lines) == "~   -+   ~"                     # 前面省略、3 行上下文、改的那行、3 行上下文、后面省略
    assert [line.number for line in lines if line.kind != "~"] == [7, 8, 9, 10, 10, 11, 12, 13]
    assert diff_stats(lines) == (1, 1)


def test_far_apart_changes_are_separate_hunks():
    before = [f"l{i}" for i in range(30)]
    after = list(before)
    after[2], after[25] = "A", "B"
    lines = diff_lines(before, after)
    assert kinds(lines).count("~") == 2 and diff_stats(lines) == (2, 2)   # 中间省略一次、最后省略一次


def test_numbers_follow_the_new_file_and_removed_lines_keep_old_numbers():
    lines = diff_lines(["a", "b", "c"], ["a", "x", "y", "c"])
    assert [(line.kind, line.number, line.text) for line in lines] == [
        (" ", 1, "a"), ("-", 2, "b"), ("+", 2, "x"), ("+", 3, "y"), (" ", 4, "c")]


def test_changed_characters_inside_a_line_are_marked():
    lines = diff_lines(["total = price * count"], ["total = price * amount"])
    old, new = lines
    assert [old.text[a:b] for a, b in old.spans] == ["c"]          # 只标真正变了的几个字：count → amount
    assert [new.text[a:b] for a, b in new.spans] == ["am"]


def test_completely_different_lines_are_not_marked_inside():
    old, new = diff_lines(["import os"], ["这一行完全不一样了！！"])
    assert old.spans == () and new.spans == ()


def test_no_change_means_no_lines():
    assert diff_lines(["a"], ["a"]) == []


def test_new_file_is_all_added_without_separators():
    lines = diff_lines([], ["a", "b"])
    assert kinds(lines) == "++" and [line.number for line in lines] == [1, 2]


# ---------- 读原文件、算改后 ----------

def test_edit_applies_the_replacement_to_the_real_file(tmp_path):
    path = tmp_path / "calc.py"
    path.write_text("def add(a, b):\n    return a - b\n\nprint(add(1, 2))\n", encoding="utf-8")
    change = proposed_change("Edit", {"file_path": str(path), "old_string": "return a - b", "new_string": "return a + b"})
    assert change.note == "" and change.numbered
    assert change.after == ["def add(a, b):", "    return a + b", "", "print(add(1, 2))"]
    assert [(line.kind, line.number) for line in diff_lines(change.before, change.after) if line.kind in "+-"] == [
        ("-", 2), ("+", 2)]                                         # 行号是文件里真的行号


def test_edit_with_windows_line_endings(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    change = proposed_change("Edit", {"file_path": str(path), "old_string": "one\ntwo", "new_string": "1\n2"})
    assert change.note == "" and change.after == ["1", "2", "three"]


def test_edit_replace_all(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("x = 1\ny = x\n", encoding="utf-8")
    once = proposed_change("Edit", {"file_path": str(path), "old_string": "x", "new_string": "z"})
    every = proposed_change("Edit", {"file_path": str(path), "old_string": "x", "new_string": "z", "replace_all": True})
    assert once.after == ["z = 1", "y = x"] and every.after == ["z = 1", "y = z"]


def test_edit_that_does_not_match_compares_just_the_snippet(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("hello\n", encoding="utf-8")
    change = proposed_change("Edit", {"file_path": str(path), "old_string": "bye", "new_string": "ciao"})
    assert (change.note, change.numbered, change.before, change.after) == ("not_found", False, ["bye"], ["ciao"])


def test_edit_can_create_a_file(tmp_path):
    change = proposed_change("Edit", {"file_path": str(tmp_path / "new.py"), "old_string": "", "new_string": "a\nb"})
    assert (change.note, change.before, change.after) == ("new_file", [], ["a", "b"])


def test_write_compares_with_the_existing_file(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("keep\nold\n", encoding="utf-8")
    change = proposed_change("Write", {"file_path": str(path), "content": "keep\nnew\n"})
    assert change.note == "" and diff_stats(diff_lines(change.before, change.after)) == (1, 1)


def test_write_new_file(tmp_path):
    change = proposed_change("Write", {"file_path": str(tmp_path / "n.txt"), "content": "a\n"})
    assert (change.note, change.before, change.after) == ("new_file", [], ["a"])


def test_gbk_file_is_read(tmp_path, monkeypatch):
    monkeypatch.setattr(change_diff.locale, "getpreferredencoding", lambda do_setlocale=True: "gbk")
    path = tmp_path / "old.txt"
    path.write_bytes("你好\n".encode("gbk"))
    assert read_text(str(path)) == ("你好\n", "ok")


@pytest.mark.parametrize("which", ["binary", "huge"])
def test_binary_or_huge_files_are_not_diffed(tmp_path, which):
    path = tmp_path / "f.bin"
    path.write_bytes(b"\x00\x01binary" if which == "binary" else b"x" * (change_diff.MAX_FILE_BYTES + 1))
    assert read_text(str(path))[1] == "unreadable"
    write = proposed_change("Write", {"file_path": str(path), "content": "x"})
    assert (write.note, write.before) == ("unreadable", [])
    edit = proposed_change("Edit", {"file_path": str(path), "old_string": "a", "new_string": "b"})
    assert (edit.note, edit.numbered) == ("unreadable", False)


def test_other_tools_have_no_change():
    assert proposed_change("Read", {"file_path": "a"}) is None


# ---------- 显示 ----------

def test_html_escapes_and_colors():
    body, added, removed = change_html(Change(["<a>", "same"], ["same", "<b> & c"]))
    assert "&lt;a&gt;" in body and "&lt;b&gt; &amp; c" in body and "<a>" not in body
    assert change_diff.COLORS["-"] in body and change_diff.COLORS["+"] in body
    assert (added, removed) == (1, 1)


def test_inline_marks_are_drawn():
    body, _, _ = change_html(Change(["value = 10"], ["value = 20"]))
    assert change_diff.STRONG["-"] in body and change_diff.STRONG["+"] in body


def test_line_numbers_are_hidden_when_not_real():
    numbered, _, _ = change_html(Change(["a"], ["b"]))
    unnumbered, _, _ = change_html(Change(["a"], ["b"], "not_found", numbered=False))
    assert ">1&nbsp;" in numbered and ">1&nbsp;" not in unnumbered
    assert i18n.tr("assistant_diff_not_found") in unnumbered


def test_long_diff_is_cut():
    body, added, _ = change_html(Change([], [f"line {i}" for i in range(100)]), max_lines=10)
    assert "line 9" in body and "line 10" not in body and "还有 90 行" in body and added == 100


def test_no_change_says_so():
    body, _, _ = change_html(Change(["a"], ["a"]))
    assert i18n.tr("assistant_diff_no_change") in body


def test_separator_line():
    assert "⋯" in change_diff._line_html(DiffLine("~", 0, ""), True, 2)
