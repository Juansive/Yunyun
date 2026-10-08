"""旁观模式（阶段 8）：你自己用的 Claude Code 做完一轮 / 等你确认时（yunyun/watch/server.py 收到），芸芸提醒你。

2026-10-08 用户定的：
- **你正看着 Claude Code 所在的窗口时不提醒**：最前面的窗口属于 hook 脚本的某个祖先进程（终端 / VS Code / Claude 桌面版），
  或者就是它的控制台窗口；
- 看得见芸芸 → 气泡："Claude Code 做完了 · 项目名"，下面一行是它最后说的第一句（等确认时是它的提示）；
- 芸芸被挡住 → 任务栏上方的角落小条（复用助手的 AssistantNotice，往上叠一层，和助手的同时出现也不挡）；
  点一下切到 Claude Code 的窗口；你自己切过去了小条也收起；芸芸露出来了改成气泡说一遍。全屏（看视频、打游戏）时不弹。
- 免打扰时段照样提醒（和助手一样：是你自己让它干的活）。
"""
import logging

from PySide6.QtCore import QObject

from yunyun import i18n
from yunyun.pet import winapi
from yunyun.pet.assistant_notice import AssistantNotice

log = logging.getLogger(__name__)


def looking_at(event):
    """你现在是不是正看着这个 Claude Code（最前面的窗口是它的终端 / 桌面版 / 控制台）。"""
    foreground = winapi.foreground_window()
    if not foreground:
        return False
    return winapi.process_id(foreground) in event.ancestors or (event.console and foreground == event.console)


def reminder_text(event):
    """(第一行, 整段)：第一行是"Claude Code 做完了 · 项目名"，整段多一行它说的第一句。"""
    header = i18n.tr(f"watch_{event.event}", event.project) if event.project else i18n.tr(f"watch_{event.event}_plain")
    return header, f"{header}\n{event.text}" if event.text else header


class WatchReminder(QObject):
    SAY_MS = 8000

    def __init__(self, pet):
        super().__init__(pet)
        self._pet = pet
        self.notice = AssistantNotice(stack=1)
        self.notice.clicked.connect(self._on_notice_clicked)
        self._event = None   # 角落小条说的是哪一件事（点它时切到那个 Claude Code）
        pet.cover_checked.connect(self._check_notice)

    def on_event(self, event, force=False):
        """收到一件事。force：设置页的"发一条测试提醒"（你正看着设置窗口，照样提醒）。"""
        if not force and looking_at(event):
            log.info("旁观模式：你正看着 Claude Code，不提醒")
            return
        header, text = reminder_text(event)
        if self._pet.is_visible_to_user():
            self._pet.say(text, self.SAY_MS)
            self._hide_notice()
        elif winapi.is_user_busy():
            log.info("旁观模式：你在全屏，不提醒")
        else:
            self._event = event
            line = f"{header} · {event.text}" if event.text else header
            self.notice.show_message("done" if event.event == "stop" else "confirm", line)

    def close(self):
        self._hide_notice()

    def _hide_notice(self):
        self._event = None
        self.notice.hide()

    def _on_notice_clicked(self):
        event = self._event
        self._hide_notice()
        if event is None:
            return
        window = event.console or winapi.main_window_of(event.ancestors)
        if window:
            winapi.activate(window)
        else:   # 找不到它的窗口（关掉了？）：把芸芸叫出来
            self._pet.bring_to_front()

    def _check_notice(self):
        """约每 0.5 秒（芸芸检查完遮挡）：你自己切到 Claude Code 了 → 小条收起；
        芸芸露出来了 → 改成气泡再说一遍（免得你没看到小条就错过了），小条收起。"""
        event = self._event
        if event is None:
            return
        if looking_at(event):
            self._hide_notice()
        elif self._pet.is_visible_to_user():
            self._pet.say(reminder_text(event)[1], self.SAY_MS)
            self._hide_notice()
