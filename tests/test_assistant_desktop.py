"""操作桌面软件（阶段 11.6b）：权限规则、读窗口、点击 / 输入 / 按键、工具、确认卡片。

真的 UI 自动化只在 assistant/desktop.py 的 _Backend 里；这里一律用假的（conftest 换成 FakeBackend），绝不碰你真的窗口。
"""
import pytest

from yunyun.assistant import permissions
from yunyun.assistant.permissions import (
    ALLOW, ASK, DENY, ElementFacts, classify_app, decide, is_risky_ui, parse_keys,
)
from yunyun.assistant.protocol import UI_CLICK_TOOL, UI_KEY_TOOL, UI_READ_TOOL, UI_TYPE_TOOL

READ, CLICK, TYPE, KEY = UI_READ_TOOL, UI_CLICK_TOOL, UI_TYPE_TOOL, UI_KEY_TOOL
APP = "Notepad.exe"


def facts(**kw):
    return ElementFacts(**{"site": APP, "element": 'button "保存"', "label": "保存", **kw})


def rule(tool, tool_input=None, element=None, grants=(), ask_screenshot=False):
    return decide(tool, tool_input or {}, ".", frozenset(grants), ask_screenshot,
                  element=element if element is not None else facts())


# ---------- 权限规则 ----------

@pytest.mark.parametrize("tool, tool_input, element, grants, expected", [
    # 读窗口：不用问；"截图前先问我"时问（可以这次对话都允许）
    (READ, {}, facts(), (), (ALLOW, "", False)),
    # 点击 / 输入 / 按键：每次问，可以按软件都允许；都允许过就不问
    (CLICK, {"ref": "u1"}, facts(), (), (ASK, "ui_click", False)),
    (TYPE, {"ref": "u2", "text": "你好"}, facts(label="文本编辑器"), (), (ASK, "ui_type", False)),
    (KEY, {"keys": "ctrl+s"}, facts(), (), (ASK, "ui_key", False)),
    (CLICK, {"ref": "u1"}, facts(), [("ui", "notepad.exe")], (ALLOW, "", False)),
    (KEY, {"keys": "Ctrl + S"}, facts(), [("ui", "notepad.exe")], (ALLOW, "", False)),
    (CLICK, {"ref": "u1"}, facts(), [("ui", "calculatorapp.exe")], (ASK, "ui_click", False)),   # 别的软件不算
    # 撤不回的按钮：每次问、标红（都允许过也问）
    (CLICK, {"ref": "u1"}, facts(label="删除"), [("ui", "notepad.exe")], (ASK, "ui_click", True)),
    (CLICK, {"ref": "u1"}, facts(label="关闭 计算器"), (), (ASK, "ui_click", True)),
    (CLICK, {"ref": "u1"}, facts(label="Uninstall"), (), (ASK, "ui_click", True)),
    (CLICK, {"ref": "u1"}, facts(label="确定(O)", context="确实要永久删除此文件吗?"), [("ui", "notepad.exe")],
     (ASK, "ui_click", True)),
    (CLICK, {"ref": "u1"}, facts(label="确定", context="字体设置"), [("ui", "notepad.exe")], (ALLOW, "", False)),
    # 危险的键：每次问、标红；Win 键组合、Shift+Delete 拒绝
    (KEY, {"keys": "enter"}, facts(), [("ui", "notepad.exe")], (ASK, "ui_key", True)),
    (KEY, {"keys": "ctrl+enter"}, facts(), (), (ASK, "ui_key", True)),
    (KEY, {"keys": "Delete"}, facts(), (), (ASK, "ui_key", True)),
    (KEY, {"keys": "alt+F4"}, facts(), (), (ASK, "ui_key", True)),
    (KEY, {"keys": "win+r"}, facts(), (), (DENY, "", False)),
    (KEY, {"keys": "shift+delete"}, facts(), (), (DENY, "", False)),
    (KEY, {"keys": "ctrl+alt+delete"}, facts(), (), (DENY, "", False)),
    (KEY, {"keys": "ctrl+shift"}, facts(), (), (DENY, "", False)),          # 只有修饰键
    (KEY, {"keys": "ctrl+printscreen"}, facts(), (), (DENY, "", False)),    # 认不得的键
    # 密码框不输入
    (TYPE, {"ref": "u2", "text": "x"}, facts(sensitive=True), [("ui", "notepad.exe")], (DENY, "", False)),
    # 聊天软件：打字照常问；"发送"按钮和回车一律不点
    (TYPE, {"ref": "u2", "text": "晚上吃啥"}, facts(site="QQ.exe", chat=True), (), (ASK, "ui_type", False)),
    (CLICK, {"ref": "u3"}, facts(site="QQ.exe", chat=True, label="发送(S)"), (), (DENY, "", False)),
    (CLICK, {"ref": "u3"}, facts(site="QQ.exe", chat=True, label="Send"), (), (DENY, "", False)),
    (KEY, {"keys": "enter"}, facts(site="QQ.exe", chat=True), (), (DENY, "", False)),
    (KEY, {"keys": "ctrl+enter"}, facts(site="QQ.exe", chat=True), (), (DENY, "", False)),
    # 一律不碰的窗口：读也不行
    (READ, {}, facts(forbidden="terminal"), (), (DENY, "", False)),
    (CLICK, {"ref": "u1"}, facts(forbidden="own"), [("ui", "notepad.exe")], (DENY, "", False)),
    (TYPE, {"ref": "u1", "text": "calc"}, facts(forbidden="run"), (), (DENY, "", False)),
    (KEY, {"keys": "tab"}, facts(forbidden="security"), (), (DENY, "", False)),
    # 看不了这个窗口 / 元素
    (CLICK, {"ref": "u9"}, facts(problem="There is no element u9."), (), (DENY, "", False)),
])
def test_ui_rules(tool, tool_input, element, grants, expected):
    decision = rule(tool, tool_input, element, grants)
    assert (decision.action, decision.kind, decision.danger) == expected


def test_ui_grant_scope_and_reasons():
    decision = rule(CLICK, {"ref": "u1"})
    assert decision.grant == (("ui", "notepad.exe"),) and decision.scope == APP
    assert rule(CLICK, {"ref": "u1"}, facts(label="删除")).grant == ()            # 撤不回的不给"都允许"
    assert rule(CLICK, {"ref": "u1"}, facts(site="")).grant == ()                 # 看不出是哪个程序也不给
    assert "send" in rule(KEY, {"keys": "enter"}, facts(chat=True)).reason
    assert "terminal" in rule(READ, {}, facts(forbidden="run")).reason
    assert "Security" in rule(READ, {}, facts(forbidden="security")).reason
    assert "ctrl+s" in rule(KEY, {"keys": "hyper+x"}).reason                       # 告诉模型怎么写
    assert decide(CLICK, {"ref": "u1"}, ".", element=None).action == DENY          # 没看过元素：拒绝


def test_reading_windows_asks_when_screenshots_ask():
    decision = rule(READ, {}, ask_screenshot=True)
    assert (decision.action, decision.kind, decision.grant) == (ASK, "ui_read", (("ui_read",),))
    assert rule(READ, {}, grants=[("ui_read",)], ask_screenshot=True).by_grant


@pytest.mark.parametrize("app, title, window_class, expected", [
    ("cmd.exe", "C:\\WINDOWS\\system32\\cmd.exe", "ConsoleWindowClass", "terminal"),
    ("WindowsTerminal.exe", "PowerShell", "CASCADIA_HOSTING_WINDOW_CLASS", "terminal"),
    ("mintty.exe", "MINGW64:/f", "mintty", "terminal"),
    ("explorer.exe", "运行", "#32770", "run"),
    ("explorer.exe", "Run", "#32770", "run"),
    ("explorer.exe", "运行", "CabinetWClass", ""),            # 叫"运行"的文件夹不算
    ("explorer.exe", "凭据管理器", "CabinetWClass", "security"),
    ("SecHealthUI.exe", "Windows 安全中心", "ApplicationFrameWindow", "security"),
    ("regedit.exe", "注册表编辑器", "RegEdit_RegEdit", "security"),
    ("Weixin.exe", "微信", "Qt51514QWindowIcon", "chat"),
    ("QQ.exe", "QQ", "Chrome_WidgetWin_1", "chat"),
    ("Notepad.exe", "无标题 - 记事本", "Notepad", ""),
    ("", "", "", ""),
])
def test_classify_app(app, title, window_class, expected):
    assert classify_app(app, title, window_class) == expected


@pytest.mark.parametrize("text, expected", [
    ("ctrl+s", (frozenset({"ctrl"}), "s")),
    ("Ctrl + Shift + N", (frozenset({"ctrl", "shift"}), "n")),
    ("Return", (frozenset(), "enter")),
    ("esc", (frozenset(), "escape")),
    ("alt+F4", (frozenset({"alt"}), "f4")),
    ("cmd+r", (frozenset({"win"}), "r")),
    ("PageDown", (frozenset(), "pagedown")),
    ("ctrl+,", (frozenset({"ctrl"}), ",")),
    ("", None), ("ctrl", None), ("ctrl+s+x", None), ("hyper+x", None), (None, None),
])
def test_parse_keys(text, expected):
    assert parse_keys(text) == expected


def test_every_key_name_has_a_code():
    assert all(isinstance(code, int) and 0 < code < 256 for code in permissions.KEYS.values())
    assert set(permissions.MODIFIERS) == {"ctrl", "alt", "shift", "win"}


@pytest.mark.parametrize("label, context, expected", [
    ("删除", "", True), ("发送(S)", "", True), ("Close", "", True), ("退出登录", "", True),
    ("七", "", False), ("打开导航", "", False),
    ("是(Y)", "要清空回收站吗？", True), ("OK", "Delete 3 items?", True),
    ("是(Y)", "要保存更改吗？", False),
    ("这是一个很长很长的列表项名字，里面虽然提到了删除两个字，但超过四十个字的多半是一大段内容而不是按钮", "", False),
])
def test_is_risky_ui(label, context, expected):
    assert is_risky_ui(ElementFacts(label=label, context=context)) is expected


# ---------- 读出来的控件 → 给模型看的几行 ----------

import asyncio  # noqa: E402

from fake_desktop import OWN_PID, fake_controls  # noqa: E402

from yunyun import i18n  # noqa: E402
from yunyun.assistant import desktop as desktop_module, protocol  # noqa: E402
from yunyun.assistant.desktop import Desktop, DesktopError, Node, context_text, explain, render  # noqa: E402
from yunyun.assistant.tools.desktop import desktop_tools, view_text  # noqa: E402


def test_render_lists_what_can_be_seen_and_operated():
    text, refs, offscreen = render(fake_controls()[201][1:])
    assert text.splitlines() == [
        '- menuitem "文件" [ref=u1] (collapsed)',            # 没名字的菜单栏不列，里面的往上提一层
        '- document "文本编辑器" [ref=u2] value="你好"',
        '- button "保存" [ref=u3]',
        '- button "删除" [ref=u4]',
        '- checkbox "自动换行" [ref=u5] (unchecked)',
        '- button "灰色的" (disabled)',                       # 灰的没有编号
        '- edit "密码" [ref=u6] value=(hidden)',              # 密码框的值不给
        '- custom "画出来的按钮" [ref=u7]',                   # 只能用鼠标点的也有编号（11.6c）
    ]                                                           # 屏幕外的标签页、滚动条连同里面的都不列
    assert offscreen == 1 and list(refs) == ["u1", "u2", "u3", "u4", "u5", "u6", "u7"] and refs["u3"].name == "保存"


def test_render_keeps_named_groups_as_headings():
    nodes = [Node("group", "数字键盘", 1), Node("button", "七", 2, ("invoke",)), Node("pane", "", 2),
             Node("button", "八", 3, ("invoke",)), Node("text", "结果", 1)]
    assert render(nodes)[0].splitlines() == ['- group "数字键盘"', '  - button "七" [ref=u1]', '  - button "八" [ref=u2]',
                                            '- text "结果"']


def test_context_and_errors():
    assert context_text(fake_controls()[205]) == "文件名已存在，要替换它吗？"
    assert context_text(fake_controls()[201]) == ""                       # 屏幕外的、没名字的不算
    timeout = OSError(-2147023436, "超时")                                  # COM 的错带 HRESULT（负数）
    assert explain(timeout) == "The app is not responding (timed out)."
    assert explain(OSError(0x80040201, "x")).startswith("That element is gone")
    assert explain(ValueError()) == "UI Automation failed (ValueError)."


# ---------- 读、点、输入、按键（假的 UI 自动化） ----------

def run(coro):
    return asyncio.run(coro)


def new_desktop():
    return Desktop(own_pids={OWN_PID})


def test_reads_the_window_the_user_was_using_but_never_yunyun():
    desk = new_desktop()
    view = run(desk.read())
    assert (view.window, view.app, view.title) == ("w201", "Notepad.exe", "无标题 - 记事本")
    assert '- button "保存" [ref=u3]' in view.snapshot and "1 more items are off screen" in view.note
    assert run(desk.read("w202")).snapshot.startswith('- text "显示为 0"')
    assert run(desk.read()).app == "CalculatorApp.exe"                     # 不给编号：接着读最后读的那个
    with pytest.raises(DesktopError, match="no window 'w999'"):
        run(desk.read("w999"))


def test_facts_come_from_the_window_not_the_model():
    desk = new_desktop()

    def facts_of(tool, tool_input):
        return run(desk.facts(tool, tool_input))

    assert facts_of(CLICK, {"ref": "u1"}).problem.startswith("Read a window")   # 还没读过
    assert facts_of(READ, {}) == ElementFacts(site="Notepad.exe")
    run(desk.read())
    delete = facts_of(CLICK, {"ref": "u4"})
    assert (delete.site, delete.element, delete.label) == ("Notepad.exe", '- button "删除"', "删除")
    assert facts_of(TYPE, {"ref": "u6", "text": "x"}).sensitive
    assert "no element 'u99'" in facts_of(CLICK, {"ref": "u99"}).problem
    assert facts_of(KEY, {"keys": "ctrl+s"}).site == "Notepad.exe"            # 按键不给编号：看窗口
    assert facts_of(READ, {"window": "w203"}).forbidden == "run"              # "运行"框
    assert facts_of(READ, {"window": "w209"}).forbidden == "own"              # 芸芸自己
    assert facts_of(READ, {"window": "w204"}).chat                            # 聊天软件
    run(desk.read("w204"))
    assert facts_of(CLICK, {"ref": "u2"}).label == "发送(S)"


def test_click_presses_without_the_mouse_and_reports_new_windows():
    desk = new_desktop()
    run(desk.read())
    backend = desk._backend
    view = run(desk.click("u3", None))                                       # 保存 → 开了"另存为"（按一下，不用切窗口）
    assert backend.done == [("press", "保存")]
    assert "Pressed u3 (invoke)." in view.note and "A new window opened: w205 · Notepad.exe · 另存为" in view.note
    assert view.window == "w201"                                              # 交回的还是这个窗口
    view = run(desk.click("u5", None))                                       # 复选框：读回来的是新状态
    assert '- checkbox "自动换行" [ref=u5] (checked)' in view.snapshot
    run(desk.read("w205"))
    assert run(desk.facts(CLICK, {"ref": "u1"})).context == "文件名已存在，要替换它吗？"
    view = run(desk.click("u2", None))                                       # 否 → 对话框关了
    assert "That window is closed now." in view.note
    assert run(desk.facts(CLICK, {"ref": "u1"})).problem.startswith("Read a window")


def test_type_puts_text_in_or_types_it_after_bringing_the_window_to_front():
    desk = new_desktop()
    run(desk.read())
    backend, fronted = desk._backend, []

    async def bring_to_front(window):
        fronted.append(window.hwnd)
        backend.front = window.pid

    view = run(desk.type("u2", "第一行\n第二行", bring_to_front))             # 能直接放字：不用切窗口
    assert backend.done == [("value", "文本编辑器", "第一行\n第二行")] and not fronted
    assert "replacing what was there" in view.note
    backend.controls[201][3].actions = ("focus",)                            # 这回不能直接放字：切到前面、一个个打
    run(desk.read())
    view = run(desk.type("u2", "再见", bring_to_front))
    assert fronted == [201] and backend.done[-2:] == [("focus", "文本编辑器"), ("text", "再见")]
    assert "at the cursor" in view.note
    with pytest.raises(DesktopError, match="password"):
        run(desk.type("u6", "hunter2", bring_to_front))


def test_keys_are_only_sent_when_the_window_is_really_in_front():
    desk = new_desktop()
    run(desk.read())
    backend = desk._backend

    async def stays_behind(window):   # 芸芸本体说切了，可最前面还是别的窗口（你在用别的）
        pass

    with pytest.raises(DesktopError, match="isn't in front"):
        run(desk.key("ctrl+s", "", stays_behind))
    assert not [d for d in backend.done if d[0] == "keys"]

    async def comes_to_front(window):
        backend.front = window.pid
    run(desk.key("Ctrl+Shift+S", "u2", comes_to_front))
    assert backend.done[-2:] == [("focus", "文本编辑器"), ("keys", ("ctrl", "shift"), "s")]
    with pytest.raises(DesktopError, match="Unknown keys"):
        run(desk.key("hyper+x", "", comes_to_front))


def test_reads_again_when_an_app_is_still_preparing(monkeypatch):
    """QQ 这类网页技术做的软件第一次被读时什么都没有，过一会儿才有（用户实测）：读不到能操作的就等一下再读。"""
    monkeypatch.setattr(desktop_module, "RETRY_S", 0)
    desk = new_desktop()

    async def main():
        await desk._run(lambda b: None)
        desk._backend.lazy = {204: 2, 202: 5}
        view = await desk.read("w204")                                         # QQ（Chrome_WidgetWin_1）：第三次读到了
        assert '- edit "输入" [ref=u1]' in view.snapshot and desk._backend.reads == 3
        view = await desk.read("w202")                                         # 别的软件只多读一次
        assert "Nothing here can be operated" in view.note and desk._backend.reads == 5
    run(main())


def test_closed_window_is_explained():
    desk = new_desktop()
    run(desk.read())
    desk._backend.list = [w for w in desk._backend.list if w.hwnd != 201]
    assert "has been closed" in run(desk.facts(CLICK, {"ref": "u3"})).problem


# ---------- 用鼠标点、交还鼠标（11.6c） ----------

import fake_desktop  # noqa: E402

from yunyun.assistant.desktop import TAKEN_BACK  # noqa: E402


def holding_desktop():
    """读了记事本、带着"芸芸本体"（切窗口）和 on_control 记录的 Desktop。"""
    told = []
    desk = Desktop(own_pids={OWN_PID}, on_control=lambda active, taken_back: told.append((active, taken_back)))

    async def bring_to_front(window):
        desk._backend.front = window.pid
    return desk, told, bring_to_front


def test_mouse_click_brings_the_window_front_and_holds_the_mouse():
    desk, told, front = holding_desktop()

    async def main():
        await desk.read()
        view = await desk.click("u7", front)                                  # 自己画的按钮：用鼠标点它的中间
        assert desk._backend.done[-1] == ("mouse", 120, 210) and "clicked with the mouse" in view.note
        assert told == [(True, False)]                                         # 告诉本体：芸芸拿着鼠标
        await desk.click("u3", front)                                          # 拿着的时候按别的：不再说一遍
        assert told == [(True, False)]
        await desk.release()                                                   # 这一问答完了：光标放回原处
        assert desk._backend.done[-1] == ("move", 500, 500) and told[-1] == (False, False)
    run(main())


def test_moving_the_mouse_takes_it_back_for_the_rest_of_the_turn():
    desk, told, front = holding_desktop()

    async def main():
        await desk.read()
        await desk.click("u7", front)
        await asyncio.sleep(0.15)
        assert told == [(True, False)]                                         # 光标没动：还拿着
        fake_desktop.CURSOR["pos"] = (900, 40)                                  # 你动了鼠标
        await asyncio.sleep(0.15)
        assert told[-1] == (False, True) and desk.taken_back
        assert ("move", 500, 500) not in desk._backend.done                    # 你拿回去的：不把光标挪回去
        assert (await desk.facts(CLICK, {"ref": "u3"})).problem == TAKEN_BACK   # 剩下的都不做（权限规则据此拒绝）
        assert (await desk.facts(KEY, {"keys": "tab"})).problem == TAKEN_BACK
        assert not (await desk.facts(READ, {})).problem                         # 读还可以
        for action in (desk.click("u3", front), desk.type("u2", "x", front), desk.key("tab", "", front)):
            with pytest.raises(DesktopError, match="took control back"):
                await action
        desk.new_turn()                                                        # 你说"继续"：新的一问又能做了
        assert not (await desk.facts(CLICK, {"ref": "u3"})).problem
    run(main())


def test_no_mouse_click_on_a_covered_spot_or_a_window_behind():
    desk, told, front = holding_desktop()

    async def stays_behind(window):
        pass

    async def main():
        await desk.read()
        with pytest.raises(DesktopError, match="isn't in front"):
            await desk.click("u7", stays_behind)
        desk._backend.cover = 999                                              # 那一点被别的窗口挡着
        with pytest.raises(DesktopError, match="covering"):
            await desk.click("u7", front)
        assert not [d for d in desk._backend.done if d[0] == "mouse"] and not told
    run(main())


def test_mouse_control_reaches_yunyun(session):
    told = []
    session.mouse_control.connect(lambda active, taken_back: told.append((active, taken_back)))
    session.ask("帮我点一下")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.MouseControl(True), protocol.MouseControl(False, True))
    assert told == [(True, False), (False, True)]
    session.close()
    assert told[-1] == (False, False)                                          # 子进程没了：小条也收起来


def test_tests_never_use_real_ui_automation():
    assert desktop_module._Backend.__name__ == "FakeDesktopBackend"


# ---------- 工具 ----------

class FakeBridge:
    """假装芸芸本体把窗口切到了前面（ok=False：没切成）。"""
    def __init__(self, desk, ok=True):
        self.desk, self.ok, self.requests = desk, ok, []

    async def request(self, make_request, reply_type):
        request = make_request(1)
        self.requests.append(request)
        if self.ok:
            hwnd = int(request.window[1:])
            self.desk._backend.front = next(w.pid for w in self.desk._backend.list if w.hwnd == hwnd)
        return reply_type(1, self.ok, detail="" if self.ok else "Window not found.")


def call(desk, name, args, bridge=None):
    tool = next(t for t in desktop_tools(desk, bridge or FakeBridge(desk)) if t.name == name)
    result = asyncio.run(tool.handler(args))
    return result.get("is_error", False), result["content"][0]["text"]


def test_tool_names_match_the_protocol():
    names = {f"mcp__yunyun__{t.name}" for t in desktop_tools(new_desktop(), None)}
    assert names == set(protocol.UI_TOOLS)


def test_desktop_tools():
    desk = new_desktop()
    error, text = call(desk, "ui_read", {})
    assert not error and text.startswith("Window: w201 · Notepad.exe · 无标题 - 记事本\n")
    assert call(desk, "ui_click", {"ref": "u99"})[0]                         # 编号不对：告诉模型
    bridge = FakeBridge(desk)
    error, text = call(desk, "ui_key", {"keys": "ctrl+s"}, bridge)
    assert not error and "Pressed ctrl+s." in text
    assert [(r.action, r.window) for r in bridge.requests] == [("switch", "w201")]   # 请芸芸本体切到前面
    error, text = call(desk, "ui_key", {"keys": "tab"}, FakeBridge(desk, ok=False))
    assert error and "Window not found." in text
    error, text = call(desk, "ui_type", {"ref": "u2", "text": "猫"})
    assert not error and ("value", "文本编辑器", "猫") in desk._backend.done


def test_view_text_comes_in_parts(monkeypatch):
    from yunyun.assistant.tools import browser as browser_tools_module
    monkeypatch.setattr(browser_tools_module, "PART_CHARS", 30)
    view = desktop_module.UiView("w1", "a.exe", "标题", "\n".join(f'- button "按钮{i}" [ref=u{i}]' for i in range(9)))
    assert "(Part 2 of " in view_text(view, 2) and "按钮" in view_text(view, 2)
    closed = desktop_module.UiView("w1", "a.exe", "标题", "", "That window is closed now.")
    assert view_text(closed).endswith("That window is closed now.\n")


def test_brain_has_the_desktop_tools_and_says_so():
    from yunyun.assistant import claude_brain
    from yunyun.assistant.persona import _APPS_NO_INSIDE, persona
    from yunyun.assistant.settings import AssistantSettings
    brain = claude_brain.ClaudeBrain(AssistantSettings(), "k", "zh", lambda e: None)
    prompt = claude_brain.build_options(AssistantSettings(), "k", "zh", bridge=brain.bridge,
                                        desktop=brain.desktop).system_prompt["append"]
    assert "ui_read" in prompt and "打开以后你看不到" not in prompt
    without = claude_brain.build_options(AssistantSettings(), "k", "zh", bridge=brain.bridge).system_prompt["append"]
    assert "ui_read" not in without and "打开以后你看不到" in without
    for language in ("en", "ja"):   # 三种语言都把"打开以后不能操作里面"那句换掉了
        assert _APPS_NO_INSIDE[language][0] not in persona(language, apps=True, desktop=True)
        assert _APPS_NO_INSIDE[language][0] in persona(language, apps=True)


# ---------- 确认的关卡（PermissionGate） ----------

def run_gate(tool, tool_input, answer=None, grants=(), read_first=""):
    """走一遍 PreToolUse + can_use_tool（read_first：先读哪个窗口，None = 不读）。返回 (hook 的决定, 发出的事件, gate)。"""
    from yunyun.assistant.claude_brain import PermissionGate
    events, desk = [], new_desktop()
    if read_first is not None:
        run(desk.read(read_first))
    gate = PermissionGate("C:\\work", events.append, desktop=desk)
    gate.grants.update(grants)

    async def main():
        hook = await gate.pre_tool_use({"tool_name": tool, "tool_input": tool_input}, "toolu_1", None)
        if hook["hookSpecificOutput"]["permissionDecision"] == "ask":
            task = asyncio.create_task(gate.can_use_tool(tool, tool_input, type("C", (), {"tool_use_id": "toolu_1"})()))
            while not any(isinstance(e, protocol.PermissionRequest) for e in events):
                await asyncio.sleep(0.01)
            request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
            gate.reply(answer(request))
            await task
        return hook["hookSpecificOutput"]
    return asyncio.run(main()), events, gate


def test_gate_asks_with_what_yunyun_saw_and_remembers_the_app():
    hook, events, gate = run_gate(CLICK, {"ref": "u3"},
                                  answer=lambda r: protocol.PermissionReply(r.request_id, True, "", True))
    request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
    assert (request.kind, request.scope, request.element, request.can_remember) == (
        "ui_click", "Notepad.exe", '- button "保存"', True)
    assert ("ui", "notepad.exe") in gate.grants
    decision = next(e for e in events if isinstance(e, protocol.ToolDecision))
    assert decision.decision == "remembered" and decision.detail == 'Notepad.exe · - button "保存"'


def test_gate_reads_without_asking_and_blocks_forbidden_windows():
    hook, events, _ = run_gate(READ, {}, read_first=None)
    assert hook["permissionDecision"] == "allow"
    hook, events, _ = run_gate(READ, {"window": "w203"}, read_first=None)
    assert hook["permissionDecision"] == "deny" and "Run box" in hook["permissionDecisionReason"]
    assert next(e for e in events if isinstance(e, protocol.ToolDecision)).decision == "blocked"


def test_gate_key_and_type_details():
    _, events, _ = run_gate(KEY, {"keys": "ctrl+s"}, grants={("ui", "notepad.exe")})
    assert next(e for e in events if isinstance(e, protocol.ToolDecision)).detail == "Notepad.exe · ctrl+s"
    _, events, _ = run_gate(TYPE, {"ref": "u2", "text": "猫"}, grants={("ui", "notepad.exe")})
    assert next(e for e in events if isinstance(e, protocol.ToolDecision)).detail.endswith("← 猫")
    hook, events, _ = run_gate(CLICK, {"ref": "u2"}, read_first="w204")      # QQ 的"发送(S)"
    assert hook["permissionDecision"] == "deny" and "send" in hook["permissionDecisionReason"]


# ---------- 确认卡片、操作记录 ----------

def ui_request(kind="ui_click", tool=CLICK, data=None, danger=False, element='- button "保存"'):
    return protocol.PermissionRequest(1, tool, data or {"ref": "u3"}, kind, not danger, APP, danger, "t", element)


def test_confirm_card_for_app_actions(qapp, monkeypatch):
    from yunyun.pet import assistant_confirm as card
    monkeypatch.setattr(i18n, "_current", "zh")
    click = ui_request()
    assert i18n.tr(card.title_key(click)) == "芸芸想在软件里按一下"
    assert card.element_text(click.element) == "按钮「保存」"
    assert card.element_text('- edit "搜索" value="芸芸"') == "输入框「搜索」"
    assert card.element_text('- checkbox "自动换行" (checked)') == "复选框「自动换行」"
    assert card.element_text('- slider "音量"') == 'slider "音量"'            # 认不得的原样
    html = card.detail_html(click, "")
    assert APP in html and "按钮「保存」" in html
    assert card.remember_text(click) == f"这次对话在 {APP} 都允许" and card.note_key(click) is None
    risky = ui_request(danger=True, element='- button "删除"')
    assert card.note_key(risky) == "assistant_confirm_note_ui_risky" and card.remember_text(risky) == ""
    typing = ui_request("ui_type", TYPE, {"ref": "u2", "text": "<b>猫</b>"}, element='- document "文本编辑器"')
    assert "&lt;b&gt;猫&lt;/b&gt;" in card.detail_html(typing, "") and "输入框「文本编辑器」" in card.detail_html(typing, "")
    keys = ui_request("ui_key", KEY, {"keys": "ctrl+s"}, element="")
    assert "按键：ctrl+s" in card.detail_html(keys, "")
    reading = ui_request("ui_read", READ, {}, element="")
    assert i18n.tr(card.title_key(reading)) == "芸芸想看一个软件窗口里的内容"
    assert card.remember_text(reading) == "这次对话都允许看窗口" and "服务商" in card.detail_html(reading, "")


def test_action_log_and_progress_name_the_app_tools(qapp, monkeypatch):
    from datetime import datetime
    from yunyun.assistant.session import _TOOL_TEXT, Action
    from yunyun.pet.assistant_reader import _ACTION_VERBS, actions_html
    monkeypatch.setattr(i18n, "_current", "zh")
    html = actions_html([Action(datetime.now(), protocol.UI_CLICK_TOOL, 'Notepad.exe · - button "保存"', "allowed",
                                True)])
    assert "软件里按一下" in html and "Notepad.exe" in html
    for tool in protocol.UI_TOOLS:
        for language in ("zh", "en", "ja"):
            monkeypatch.setattr(i18n, "_current", language)
            assert i18n.tr(_TOOL_TEXT[tool]) and i18n.tr(_ACTION_VERBS[tool])
