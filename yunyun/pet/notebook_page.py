"""设置窗口的"记事本"页（阶段 10 的 B）：芸芸记下的事，看、改、删、全部清空，还有总开关。

    [✓] 让芸芸用记事本（记住你的习惯、项目的规矩）
    [✓] 让芸芸从相处中学习（阶段 11.3）
    说明（灰色）
    ▾ 关于你（2）
        用户喜欢简短的回复            10-08
    ▾ 项目：calc-bug（1）
        测试用 python -m unittest -v  10-08
    ▾ 相处笔记（1 条在用，1 条观察中）
        累的时候先安慰，不要调侃      10-14
        偶尔开实验室的玩笑会笑        观察中（灰色）
    [编辑…] [删除]            [全部清空]

记忆本身在 assistant/memory.py、相处笔记在 assistant/rapport.py（芸芸进程和助手子进程都写）：
每次显示这一页都重新读一遍（芸芸聊天时可能刚记了新的）。开关只写 config 的那一项，下一段对话开始生效。
"""
import logging
import os
from datetime import datetime
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from yunyun import i18n
from yunyun.assistant import memory, rapport
from yunyun.assistant import settings as assistant_settings
from yunyun.pet.theme import Toggle
from yunyun.pet.widgets import note

log = logging.getLogger(__name__)
ID_ROLE = Qt.ItemDataRole.UserRole
RAPPORT_ROLE = Qt.ItemDataRole.UserRole + 1   # 这一行是相处笔记（不是记事本的一条）
WAITING_COLOR = QColor("#9CA3AF")             # 观察中的相处笔记：灰色


def _date(text):
    try:
        return f"{datetime.fromisoformat(text):%m-%d}"
    except (TypeError, ValueError):
        return ""


class NotebookPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        tr = i18n.tr
        layout = QVBoxLayout(self)
        self._enabled = Toggle(tr("notebook_enabled"))
        self._enabled.setChecked(assistant_settings.load_settings().notebook)
        self._enabled.toggled.connect(assistant_settings.set_notebook_enabled)
        layout.addWidget(self._enabled)
        self._rapport = Toggle(tr("rapport_enabled"))   # 相处笔记（11.3，默认开）
        self._rapport.setChecked(assistant_settings.load_settings().rapport)
        self._rapport.toggled.connect(assistant_settings.set_rapport_enabled)
        layout.addWidget(self._rapport)
        layout.addWidget(note(tr("notebook_hint")))
        layout.addWidget(note(tr("rapport_hint")))

        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(True)
        header = self._tree.header()   # 标题占满，日期靠右
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        self._tree.itemDoubleClicked.connect(lambda item, column: self._edit())
        layout.addWidget(self._tree, 1)
        self._empty = note(tr("notebook_empty"), "#888")   # 还没有记过：代替列表显示
        layout.addWidget(self._empty)

        row = QHBoxLayout()
        self._edit_button = QPushButton(tr("notebook_edit"))
        self._edit_button.clicked.connect(self._edit)
        self._delete_button = QPushButton(tr("notebook_delete"))
        self._delete_button.clicked.connect(self._delete)
        self._clear_button = QPushButton(tr("notebook_clear"))
        self._clear_button.clicked.connect(self._clear)
        row.addWidget(self._edit_button)
        row.addWidget(self._delete_button)
        row.addStretch()
        row.addWidget(self._clear_button)
        layout.addLayout(row)
        self.refresh()

    # ---------- 对外（测试也用） ----------

    def refresh(self):
        """重新读记事本、重画列表（分组：关于你、每个项目；组里最近改过的在前）。"""
        memories = memory.load()
        self._memories = {m.id: m for m in memories}
        self._tree.clear()
        groups = {}
        for m in sorted(memories, key=lambda m: (m.updated or m.created, m.id), reverse=True):
            key = "" if m.scope == memory.USER else os.path.normcase(m.workspace)
            groups.setdefault(key, (m.workspace, []))[1].append(m)
        for key in sorted(groups, key=lambda k: (k != "", os.path.basename(k).lower())):
            workspace, items = groups[key]
            name = (i18n.tr("notebook_about_you", len(items)) if not key
                    else i18n.tr("notebook_project", os.path.basename(workspace) or workspace, len(items)))
            group = QTreeWidgetItem(self._tree, [name, ""])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)   # 分组标题不能选
            group.setToolTip(0, workspace)
            for m in items:
                child = QTreeWidgetItem(group, [m.title, _date(m.updated or m.created)])
                child.setData(0, ID_ROLE, m.id)
                child.setToolTip(0, m.content or m.title)
            group.setFirstColumnSpanned(True)   # 分组标题（项目文件夹名）占整行
            group.setExpanded(True)
        self._add_rapport_group()
        anything = bool(memories or self._notes)
        self._tree.setVisible(anything)
        self._empty.setVisible(not anything)
        self._update_buttons()

    def _add_rapport_group(self):
        """相处笔记一组（放最后）：在用的在前，观察中的灰色、右边写"观察中"；你说过的提示一下。"""
        data = rapport.load()
        active, waiting = rapport.active_notes(data), rapport.candidates(data)
        self._notes = {n.id: n for n in active + waiting}
        if not self._notes:
            return
        group = QTreeWidgetItem(self._tree, [i18n.tr("rapport_group", len(active), len(waiting)), ""])
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)
        for n in active + waiting:
            when = _date(n.updated or n.created) if n.active else i18n.tr("rapport_waiting")
            child = QTreeWidgetItem(group, [n.text, when])
            child.setData(0, ID_ROLE, n.id)
            child.setData(0, RAPPORT_ROLE, True)
            child.setToolTip(0, i18n.tr("rapport_explicit") if n.explicit else
                             i18n.tr("rapport_seen", len(n.sessions)))
            if not n.active:
                for column in (0, 1):
                    child.setForeground(column, WAITING_COLOR)
        group.setFirstColumnSpanned(True)
        group.setExpanded(True)

    @property
    def selected_id(self):
        item = self._tree.currentItem()
        return item.data(0, ID_ROLE) if item is not None and item.isSelected() else None

    @property
    def selected_is_rapport(self):
        item = self._tree.currentItem()
        return bool(item is not None and item.isSelected() and item.data(0, RAPPORT_ROLE))

    # ---------- 内部 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()   # 芸芸聊天时可能刚记了新的

    def _update_buttons(self):
        chosen = self.selected_id is not None
        self._edit_button.setEnabled(chosen)
        self._delete_button.setEnabled(chosen)
        self._clear_button.setEnabled(bool(self._memories or self._notes))

    def _edit(self):
        if self.selected_is_rapport:
            n = self._notes.get(self.selected_id)
            if n is None:
                return
            dialog = NotebookEditDialog(SimpleNamespace(title=n.text, content=""), self, details=False)
            if dialog.exec() == QDialog.DialogCode.Accepted and not rapport.edit(n.id, dialog.values()[0]):
                log.info("相处笔记：没改成 %s（芸芸刚删了？）", n.id)
            self.refresh()
            return
        m = self._memories.get(self.selected_id)
        if m is None:
            return
        dialog = NotebookEditDialog(m, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            title, content = dialog.values()
            try:
                memory.remember(title, content, m.scope, m.workspace, m.id)
            except memory.NotebookError as e:   # 这一条刚被芸芸删了之类（标题空了对话框里已经拦过）
                log.info("记事本：没改成 %s（%s）", m.id, e)
            self.refresh()

    def _delete(self):
        memory_id = self.selected_id
        if memory_id:
            (rapport.delete if self.selected_is_rapport else memory.forget)(memory_id)
            self.refresh()

    def _clear(self):
        """记事本和相处笔记全部清空（先问一句）。"""
        count = len(self._memories) + len(self._notes)
        answer = QMessageBox.question(self, i18n.tr("notebook_clear"), i18n.tr("notebook_clear_confirm", count))
        if answer == QMessageBox.StandardButton.Yes:
            memory.clear()
            rapport.clear()
            self.refresh()


class NotebookEditDialog(QDialog):
    """改一条：标题（一句话）+ 细节。details=False：只有一句话（相处笔记）。"""

    def __init__(self, entry, parent=None, details=True):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("notebook_edit_title" if details else "rapport_edit_title"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(i18n.tr("notebook_title_label")))
        self._title = QLineEdit(entry.title)
        self._title.setMaxLength(memory.MAX_TITLE if details else rapport.MAX_TEXT)
        layout.addWidget(self._title)
        self._content = QPlainTextEdit(entry.content, self)
        if details:
            layout.addWidget(QLabel(i18n.tr("notebook_content_label")))
            layout.addWidget(self._content)
        else:
            self._content.hide()
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._title.textChanged.connect(self._update_ok)
        self.resize(420, 260)

    def _update_ok(self):
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self._title.text().strip()))

    def values(self):
        return self._title.text().strip(), self._content.toPlainText().strip()[:memory.MAX_CONTENT]
