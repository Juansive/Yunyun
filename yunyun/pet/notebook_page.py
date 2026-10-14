"""设置窗口的"记事本"页（阶段 10 的 B）：芸芸记下的事，看、改、删、全部清空，还有总开关。

    [✓] 让芸芸用记事本（记住你的习惯、项目的规矩）
    说明（灰色）
    ▾ 关于你（2）
        用户喜欢简短的回复            10-08
    ▾ 项目：calc-bug（1）
        测试用 python -m unittest -v  10-08
    [编辑…] [删除]            [全部清空]

记忆本身在 assistant/memory.py（芸芸进程和助手子进程都写）：每次显示这一页都重新读一遍（芸芸聊天时可能刚记了新的）。
总开关只写 config 的 assistant.notebook 一项，下一段对话开始生效。
"""
import logging
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from yunyun import i18n
from yunyun.assistant import memory
from yunyun.assistant import settings as assistant_settings
from yunyun.pet.theme import Toggle
from yunyun.pet.widgets import note

log = logging.getLogger(__name__)
ID_ROLE = Qt.ItemDataRole.UserRole


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
        layout.addWidget(note(tr("notebook_hint")))

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
        self._tree.setVisible(bool(memories))
        self._empty.setVisible(not memories)
        self._update_buttons()

    @property
    def selected_id(self):
        item = self._tree.currentItem()
        return item.data(0, ID_ROLE) if item is not None and item.isSelected() else None

    # ---------- 内部 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()   # 芸芸聊天时可能刚记了新的

    def _update_buttons(self):
        chosen = self.selected_id is not None
        self._edit_button.setEnabled(chosen)
        self._delete_button.setEnabled(chosen)
        self._clear_button.setEnabled(bool(self._memories))

    def _edit(self):
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
            memory.forget(memory_id)
            self.refresh()

    def _clear(self):
        count = len(self._memories)
        answer = QMessageBox.question(self, i18n.tr("notebook_clear"), i18n.tr("notebook_clear_confirm", count))
        if answer == QMessageBox.StandardButton.Yes:
            memory.clear()
            self.refresh()


class NotebookEditDialog(QDialog):
    """改一条：标题（一句话）+ 细节。"""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("notebook_edit_title"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(i18n.tr("notebook_title_label")))
        self._title = QLineEdit(entry.title)
        self._title.setMaxLength(memory.MAX_TITLE)
        layout.addWidget(self._title)
        layout.addWidget(QLabel(i18n.tr("notebook_content_label")))
        self._content = QPlainTextEdit(entry.content)
        layout.addWidget(self._content)
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
