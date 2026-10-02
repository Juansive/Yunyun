"""设置窗口的"历史"页：按日期分组列出最近 7 天芸芸展示过的提醒，单击一条看详情。"""
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from yunyun import i18n


class HistoryPage(QWidget):
    def __init__(self, history):
        super().__init__()
        self._history = history  # MailHistory，可能是 None（比如测试里）
        tr = i18n.tr
        layout = QVBoxLayout(self)

        note = QLabel(tr("history_note"))
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        layout.addWidget(note)

        # 树形列表：第一层是日期（今天 / 昨天 / 10月1日），展开后是那天的每一条提醒
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)  # 不显示展开用的小箭头：日期行一直展开着
        self._tree.currentItemChanged.connect(self._show_detail)
        layout.addWidget(self._tree, 1)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._detail.setStyleSheet("color: #444; padding: 6px; background: #FAFAFA; border-radius: 6px;")
        self._detail.setMinimumHeight(70)
        self._detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._detail)

        self._clear_button = QPushButton(tr("history_clear"))
        self._clear_button.clicked.connect(self._clear)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._clear_button)
        layout.addLayout(row)

        self.refresh()

    def showEvent(self, event):
        self.refresh()  # 每次翻到这一页都重新读，显示最新的记录
        super().showEvent(event)

    def refresh(self):
        tr = i18n.tr
        self._tree.clear()
        self._detail.clear()
        entries = self._history.recent() if self._history is not None else []
        self._clear_button.setEnabled(bool(entries))
        if not entries:
            empty = QTreeWidgetItem([tr("history_empty")])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)  # 灰色、不能选中
            self._tree.addTopLevelItem(empty)
            return

        groups = {}  # {日期: 那天的树节点}；entries 已经是最新的在前，所以分组也按从新到旧
        for entry in entries:
            shown = datetime.fromtimestamp(entry.shown_at)
            day = shown.date()
            if day not in groups:
                header = QTreeWidgetItem([_day_label(day)])
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 日期行不能选中
                font = header.font(0)
                font.setBold(True)
                header.setFont(0, font)
                self._tree.addTopLevelItem(header)
                header.setExpanded(True)
                groups[day] = header
            item = QTreeWidgetItem([f"{shown:%H:%M}   {entry.bubble}"])
            item.setData(0, Qt.ItemDataRole.UserRole, entry)  # 把整条记录藏在列表项里，显示详情时用
            item.setToolTip(0, entry.bubble)
            groups[day].addChild(item)

    def _show_detail(self, item, _previous=None):
        entry = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if entry is None:
            self._detail.clear()
            return
        tr = i18n.tr
        lines = [
            tr("card_detail_line", tr("card_subject"), entry.subject or tr("card_no_subject")),
            tr("card_detail_line", tr("card_sender"), entry.sender),
        ]
        if entry.date:
            lines.append(tr("card_detail_line", tr("card_time"), f"{entry.date.astimezone():%Y-%m-%d %H:%M}"))
        if entry.attachments:
            lines.append(tr("card_detail_line", tr("card_attachments"),
                            tr("list_separator").join(a or "…" for a in entry.attachments)))
        self._detail.setText("\n".join(lines))

    def _clear(self):
        answer = QMessageBox.question(self, self.window().windowTitle(), i18n.tr("history_confirm_clear"))
        if answer == QMessageBox.StandardButton.Yes and self._history is not None:
            self._history.clear()
            self.refresh()


def _day_label(day):
    today = date.today()
    if day == today:
        return i18n.tr("date_today")
    if day == today - timedelta(days=1):
        return i18n.tr("date_yesterday")
    return i18n.tr("date_month_day", day.month, day.day)
