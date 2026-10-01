"""右键菜单的公共部分（芸芸本体菜单和托盘菜单共用）。"""
from PySide6.QtWidgets import QMenu


class KeepOpenMenu(QMenu):
    """点击 keep_open_action（比如"退出"）时菜单不关闭，方便快速点第二下。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keep_open_action = None

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.position().toPoint())
        if action is not None and action is self.keep_open_action:
            action.trigger()  # 只触发动作，不走 QMenu 默认的"触发后关闭菜单"流程
            return
        super().mouseReleaseEvent(event)
