"""设置 → 记事本（pet/notebook_page.py）：列表分组、改、删、全部清空、总开关。记事本文件在临时目录（conftest）。"""
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from yunyun import i18n
from yunyun.assistant import memory
from yunyun.assistant import settings as assistant_settings
from yunyun.pet import notebook_page
from yunyun.pet.notebook_page import NotebookPage


@pytest.fixture
def page(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    memory.remember("用户喜欢简短的回复")
    memory.remember("测试用 unittest", "在根目录跑", memory.PROJECT, "F:\\code\\calc-bug")
    memory.remember("用户用 Windows")
    return NotebookPage()


def tree(page):
    """[(分组标题, [条目标题……])]"""
    result = []
    for i in range(page._tree.topLevelItemCount()):
        group = page._tree.topLevelItem(i)
        result.append((group.text(0), [group.child(j).text(0) for j in range(group.childCount())]))
    return result


def select(page, memory_id):
    for i in range(page._tree.topLevelItemCount()):
        group = page._tree.topLevelItem(i)
        for j in range(group.childCount()):
            if group.child(j).data(0, notebook_page.ID_ROLE) == memory_id:
                page._tree.setCurrentItem(group.child(j))
                group.child(j).setSelected(True)
                return


def test_list_groups_about_you_first_then_projects(page):
    assert tree(page) == [("关于你（2）", ["用户用 Windows", "用户喜欢简短的回复"]),
                          ("项目：calc-bug（1）", ["测试用 unittest"])]
    assert not page._edit_button.isEnabled() and not page._delete_button.isEnabled()   # 没选中
    select(page, "m2")
    assert page.selected_id == "m2" and page._edit_button.isEnabled()


def test_empty_notebook_shows_a_hint(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    page = NotebookPage()
    assert page._tree.isHidden() and not page._empty.isHidden()
    assert not page._clear_button.isEnabled()


def test_edit_delete_and_clear(page, monkeypatch):
    class FakeDialog:
        def __init__(self, entry, parent=None):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return "测试用 pytest -q", "在根目录跑"

    monkeypatch.setattr(notebook_page, "NotebookEditDialog", FakeDialog)
    select(page, "m2")
    page._edit()
    assert next(m for m in memory.load() if m.id == "m2").title == "测试用 pytest -q"
    assert tree(page)[1] == ("项目：calc-bug（1）", ["测试用 pytest -q"])
    select(page, "m1")
    page._delete()
    assert [m.id for m in memory.load()] == ["m2", "m3"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    page._clear()                                                     # 选了"不"：什么都不删
    assert len(memory.load()) == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page._clear()
    assert memory.load() == [] and page._tree.isHidden()


def test_page_reloads_when_shown(page):
    memory.remember("芸芸刚记的")                                      # 芸芸聊天时记了一条
    page.show()
    assert "芸芸刚记的" in tree(page)[0][1]


def test_switch_saves_only_the_notebook_setting(page):
    assert page._enabled.isChecked()
    page._enabled.setChecked(False)
    assert not assistant_settings.load_settings().notebook


def test_rapport_notes_are_listed_last_and_can_be_edited_deleted_and_cleared(page, monkeypatch):
    """相处笔记（11.3）：放最后一组，在用的在前、观察中的灰色写"观察中"；改、删、全部清空都管它。"""
    from yunyun.assistant import rapport
    r = rapport.Rapport()
    rapport.apply(r, {"add": [{"text": "喜欢被叫主人", "explicit": True}, {"text": "偶尔开实验室的玩笑"}]}, "2026-10-14")
    rapport.save(r)
    page.refresh()
    group = page._tree.topLevelItem(2)
    assert tree(page)[2] == ("相处笔记（1 条在用，1 条观察中）", ["喜欢被叫主人", "偶尔开实验室的玩笑"])
    assert group.child(1).text(1) == "观察中" and group.child(0).toolTip(0) == "你明确说过的"
    assert group.child(1).foreground(0).color() == notebook_page.WAITING_COLOR

    class FakeDialog:
        def __init__(self, entry, parent=None, details=True, **kw):
            assert entry.title == "偶尔开实验室的玩笑" and not details              # 只有一句话
        exec = lambda self: QDialog.DialogCode.Accepted
        values = lambda self: ("偶尔开实验室的玩笑她会笑", "")

    monkeypatch.setattr(notebook_page, "NotebookEditDialog", FakeDialog)
    page._tree.setCurrentItem(group.child(1))
    group.child(1).setSelected(True)
    assert page.selected_kind == notebook_page.RAPPORT and page.selected_id == "r2"
    page._edit()
    assert [n.text for n in rapport.load().notes] == ["喜欢被叫主人", "偶尔开实验室的玩笑她会笑"]
    select(page, "r1")
    page._delete()
    assert [n.id for n in rapport.load().notes] == ["r2"] and len(memory.load()) == 3   # 记事本没动
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page._clear()
    assert rapport.load().notes == [] and memory.load() == [] and page._tree.isHidden()


def test_recollections_are_listed_and_can_be_edited_deleted_and_cleared(page, monkeypatch):
    """回忆（11.4）：放最后一组，新的在前，鼠标停上去看关键词；改、删、全部清空都管它。"""
    from datetime import datetime, timedelta
    from yunyun.assistant import recollections
    day = lambda ago: datetime.now() - timedelta(days=ago)
    recollections.remember("s1", day(2).isoformat(timespec="seconds"), "聊答辩准备", ["答辩"])
    recollections.remember("s2", day(0).isoformat(timespec="seconds"), "修好 pytest 的报错", ["pytest"])
    page.refresh()
    group = page._tree.topLevelItem(2)
    assert tree(page)[2] == ("回忆（2 段）", ["修好 pytest 的报错", "聊答辩准备"])
    assert group.child(0).text(1) == f"{day(0):%m-%d}" and group.child(1).toolTip(0) == "答辩"

    class FakeDialog:
        def __init__(self, entry, parent=None, details=True, heading="", max_title=0):
            assert entry.title == "聊答辩准备" and not details and heading == "recollections_edit_title"
        exec = lambda self: QDialog.DialogCode.Accepted
        values = lambda self: ("聊答辩准备，周五交初稿", "")

    monkeypatch.setattr(notebook_page, "NotebookEditDialog", FakeDialog)
    select(page, "s1")
    assert page.selected_kind == notebook_page.CHAT
    page._edit()
    assert [r.summary for r in recollections.load()] == ["聊答辩准备，周五交初稿", "修好 pytest 的报错"]
    select(page, "s2")
    page._delete()
    assert [r.session for r in recollections.load()] == ["s1"]
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    page._clear()
    assert recollections.load() == [] and memory.load() == []


def test_rapport_switch_is_on_by_default_and_saved(page):
    assert page._rapport.isChecked()
    page._rapport.setChecked(False)
    assert not assistant_settings.load_settings().rapport and assistant_settings.load_settings().notebook


def test_edit_dialog_needs_a_title(qapp):
    dialog = notebook_page.NotebookEditDialog(memory.Memory("m1", memory.USER, "标题", "细节"))
    assert dialog.values() == ("标题", "细节")
    dialog._title.setText("  ")
    assert not dialog._buttons.button(dialog._buttons.StandardButton.Ok).isEnabled()


# ---------- 12.5：我是谁、日记、存放和清空 ----------

def test_who_am_i_and_diary_groups_come_first(page, monkeypatch):
    from yunyun.assistant import diary, whoami
    who = whoami.WhoAmI([whoami.Line("s1", "我喜欢夜里陪人写代码")])
    whoami.apply(who, {"propose": [{"text": "我喜欢下雨天"}]}, "2026-10-25")
    whoami.save(who)
    book = diary.Diary()
    diary.write(book, "2026-10-24", "第一篇")
    diary.write(book, "2026-10-25", "今天陪小陈赶论文。" * 10)
    diary.save(book)
    page.refresh()
    assert [name for name, _ in tree(page)][:3] == ["我是谁（1 句 · 观察中 1）", "日记（2 篇）", "关于你（2）"]
    group = page._tree.topLevelItem(0)
    assert group.child(1).text(1) == "1/3 天" and group.child(1).foreground(0).color() == notebook_page.WAITING_COLOR
    diary_group = page._tree.topLevelItem(1)
    assert diary_group.child(0).text(1) == "10-25" and diary_group.child(0).text(0).endswith("…")

    shown = []
    monkeypatch.setattr(notebook_page, "DiaryDialog",
                        lambda day, text, parent=None: SimpleNamespace(exec=lambda: shown.append((day, text))))
    select(page, "2026-10-24")
    page._edit()
    assert shown == [("2026-10-24", "第一篇")]
    page._delete()
    assert list(diary.load().entries) == ["2026-10-25"]

    class FakeWho:
        def __init__(self, who, parent=None):
            assert whoami.text_of(who) == "我喜欢夜里陪人写代码"
        exec = lambda self: QDialog.DialogCode.Accepted
        text = lambda self: "我喜欢夜里陪人写代码\n我也喜欢猫"

    monkeypatch.setattr(notebook_page, "WhoAmIDialog", FakeWho)
    select(page, "s1")
    page._edit()
    assert whoami.text_of(whoami.load()) == "我喜欢夜里陪人写代码\n我也喜欢猫"
    select(page, "k1")
    page._delete()                                                    # 观察中的提议扔掉
    assert whoami.load().observing == []
    select(page, "s1")
    page._delete()                                                    # 删一句
    assert whoami.text_of(whoami.load()) == "我也喜欢猫"


def test_who_am_i_dialog_restores_an_old_version(qapp, monkeypatch):
    from yunyun.assistant import whoami
    monkeypatch.setattr(i18n, "_current", "zh")
    who = whoami.WhoAmI([whoami.Line("s1", "现在的")], versions=[{"at": "2026-10-20T10:00:00", "text": "最早的"},
                                                                 {"at": "2026-10-23T21:00:00", "text": "上一版\n第二句"}])
    dialog = notebook_page.WhoAmIDialog(who)
    assert dialog.text() == "现在的" and dialog._versions.itemText(0) == "2026-10-23 21:00 · 2 句"
    dialog._use.click()
    assert dialog.text() == "上一版\n第二句"
    assert not notebook_page.WhoAmIDialog(whoami.WhoAmI())._use.isEnabled()   # 没有旧版：按钮是灰的


def test_three_clear_buttons(page, monkeypatch):
    from yunyun.assistant import archive, diary, recollections, whoami
    from yunyun.pet import assistant_history

    def fill():
        archive.record("s1", "原话", "嗯")
        whoami.set_text("我喜欢猫")
        book = diary.Diary()
        diary.write(book, "2026-10-25", "日记")
        diary.save(book)
        recollections.remember("s1", "2026-10-25T10:00:00", "聊猫")
        memory.remember("用户喜欢猫")

    cleared = []
    page.history_cleared.connect(lambda: cleared.append(True))
    answers = []
    for name in ("question", "warning"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, name=name: answers.append(name) or QMessageBox.StandardButton.Yes)
    fill()
    page._clear_history()                                             # 只清原话和历史
    assert archive.count() == 0 and cleared == [True] and whoami.load().lines and memory.load()
    fill()
    page._clear()                                                     # 只清人设记忆
    assert archive.count() == 1 and not whoami.load().lines and not diary.load().entries
    assert recollections.load() == [] and memory.load() == []
    fill()
    page._clear_all()                                                 # 全部：用的是警告框
    assert archive.count() == 0 and memory.load() == [] and answers[-1] == "warning" and len(cleared) == 2
    assert assistant_history.history_path().exists() is False


def test_years_and_memory_folder(page, monkeypatch, tmp_path):
    from yunyun.assistant import memory_folder
    page._years.setCurrentIndex(page._years.findData(3))
    assert memory_folder.archive_years() == 3
    monkeypatch.setattr(memory_folder, "default_folder", lambda: tmp_path / "default")
    monkeypatch.setattr(memory, "memory_path", lambda: memory_folder.folder() / "memory.json")
    memory.remember("用户喜欢猫")
    new = tmp_path / "新文件夹"
    monkeypatch.setattr(notebook_page.QFileDialog, "getExistingDirectory", lambda *a: str(new))
    page._choose_folder()
    assert memory_folder.folder() == new and (new / "memory.json").exists()
    assert not (tmp_path / "default" / "memory.json").exists() and [m.title for m in memory.load()] == ["用户喜欢猫"]
    assert "搬好了" in page._status.text() and page._folder.toolTip() == str(new)
    other = tmp_path / "别处"
    other.mkdir()
    (other / "diary.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(notebook_page.QFileDialog, "getExistingDirectory", lambda *a: str(other))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    page._choose_folder()                                             # 那边已经有记忆、选了"不"：不换
    assert memory_folder.folder() == new
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    page._choose_folder()                                             # 用那边的：这边的不搬
    assert memory_folder.folder() == other and (new / "memory.json").exists() and memory.load() == []
