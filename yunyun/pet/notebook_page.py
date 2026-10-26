"""设置窗口的"记事本"页（阶段 10 的 B；12.5 起芸芸记得的一切都在这页）：看、改、删，存放和清空，还有总开关。

    [✓] 让芸芸用记事本（记住你的习惯、项目的规矩）
    [✓] 让芸芸回想以前的对话（每天回顾一次）
    说明（灰色）
    ▾ 我是谁（2 句 · 观察中 1）（阶段 12）
        我喜欢夜里陪人写代码          10-23
        我喜欢下雨天                  1/3 天（灰色）
    ▾ 日记（3 篇）
        今天陪小陈赶论文……            10-25
    ▾ 关于你（2）
        用户喜欢简短的回复            10-08
    ▾ 项目：calc-bug（1）
        测试用 python -m unittest -v  10-08
    ▾ 相处笔记（1 条在用，1 条观察中）
        累的时候先安慰，不要调侃      10-14
        偶尔开实验室的玩笑会笑        观察中（灰色）
    ▾ 回忆（2 段）（阶段 11.4）
        帮用户修好 pytest 的报错      10-14
    [看 / 改…] [删除]
    ── 存放和清空 ──
    原话存多久 [一直留 ▾]
    记忆文件夹 C:\\…\\assistant        [更改…]
    [清空历史对话] [清空人设记忆] [全部清空]

记忆本身在 assistant/ 的 memory / rapport / recollections / diary / whoami / archive（芸芸进程和助手子进程都写）：
每次显示这一页都重新读一遍（芸芸聊天时可能刚记了新的）。开关只写 config 的那一项，下一段对话开始生效。
"""
import logging
import os
from datetime import datetime
from types import SimpleNamespace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from yunyun import i18n
from yunyun.assistant import diary, memory, memory_folder, rapport, recollections, whoami
from yunyun.assistant import settings as assistant_settings
from yunyun.pet import assistant_history
from yunyun.pet.theme import Toggle
from yunyun.pet.widgets import StatusLabel, note

log = logging.getLogger(__name__)
ID_ROLE = Qt.ItemDataRole.UserRole
KIND_ROLE = Qt.ItemDataRole.UserRole + 1   # 这一行是哪一种（没设 = 记事本的一条）
MEMORY, RAPPORT, CHAT = "memory", "rapport", "chat"   # 记事本 / 相处笔记（11.3）/ 回忆（11.4）
SELF, SELF_PENDING, DIARY = "self", "self_pending", "diary"   # 我是谁的一句 / 观察中的提议 / 日记（12.5）
WAITING_COLOR = QColor("#9CA3AF")          # 观察中的：灰色
DIARY_PREVIEW = 30                         # 日记在列表里显示前多少字
FOLDER_WIDTH = 320                         # 记忆文件夹的路径最多显示多宽（像素，太长中间省略）


def _date(text):
    try:
        return f"{datetime.fromisoformat(text):%m-%d}"
    except (TypeError, ValueError):
        return ""


class NotebookPage(QWidget):
    history_cleared = Signal()   # 清空了历史对话（文件已经删了）：芸芸把内存里的卡片也清掉

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = i18n.tr
        layout = QVBoxLayout(self)
        self._enabled = Toggle(tr("notebook_enabled"))
        self._enabled.setChecked(assistant_settings.load_settings().notebook)
        self._enabled.toggled.connect(assistant_settings.set_notebook_enabled)
        layout.addWidget(self._enabled)
        self._rapport = Toggle(tr("rapport_enabled"))   # 相处笔记、回忆、每日回顾（11.3 / 12，默认开）
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
        row.addWidget(self._edit_button)
        row.addWidget(self._delete_button)
        row.addStretch()
        layout.addLayout(row)

        # 存放和清空（12.5）
        heading = QLabel(tr("memory_storage"))
        heading.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(heading)
        form = QFormLayout()
        self._years = QComboBox()
        for years in memory_folder.YEAR_CHOICES:
            self._years.addItem(tr(f"archive_years_{years}"), years)
        self._years.setCurrentIndex(max(0, self._years.findData(memory_folder.archive_years())))
        self._years.currentIndexChanged.connect(lambda: memory_folder.set_archive_years(self._years.currentData()))
        form.addRow(tr("archive_years_label"), self._years)
        folder_row = QHBoxLayout()
        self._folder = QLabel()
        self._folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        change = QPushButton(tr("memory_folder_change"))
        change.clicked.connect(self._choose_folder)
        folder_row.addWidget(self._folder, 1)
        folder_row.addWidget(change)
        form.addRow(tr("memory_folder_label"), folder_row)
        layout.addLayout(form)
        clears = QHBoxLayout()
        self._clear_history_button = QPushButton(tr("assistant_history_clear"))
        self._clear_history_button.clicked.connect(self._clear_history)
        self._clear_button = QPushButton(tr("memory_clear_persona"))
        self._clear_button.clicked.connect(self._clear)
        self._clear_all_button = QPushButton(tr("memory_clear_all"))
        self._clear_all_button.clicked.connect(self._clear_all)
        for button in (self._clear_history_button, self._clear_button, self._clear_all_button):
            clears.addWidget(button)
        clears.addStretch()
        layout.addLayout(clears)
        self._status = StatusLabel()
        layout.addWidget(self._status)
        self.refresh()

    # ---------- 对外（测试也用） ----------

    def refresh(self):
        """重新读一遍、重画列表（分组：我是谁、日记、关于你、每个项目、相处笔记、回忆；组里新的在前）。"""
        folder = str(memory_folder.folder())   # 路径太长时中间省略（不把设置窗口撑宽），鼠标停上去看全的
        self._folder.setText(QFontMetrics(self._folder.font()).elidedText(folder, Qt.TextElideMode.ElideMiddle,
                                                                         FOLDER_WIDTH))
        self._folder.setToolTip(folder)
        self._tree.clear()
        who = whoami.load()
        self._who = who
        if who.lines or who.observing:   # 我是谁（12）：现在的每一句；观察中的提议灰色、右边写"几天 / 要几天"
            group = self._group(i18n.tr("whoami_group", len(who.lines), len(who.observing)))
            changed = _date(who.versions[-1]["at"]) if who.versions else ""
            for line in who.lines:
                self._row(group, line.text, changed, line.id, SELF, line.text)
            for change in who.observing:
                text = change.text or i18n.tr("whoami_remove", next(
                    (line.text for line in who.lines if line.id == change.replaces), change.replaces))
                child = self._row(group, text, i18n.tr("whoami_days", len(change.days), whoami.DAYS_TO_APPLY),
                                  change.id, SELF_PENDING, i18n.tr("whoami_pending_tip"))
                for column in (0, 1):
                    child.setForeground(column, WAITING_COLOR)
        self._diary = diary.load().entries
        if self._diary:   # 日记（12）：新的在前，列表里只显示开头
            group = self._group(i18n.tr("diary_group", len(self._diary)))
            for day in sorted(self._diary, reverse=True):
                text = " ".join(self._diary[day].split())
                preview = text if len(text) <= DIARY_PREVIEW else text[:DIARY_PREVIEW] + "…"
                self._row(group, preview, day[5:], day, DIARY, text)
        memories = memory.load()
        self._memories = {m.id: m for m in memories}
        groups = {}
        for m in sorted(memories, key=lambda m: (m.updated or m.created, m.id), reverse=True):
            key = "" if m.scope == memory.USER else os.path.normcase(m.workspace)
            groups.setdefault(key, (m.workspace, []))[1].append(m)
        for key in sorted(groups, key=lambda k: (k != "", os.path.basename(k).lower())):
            workspace, items = groups[key]
            name = (i18n.tr("notebook_about_you", len(items)) if not key
                    else i18n.tr("notebook_project", os.path.basename(workspace) or workspace, len(items)))
            group = self._group(name)
            group.setToolTip(0, workspace)
            for m in items:
                self._row(group, m.title, _date(m.updated or m.created), m.id, MEMORY, m.content or m.title)
        data = rapport.load()
        active, waiting = rapport.active_notes(data), rapport.candidates(data)
        self._notes = {n.id: n for n in active + waiting}
        chats = recollections.recent(recollections.load(), recollections.MAX_ITEMS)
        self._chats = {r.session: r for r in chats}
        if self._notes:   # 相处笔记（11.3）：在用的在前，观察中的灰色、右边写"观察中"；你说过的提示一下
            group = self._group(i18n.tr("rapport_group", len(active), len(waiting)))
            for n in active + waiting:
                when = _date(n.updated or n.created) if n.active else i18n.tr("rapport_waiting")
                tip = i18n.tr("rapport_explicit") if n.explicit else i18n.tr("rapport_seen", len(n.sessions))
                child = self._row(group, n.text, when, n.id, RAPPORT, tip)
                if not n.active:
                    for column in (0, 1):
                        child.setForeground(column, WAITING_COLOR)
        if chats:         # 回忆（11.4）：每段对话一句话，新的在前；鼠标停上去看关键词
            group = self._group(i18n.tr("recollections_group", len(chats)))
            for r in chats:
                self._row(group, r.summary, _date(r.date), r.session, CHAT, ", ".join(r.keywords) or r.summary)
        anything = self._tree.topLevelItemCount() > 0
        self._tree.setVisible(anything)
        self._empty.setVisible(not anything)
        self._update_buttons()

    def _group(self, name):
        group = QTreeWidgetItem(self._tree, [name, ""])
        group.setFlags(Qt.ItemFlag.ItemIsEnabled)   # 分组标题不能选
        group.setFirstColumnSpanned(True)           # 占整行（项目文件夹名可能很长）
        group.setExpanded(True)
        return group

    @staticmethod
    def _row(group, text, when, item_id, kind, tip):
        child = QTreeWidgetItem(group, [text, when])
        child.setData(0, ID_ROLE, item_id)
        child.setData(0, KIND_ROLE, kind)
        child.setToolTip(0, tip)
        return child

    @property
    def selected_id(self):
        item = self._tree.currentItem()
        return item.data(0, ID_ROLE) if item is not None and item.isSelected() else None

    @property
    def selected_kind(self):
        """选中的是哪一种（MEMORY / RAPPORT / CHAT / SELF / SELF_PENDING / DIARY）；没选中是 None。"""
        item = self._tree.currentItem()
        return (item.data(0, KIND_ROLE) or MEMORY) if item is not None and item.isSelected() else None

    # ---------- 内部 ----------

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()   # 芸芸聊天时可能刚记了新的

    def _update_buttons(self):
        chosen = self.selected_id is not None
        self._edit_button.setEnabled(chosen)
        self._delete_button.setEnabled(chosen)
        self._clear_button.setEnabled(self._tree.topLevelItemCount() > 0)

    def _edit(self):
        kind, item_id = self.selected_kind, self.selected_id
        if kind in (SELF, SELF_PENDING):   # 我是谁：整段一起改，能退回旧版
            dialog = WhoAmIDialog(self._who, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                whoami.set_text(dialog.text())
            self.refresh()
            return
        if kind == DIARY:                  # 日记是她写的：只看
            if item_id in self._diary:
                DiaryDialog(item_id, self._diary[item_id], self).exec()
            return
        if kind in (RAPPORT, CHAT):   # 只有一句话
            item = (self._notes if kind == RAPPORT else self._chats).get(item_id)
            if item is None:
                return
            text, heading, limit, save = ((item.text, "rapport_edit_title", rapport.MAX_TEXT, rapport.edit)
                                          if kind == RAPPORT else
                                          (item.summary, "recollections_edit_title", recollections.MAX_SUMMARY,
                                           recollections.edit))
            dialog = NotebookEditDialog(SimpleNamespace(title=text, content=""), self, details=False, heading=heading,
                                        max_title=limit)
            if dialog.exec() == QDialog.DialogCode.Accepted and not save(item_id, dialog.values()[0]):
                log.info("记事本：没改成一条（%s，芸芸刚删了？）", kind)
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
        item_id, kind = self.selected_id, self.selected_kind
        if not item_id:
            return
        if kind == SELF:
            whoami.set_text("\n".join(line.text for line in self._who.lines if line.id != item_id))
        else:
            {MEMORY: memory.forget, RAPPORT: rapport.delete, CHAT: recollections.delete,
             SELF_PENDING: whoami.drop_change, DIARY: diary.delete}[kind](item_id)
        self.refresh()

    def _ask(self, title_key, text, warning=False):
        ask = QMessageBox.warning if warning else QMessageBox.question
        answer = ask(self, i18n.tr(title_key), text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                     QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    @staticmethod
    def _forget_persona():
        memory.clear()
        whoami.clear()
        diary.clear()
        rapport.clear()
        recollections.clear()

    def _clear_history(self):
        """清空历史对话：卡片、Claude Code 的对话记录、撤销用的备份、原话存档（先问一句）。人设记忆不动。"""
        if self._ask("assistant_history_clear", i18n.tr("assistant_history_confirm")):
            assistant_history.clear()
            self.history_cleared.emit()
            self._status.show_text(i18n.tr("assistant_history_cleared"))

    def _clear(self):
        """清空人设记忆：记事本、我是谁、日记、相处笔记、回忆（先问一句）。原话不动。"""
        if self._ask("memory_clear_persona", i18n.tr("memory_clear_persona_confirm", len(self._memories))):
            self._forget_persona()
            self.refresh()
            self._status.show_text(i18n.tr("memory_cleared"))

    def _clear_all(self):
        """全部清空：历史对话 + 人设记忆（警告，默认"不"）。"""
        if self._ask("memory_clear_all", i18n.tr("memory_clear_all_confirm"), warning=True):
            assistant_history.clear()
            self._forget_persona()
            self.history_cleared.emit()
            self.refresh()
            self._status.show_text(i18n.tr("memory_cleared"))

    def _choose_folder(self):
        """换记忆文件夹：新文件夹里已经有芸芸的记忆就问用不用那里的，没有就搬过去。"""
        chosen = QFileDialog.getExistingDirectory(self, i18n.tr("memory_folder_label"), str(memory_folder.folder()))
        if not chosen:
            return
        use_existing = memory_folder.has_memory(chosen)
        if use_existing and not self._ask("memory_folder_label", i18n.tr("memory_folder_exists")):
            return
        try:
            memory_folder.move_to(chosen, use_existing)
        except OSError as e:
            log.warning("记忆文件夹没换成（%s）", type(e).__name__)
            self._status.show_text(i18n.tr("memory_folder_failed", type(e).__name__), error=True)
            return
        self.refresh()
        self._status.show_text(i18n.tr("memory_folder_moved"))


class NotebookEditDialog(QDialog):
    """改一条：标题（一句话）+ 细节。details=False：只有一句话（相处笔记、回忆）。"""

    def __init__(self, entry, parent=None, details=True, heading="notebook_edit_title", max_title=memory.MAX_TITLE):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr(heading))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(i18n.tr("notebook_title_label")))
        self._title = QLineEdit(entry.title)
        self._title.setMaxLength(max_title)
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


class WhoAmIDialog(QDialog):
    """改"我是谁"（12.5，用户定的样子）：上面全文（一句一行，能改），下面旧版本下拉框 +"用这一版"
    （先填进上面给你看，按确定才生效）。"""

    def __init__(self, who, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("whoami_edit_title"))
        layout = QVBoxLayout(self)
        layout.addWidget(note(i18n.tr("whoami_edit_hint")))
        self._text = QPlainTextEdit(whoami.text_of(who), self)
        layout.addWidget(self._text, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel(i18n.tr("whoami_versions")))
        self._versions = QComboBox()
        for version in reversed(who.versions):   # 新的在前
            lines = [t for t in version["text"].splitlines() if t.strip()]
            self._versions.addItem(i18n.tr("whoami_version_item", version["at"][:16].replace("T", " "), len(lines)),
                                   version["text"])
        self._use = QPushButton(i18n.tr("whoami_use_version"))
        self._use.clicked.connect(lambda: self._text.setPlainText(self._versions.currentData() or ""))
        self._versions.setEnabled(bool(who.versions))
        self._use.setEnabled(bool(who.versions))
        row.addWidget(self._versions, 1)
        row.addWidget(self._use)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(460, 320)

    def text(self):
        return self._text.toPlainText()


class DiaryDialog(QDialog):
    """看一篇日记（只读：是她写的）。"""

    def __init__(self, day, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.tr("diary_title", day))
        layout = QVBoxLayout(self)
        view = QTextBrowser()
        view.setPlainText(text)
        layout.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(420, 300)
