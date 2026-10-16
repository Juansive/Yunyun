"""8.6b（2026-10-16）芸芸当你自己的 Claude Code 的"手"：它要你批准时（PermissionRequest hook），芸芸弹一张确认卡片，
你在卡片上点允许 / 拒绝就直接生效（它自己的确认框随之消失）。你在 Claude 那边点了也行——hook 不会被叫停，
芸芸看后面的事件（批准的那件事做完了、它停下了……）自己收起卡片。

用户定的（2026-10-16）：
- **你正看着 Claude Code 的窗口时不弹**（用它自己的框）；你在别处、或者中途切走了它还在等 → 弹；全屏（看视频、打游戏）时不弹；
- 按钮：允许一次 / 拒绝（可以写一句理由给 Claude）/ 去看看（切到它的窗口）；不做"以后都允许"；
- 芸芸被挡住时卡片照样浮在最上面：独立的置顶窗口（不挂在芸芸身上——挂着的话一置顶会把芸芸一起拉到最前面），出现时不抢键盘；
- 样子照助手的确认卡片（AssistantConfirm）：命令全文、改文件的红删绿增、危险命令标红；标题"Claude Code 想……· 项目名"。
"""
import html
import json
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, Signal

from yunyun import i18n
from yunyun.assistant.permissions import analyze_command
from yunyun.assistant.protocol import PermissionRequest
from yunyun.pet import winapi
from yunyun.pet.assistant_confirm import _SECONDARY_STYLE, MONO, AssistantConfirm
from yunyun.pet.side_panel import make_button
from yunyun.pet.watch_reminder import looking_at

COMMAND_TOOLS = ("Bash", "PowerShell")
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
READ_TOOLS = ("Read", "Glob", "Grep", "LS")
WEB_TOOLS = ("WebFetch", "WebSearch")
DONE_EVENTS = ("stop", "idle", "failed", "end")   # 这段对话停下了：还在等的都不用等了
OTHER_CHARS = 2000


# ---------- 内容（纯函数，方便测） ----------

def request_for(event):
    """"要你批准"的事 → 确认卡片要的 PermissionRequest（请求号就是 relay 等回话的编号）。危险命令芸芸自己看（和助手一样的规则）。"""
    data = event.input
    if event.tool in COMMAND_TOOLS:
        return PermissionRequest(event.reply_id, event.tool, data, kind="command",
                                 danger=analyze_command(str(data.get("command") or "")).danger)
    kind = "edit" if event.tool in EDIT_TOOLS else "read_outside" if event.tool in READ_TOOLS else "other"
    return PermissionRequest(event.reply_id, event.tool, data, kind=kind)


def title_key(request):
    if request.kind == "command":
        return "watch_confirm_command_danger" if request.danger else "watch_confirm_command"
    if request.kind == "edit":
        return "watch_confirm_write" if request.tool == "Write" else "watch_confirm_edit"
    if request.kind == "read_outside":
        return "watch_confirm_read"
    return "watch_confirm_web" if request.tool in WEB_TOOLS else "watch_confirm_other"


def other_html(request):
    """别的工具：网页的网址 / 搜什么；别的（MCP 工具……）把参数原样列出来（太长截断）。"""
    data = request.input
    text = str(data.get("url") or data.get("query") or "") if request.tool in WEB_TOOLS \
        else json.dumps(data, ensure_ascii=False, indent=1)
    if len(text) > OTHER_CHARS:
        text = text[:OTHER_CHARS] + "…"
    return f"<div {MONO}>{html.escape(text)}</div>"


# ---------- 卡片 ----------

class WatchConfirm(AssistantConfirm):
    looked = Signal()   # 点了"去看看"

    def __init__(self):
        self._look_button = None
        super().__init__(None)   # 独立的窗口（见文件开头）
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.project = ""
        self._look_button = make_button(_SECONDARY_STYLE, self.looked)
        self._button_row.insertWidget(1, self._look_button)
        self.retranslate()

    def retranslate(self):
        super().retranslate()
        self._reason.setPlaceholderText(i18n.tr("watch_confirm_reason"))
        if self._look_button is not None:
            self._look_button.setText(i18n.tr("watch_confirm_look"))
            self._look_button.setToolTip(i18n.tr("watch_confirm_look_tip"))

    def _fill(self, request):
        super()._fill(request)   # 命令全文、改文件的对比、危险命令标红：和助手的一样
        title = i18n.tr(title_key(request), request.tool)
        self._title.setText(("⚠ " if request.danger else "") + (f"{title} · {self.project}" if self.project else title))
        self._note.setText(i18n.tr("watch_confirm_note_danger") if request.danger else "")
        self._note.setVisible(request.danger)
        said = str(request.input.get("description") or "").strip()   # Claude 自己说的"这条命令是干什么的"
        self._said.setText(i18n.tr("watch_confirm_said_by", said) if said else "")
        self._said.setVisible(bool(said))
        self._remember_button.hide()
        if request.kind == "other":
            self._detail.setHtml(other_html(request))
        self._fit_height()


# ---------- 管着卡片 ----------

@dataclass
class _Pending:
    event: object
    request: PermissionRequest


class WatchApprovals(QObject):
    """收到"要你批准"：先留着（server.hold）等你在卡片上点；你没在看它时把卡片弹出来（约每 0.5 秒看一次，跟着芸芸查遮挡）。"""

    def __init__(self, pet, server):
        super().__init__(pet)
        self._pet = pet
        self._server = server
        self._pending = []
        self._showing = None   # 卡片上正在显示的是 (哪个请求, 还有几个)
        self.card = None
        pet.cover_checked.connect(self._refresh)

    def on_event(self, event):
        if event.event == "approval":
            if not event.reply_id:   # relay 没在等回话：没法批准，不弹
                return
            self._server.hold(event)
            self._pending.append(_Pending(event, request_for(event)))
        else:
            for item in [p for p in self._pending if self._resolved(p, event)]:
                self._drop(item)
        self._refresh()

    def handles(self, event):
        """"等你确认"的提醒要不要让给卡片（WatchReminder 的 skip）：这段对话有在等的批准。"""
        return event.event == "permission" and any(p.event.session == event.session for p in self._pending)

    def close(self):
        for item in list(self._pending):
            self._drop(item)
        if self.card is not None:
            self.card.close_now()

    # ---------- 内部 ----------

    @staticmethod
    def _resolved(item, event):
        """event 说明这件事不用再等了吗：同一段对话停下了、开始了新的一问（没有指纹的 busy）、批准的那件事做完了（指纹对得上）。"""
        if event.session != item.event.session:
            return False
        if event.event in DONE_EVENTS:
            return True
        return event.event == "busy" and (not event.fp or event.fp == item.event.fp)

    def _drop(self, item, reply=None):
        self._server.answer(item.event, reply)   # 已经不等了的话回个空的（relay 早走了也没关系）
        self._pending.remove(item)

    def _refresh(self):
        for item in [p for p in self._pending if not self._server.waiting(p.event)]:
            self._pending.remove(item)   # relay 走了：超时、Claude Code 停了 / 被打断
        current = self._pending[0] if self._pending else None
        if current is None or winapi.is_user_busy() or looking_at(current.event):
            self._showing = None
            if self.card is not None and self.card.is_open:
                self.card.hide_animated()
            return
        showing = (current.request.request_id, len(self._pending))
        if showing == self._showing and self.card.is_open:
            return   # 已经在显示了：不重新摆位置（你可能拖过它）
        self._showing = showing
        card = self._card()
        card.project = current.event.project
        card.show_request(current.request, self._pet.frameGeometry(), len(self._pending) - 1, current.event.cwd)

    def _card(self):
        if self.card is None:
            self.card = WatchConfirm()
            self.card.answered.connect(self._on_answered)
            self.card.looked.connect(self._on_look)
        return self.card

    def _on_answered(self, request_id, allow, reason, remember):
        item = next((p for p in self._pending if p.request.request_id == request_id), None)
        if item is None:
            return
        self._drop(item, {"behavior": "allow"} if allow else
                   {"behavior": "deny", "message": reason or i18n.tr("watch_confirm_denied")})
        self._refresh()

    def _on_look(self):
        """切到 Claude Code 的窗口（下一次看的时候你正看着它了，卡片自己收起）。"""
        if not self._pending:
            return
        event = self._pending[0].event
        window = event.console or winapi.main_window_of(event.ancestors)
        if window:
            winapi.activate(window)
