"""设置窗口的"提醒"页（阶段 11.5）：芸芸记着的提醒，看、删、全部清空。

    说明（灰色）：在助手模式里说"25 分钟后叫我"……
      时间              重复      内容
      今天 15:00        每天      喝水
      10-20 周一 09:00  工作日    开组会
    [删除]                         [全部清空]

提醒本身在 notify/reminders.py（助手子进程用工具写，芸芸本体到点响）：每次显示这一页都重新读一遍。
"""
from datetime import datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from yunyun import i18n
from yunyun.notify import reminders
from yunyun.pet.widgets import note

ID_ROLE = Qt.ItemDataRole.UserRole


def when_text(when, now=None):
    """"今天 15:00" / "明天 09:00" / "10-20 周一 09:00"。"""
    today = (now or datetime.now()).date()
    clock = f"{when:%H:%M}"
    if when.date() == today:
        return i18n.tr("reminders_today", clock)
    if when.date() == today + timedelta(days=1):
        return i18n.tr("reminders_tomorrow", clock)
    weekday = i18n.tr("weekday_names").split(",")[when.weekday()]
    return f"{when:%m-%d} {weekday} {clock}"


class RemindersPage(QWidget):
    def __init__(self, parent=None, now=datetime.now):
        """now：现在几点（测试换成固定的）。"""
        super().__init__(parent)
        tr = i18n.tr
        self._now = now
        layout = QVBoxLayout(self)
        layout.addWidget(note(tr("reminders_intro")))
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels([tr("reminders_when"), tr("reminders_repeat"), tr("reminders_text")])
        self._tree.setRootIsDecorated(False)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._tree, 1)
        self._empty = note(tr("reminders_empty"), "#888")
        layout.addWidget(self._empty)
        row = QHBoxLayout()
        self._delete_button = QPushButton(tr("reminders_delete"))
        self._delete_button.clicked.connect(self._delete)
        self._clear_button = QPushButton(tr("reminders_clear"))
        self._clear_button.clicked.connect(self._clear)
        row.addWidget(self._delete_button)
        row.addStretch()
        row.addWidget(self._clear_button)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        """重新读提醒、重画列表（先响的在前）。"""
        self._items = reminders.load()
        self._tree.clear()
        now = self._now()
        for r in self._items:
            item = QTreeWidgetItem(self._tree, [when_text(reminders.due_time(r), now),
                                                i18n.tr(f"reminders_repeat_{r.repeat}"), r.text])
            item.setData(0, ID_ROLE, r.id)
            item.setToolTip(2, r.text)
        self._tree.setVisible(bool(self._items))
        self._empty.setVisible(not self._items)
        self._update_buttons()

    @property
    def selected_id(self):
        item = self._tree.currentItem()
        return item.data(0, ID_ROLE) if item is not None and item.isSelected() else None

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()   # 助手刚设了新的、到点响过的一次性提醒已经没了

    def _update_buttons(self):
        self._delete_button.setEnabled(self.selected_id is not None)
        self._clear_button.setEnabled(bool(self._items))

    def _delete(self):
        if self.selected_id:
            reminders.cancel(self.selected_id)
            self.refresh()

    def _clear(self):
        answer = QMessageBox.question(self, i18n.tr("reminders_clear"),
                                      i18n.tr("reminders_clear_confirm", len(self._items)))
        if answer == QMessageBox.StandardButton.Yes:
            reminders.clear()
            self.refresh()
