"""设置窗口的"历史"页：按日期分组列出最近 7 天芸芸展示过的提醒，单击一条看详情。
添加了两个以上邮箱时，每条前面写着是哪个邮箱收的（和卡片上的标签一样，card_deck.mailbox_label）。"""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from yunyun import i18n
from yunyun.pet.card_deck import mailbox_label
from yunyun.pet.widgets import button_row, day_label, note


class HistoryPage(QWidget):
    def __init__(self, history, mail_accounts=lambda: []):
        super().__init__()
        self._history = history  # MailHistory，可能是 None（比如测试里）
        self._mail_accounts = mail_accounts   # 返回现在添加的邮箱（标"哪个邮箱收的"用）
        self._accounts = []
        tr = i18n.tr
        layout = QVBoxLayout(self)

        layout.addWidget(note(tr("history_note")))

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
        layout.addLayout(button_row(self._clear_button))

        self.refresh()

    def showEvent(self, event):
        self.refresh()  # 每次翻到这一页都重新读，显示最新的记录
        super().showEvent(event)

    def refresh(self):
        tr = i18n.tr
        self._tree.clear()
        self._detail.clear()
        entries = self._history.recent() if self._history is not None else []
        self._accounts = self._mail_accounts()
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
                header = QTreeWidgetItem([day_label(day)])
                header.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 日期行不能选中
                font = header.font(0)
                font.setBold(True)
                header.setFont(0, font)
                self._tree.addTopLevelItem(header)
                header.setExpanded(True)
                groups[day] = header
            label = mailbox_label(entry.account, self._accounts)
            item = QTreeWidgetItem([f"{shown:%H:%M}   " + (f"{label} · " if label else "") + entry.bubble])
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
        if mailbox_label(entry.account, self._accounts):
            lines.append(tr("card_detail_line", tr("card_mailbox"), entry.account))
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
