"""设置窗口各页（还有助手的历史面板）共用的小控件和小函数。"""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel

from yunyun import i18n


def note(text="", color="#666"):
    """说明文字：自动换行，默认灰色（重要提示用橙色 #B26A00 之类）。"""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {color};")
    return label


class StatusLabel(QLabel):
    """"测试并保存"、"连接"之后的结果：成功绿色、出错红色；能选中复制（报错时有用）。"""

    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def show_text(self, text, error=False):
        self.setStyleSheet("color: #C62828;" if error else "color: #2E7D32;")
        self.setText(text)


def button_row(*widgets):
    """一行按钮，靠右放（左边一个弹簧）。"""
    row = QHBoxLayout()
    row.addStretch()
    for widget in widgets:
        row.addWidget(widget)
    return row


def day_label(day, today=None):
    """按日期分组时的标题："今天" / "昨天" / "10月1日"。"""
    delta = ((today or date.today()) - day).days
    if delta == 0:
        return i18n.tr("date_today")
    if delta == 1:
        return i18n.tr("date_yesterday")
    return i18n.tr("date_month_day", day.month, day.day)
