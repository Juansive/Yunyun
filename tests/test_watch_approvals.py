"""8.6b（2026-10-16）芸芸当 Claude Code 的"手"：它要你批准时芸芸弹确认卡片（pet/watch_approvals.py）。

假的通道（记下留着的、回过的话）+ 假的桌面（最前面的窗口是谁、全不全屏，见 test_watch.desk）。
"""
import pytest
from PySide6.QtCore import QRect

from helpers import wait_until
from test_watch import FakePet, desk  # noqa: F401（desk 是 fixture）
from yunyun import i18n
from yunyun.pet import watch_approvals
from yunyun.pet.watch_approvals import WatchApprovals, WatchConfirm, other_html, request_for, title_key
from yunyun.watch.server import WatchEvent


class Pet(FakePet):
    def frameGeometry(self):
        return QRect(800, 500, 160, 160)


class FakeServer:
    def __init__(self):
        self.held, self.answers, self.gone = [], [], set()

    def hold(self, event):
        self.held.append(event.reply_id)

    def answer(self, event, reply=None):
        self.answers.append((event.reply_id, reply))

    def waiting(self, event):
        return event.reply_id not in self.gone and event.reply_id not in [rid for rid, _ in self.answers]


def approval(reply_id=1, tool="PowerShell", data=None, session="s1", fp="fp1"):
    return WatchEvent("approval", "F:\\code\\proj", "", (50, 40), 0, session, tool, reply_id, fp,
                      data if data is not None else {"command": "npm test", "description": "跑一下测试"})


def other(kind, session="s1", fp=""):
    return WatchEvent(kind, "F:\\code\\proj", session=session, fp=fp)


@pytest.fixture
def hands(desk, monkeypatch):   # noqa: F811
    monkeypatch.setattr(i18n, "_current", "zh")
    pet, server = Pet(), FakeServer()
    approvals = WatchApprovals(pet, server)
    yield pet, server, approvals
    approvals.close()


# ---------- 卡片上写什么 ----------

def test_what_the_card_says():
    assert title_key(request_for(approval())) == "watch_confirm_command"
    risky = request_for(approval(data={"command": "Remove-Item -Recurse -Force build"}))
    assert risky.danger and title_key(risky) == "watch_confirm_command_danger"   # 危险命令芸芸自己看（和助手一样的规则）
    assert title_key(request_for(approval(tool="Write", data={"file_path": "a.txt", "content": "x"}))) == "watch_confirm_write"
    assert title_key(request_for(approval(tool="Edit", data={"file_path": "a.txt"}))) == "watch_confirm_edit"
    assert title_key(request_for(approval(tool="Grep", data={"pattern": "x", "path": "C:\\"}))) == "watch_confirm_read"
    web = request_for(approval(tool="WebFetch", data={"url": "https://example.com/a", "prompt": "看看"}))
    assert title_key(web) == "watch_confirm_web" and "https://example.com/a" in other_html(web)
    mcp = request_for(approval(tool="mcp__github__create_issue", data={"title": "<b>bug</b>"}))
    assert title_key(mcp) == "watch_confirm_other" and "&lt;b&gt;bug" in other_html(mcp)   # 参数原样列出（转义过）


def test_card_looks_like_the_assistant_one_but_says_claude_code(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    card = WatchConfirm()
    card.project = "proj"
    card.show_request(request_for(approval()), QRect(800, 500, 160, 160), 1, "F:\\code\\proj")
    assert card._title.text() == "Claude Code 想执行一条命令 · proj" and card._said.text() == "它说：跑一下测试"
    assert "npm test" in card._detail.toPlainText() and card._more.text() == "还有 1 个"
    assert not card._remember_button.isVisible() and card._look_button.text() == "去看看"   # 没有"以后都允许"（用户定的）
    assert "Claude" in card._reason.placeholderText() and not card._note.isVisible()
    card.show_request(request_for(approval(2, data={"command": "Remove-Item -Recurse -Force build"})), QRect(), 0, "")
    assert card._title.text().startswith("⚠ ") and card._note.isVisible()
    assert card.parent() is None   # 独立的窗口（挂在芸芸身上的话，一置顶会把芸芸一起拉到最前面）
    card.close_now()


# ---------- 什么时候弹、点了以后 ----------

def test_card_pops_up_when_you_are_elsewhere_and_allow_works(qapp, hands):
    pet, server, approvals = hands
    approvals.on_event(approval())
    assert server.held == [1] and approvals.card.is_open                 # 留着等你点；你在别处 → 卡片马上出来
    approvals.card._answer(True)
    assert server.answers == [(1, {"behavior": "allow"})]
    assert wait_until(qapp, lambda: not approvals.card.isVisible())


def test_deny_tells_claude_why(qapp, hands):
    pet, server, approvals = hands
    approvals.on_event(approval(1))
    approvals.on_event(approval(2, fp="fp2"))
    assert approvals.card._more.text() == "还有 1 个"
    approvals.card._reason.setText("先别跑，等我改完")
    approvals.card._deny()
    assert server.answers[-1] == (1, {"behavior": "deny", "message": "先别跑，等我改完"})
    assert approvals.card.request.request_id == 2                        # 下一个接着出来
    approvals.card._deny()
    assert server.answers[-1] == (2, {"behavior": "deny", "message": i18n.tr("watch_confirm_denied")})


def test_no_card_while_you_look_at_claude_code_but_it_comes_when_you_leave(qapp, hands, desk):  # noqa: F811
    pet, server, approvals = hands
    desk["foreground_pid"] = 40                                           # 你正看着 Claude Code 所在的窗口
    approvals.on_event(approval())
    assert approvals.card is None or not approvals.card.is_open           # 用它自己的框
    desk["foreground_pid"] = 1                                            # 切走了，它还在等
    pet.cover_checked.emit()
    assert approvals.card.is_open
    desk["busy"] = True                                                   # 全屏：不弹
    pet.cover_checked.emit()
    assert not approvals.card.is_open


def test_answered_in_claude_code_so_the_card_goes_away(qapp, hands):
    pet, server, approvals = hands
    approvals.on_event(approval(fp="fp1"))
    approvals.on_event(other("busy", fp="other-tool"))                    # 别的工具做完了：还在等这一个
    assert approvals.card.is_open and not server.answers
    approvals.on_event(other("busy", session="s2", fp="fp1"))             # 另一段对话的：不算
    assert approvals.card.is_open
    approvals.on_event(other("busy", fp="fp1"))                           # 批准的这件事做完了：你在 Claude 那边点过了
    assert server.answers == [(1, None)] and wait_until(qapp, lambda: not approvals.card.isVisible())
    for done in ("stop", "idle", "failed", "end"):                        # 停下了 / 闲着 / 出错 / 结束：都不用等了
        approvals.on_event(approval(2))
        approvals.on_event(other(done))
        assert server.answers[-1] == (2, None)
    approvals.on_event(approval(3))
    approvals.on_event(other("busy"))                                     # 开始了新的一问（没有指纹）
    assert server.answers[-1] == (3, None)


def test_relay_gone_and_look_button(qapp, hands, desk):  # noqa: F811
    pet, server, approvals = hands
    desk["windows"] = {40: 4040}
    approvals.on_event(approval())
    approvals.card.looked.emit()
    assert desk["activated"] == [4040]                                    # 去看看：切到 Claude Code 的窗口
    server.gone.add(1)                                                    # relay 走了（超时 / Claude Code 停了）
    pet.cover_checked.emit()
    assert wait_until(qapp, lambda: not approvals.card.isVisible()) and not approvals._pending


def test_reminder_steps_aside_for_the_card(qapp, hands):
    pet, server, approvals = hands
    approvals.on_event(approval())
    assert approvals.handles(WatchEvent("permission", session="s1"))      # 6 秒后的"等你确认"提醒：卡片已经在了
    assert not approvals.handles(WatchEvent("permission", session="s2"))
    assert not approvals.handles(WatchEvent("stop", session="s1"))


def test_old_relay_without_reply_gets_no_card(qapp, hands):
    pet, server, approvals = hands
    approvals.on_event(approval(reply_id=0))
    assert not server.held and approvals.card is None


def test_close_lets_everything_go(qapp, hands):
    pet, server, approvals = hands
    approvals.on_event(approval(1))
    approvals.on_event(approval(2, fp="fp2"))
    approvals.close()
    assert server.answers == [(1, None), (2, None)] and not approvals.card.isVisible()


def test_module_has_no_heavy_imports():
    """芸芸进程（节能版也是）里要用：不能带进 Agent SDK / anthropic（在新开的进程里看）。"""
    from test_assistant_imports import modules_after, top_level
    modules = top_level(modules_after(f"import {watch_approvals.__name__}"))
    assert "claude_agent_sdk" not in modules and "anthropic" not in modules
