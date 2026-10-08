"""设置 → 记事本（pet/notebook_page.py）：列表分组、改、删、全部清空、总开关。记事本文件在临时目录（conftest）。"""
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


def test_edit_dialog_needs_a_title(qapp):
    dialog = notebook_page.NotebookEditDialog(memory.Memory("m1", memory.USER, "标题", "细节"))
    assert dialog.values() == ("标题", "细节")
    dialog._title.setText("  ")
    assert not dialog._buttons.button(dialog._buttons.StandardButton.Ok).isEnabled()
