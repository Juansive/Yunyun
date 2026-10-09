"""芸芸的浏览器（阶段 6.2a）：网址规则、快照里遮掉密码 / 卡号、分段、"停止"时放过浏览器、Browser 的开关（假 Playwright）、
三个工具、会话和总管。不开真的浏览器：conftest 把 browser.start_playwright 换成了报错；真的 Edge 在 tools/try_browser.py 里测。
"""
import asyncio

import pytest

from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import browser as browser_module
from yunyun.assistant import permissions, proctree, protocol
from yunyun.assistant.browser import HIDDEN, Browser, BrowserError, mask_values, valued_refs
from yunyun.assistant.permissions import ALLOW, DENY, browser_url
from yunyun.assistant.session import AssistantSession
from yunyun.assistant.tools.browser import browser_tools, page_text, split_parts


# ---------- 网址、权限 ----------

@pytest.mark.parametrize("given, expected", [
    ("https://example.com/a?b=1", "https://example.com/a?b=1"),
    ("HTTP://Example.com", "HTTP://Example.com"),
    ("example.com/a", "https://example.com/a"),
    ("www.bilibili.com", "https://www.bilibili.com"),
    ("localhost:8000/x", "http://localhost:8000/x"),
    ("127.0.0.1:5000", "http://127.0.0.1:5000"),
    ("file:///C:/Users/me/secret.txt", ""),          # 本机文件：会绕过"读工作文件夹以外要问"
    ("javascript:alert(1)", ""),
    ("data:text/html,<b>x</b>", ""),
    ("edge://settings", ""),
    ("https:example.com", ""),
    ("http://a b.com", ""),
    ("", ""), (None, ""), ("just words", ""),
])
def test_browser_url(given, expected):
    assert browser_url(given) == expected


@pytest.mark.parametrize("tool, tool_input, expected", [
    (protocol.BROWSER_OPEN_TOOL, {"url": "https://example.com"}, ALLOW),   # 看网页不用问
    (protocol.BROWSER_OPEN_TOOL, {"url": "file:///C:/x.txt"}, DENY),
    (protocol.BROWSER_OPEN_TOOL, {}, DENY),
    (protocol.BROWSER_READ_TOOL, {"part": 2}, ALLOW),
    (protocol.BROWSER_BACK_TOOL, {}, ALLOW),
    ("mcp__yunyun__browser_click", {"ref": "e1"}, DENY),                   # 点击、输入 6.2b 才有规则：先一律拒绝
])
def test_browser_rules(tool, tool_input, expected):
    assert permissions.decide(tool, tool_input, "C:\\work").action == expected


# ---------- 快照：遮掉敏感输入框的值、分段 ----------

SNAPSHOT = """- generic [ref=e1]:
  - textbox "名字" [ref=e3]: 张三
  - textbox "密码" [ref=e5]: hunter2-secret
  - textbox "Time: now" [active] [ref=e6]: "4111111111111111"
  - link "链接" [ref=e4] [cursor=pointer]:
    - /url: "#x"
  - iframe [ref=e7]:
    - textbox "卡号" [ref=f1e2]: 4242"""


def test_valued_refs_and_masking():
    assert valued_refs(SNAPSHOT) == ["e3", "e5", "e6", "f1e2"]           # 链接那种以冒号结尾的不算
    masked = mask_values(SNAPSHOT, ["e5", "e6", "f1e2"])
    assert "hunter2" not in masked and "4111" not in masked and "4242" not in masked
    assert f'- textbox "密码" [ref=e5]: {HIDDEN}' in masked
    assert f'- textbox "Time: now" [active] [ref=e6]: {HIDDEN}' in masked   # 名字里有冒号也不怕
    assert '- textbox "名字" [ref=e3]: 张三' in masked                    # 不敏感的照常
    assert '- /url: "#x"' in masked


def test_split_parts_keeps_lines_and_limits_size():
    text = "\n".join(f"- line {i}" for i in range(100))
    parts = split_parts(text, size=100)
    assert all(len(p) <= 100 for p in parts) and "".join(parts) == text
    assert split_parts("x" * 250, size=100) == ["x" * 100, "x" * 100, "x" * 50]   # 一行太长就硬切
    assert split_parts("") == [""]


def test_page_text_says_which_part(monkeypatch):
    from yunyun.assistant.tools import browser as tools_module
    monkeypatch.setattr(tools_module, "PART_CHARS", 20)
    text = page_text("https://a.com", "标题", "- a\n- b\n" * 10, part=2)
    assert text.startswith("URL: https://a.com\nTitle: 标题\n(Part 2 of")
    assert "Part" not in page_text("https://a.com", "t", "- a")              # 一段就够：不提分段
    assert "(Part 1 of" in page_text("https://a.com", "t", "- a\n" * 50, part=0)   # 段号不对就用第一段


# ---------- "停止"时放过浏览器 ----------

def test_stop_keeps_the_browser_driver_and_edge():
    root = 100
    processes = [
        (100, 1, "python.exe"),
        (200, 100, "claude.exe"), (201, 200, "conhost.exe"), (202, 200, "bash.exe"), (203, 202, "node.exe"),
        (300, 100, "node.exe"), (301, 300, "msedge.exe"), (302, 301, "msedge.exe"), (303, 301, "msedge.exe"),
        (400, 100, "powershell.exe"),
    ]
    keep = proctree.keepers(root, processes)
    assert {100, 200, 201, 300, 301, 302, 303} <= keep                       # 浏览器驱动和 Edge 都留着
    assert not {202, 203, 400} & keep                                        # 命令开的 node.exe 照样结束


# ---------- Browser（假 Playwright） ----------

class FakeLocator:
    def __init__(self, page, ref):
        self.page, self.ref = page, ref

    async def evaluate(self, script, timeout=None):
        if self.ref in self.page.broken:
            raise RuntimeError("element is gone")
        return self.ref in self.page.sensitive


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url, self._title, self.closed = "about:blank", "", False
        self.snapshot, self.sensitive, self.broken = "- generic [ref=e1]", set(), set()
        self.history = []

    def is_closed(self):
        return self.closed

    async def bring_to_front(self):
        pass

    async def goto(self, url, wait_until=None):
        if "nowhere" in url:
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED at https://nowhere.example/\nCall log: …")
        self.history.append(self.url)
        self.url, self._title = url, f"页面 {url}"

    async def go_back(self, wait_until=None):
        if not self.history or self.history[-1] == "about:blank":
            return None
        self.url = self.history.pop()
        return object()

    async def wait_for_load_state(self, state, timeout=None):
        pass

    async def aria_snapshot(self, mode=None, timeout=None):
        assert mode == "ai"
        return self.snapshot

    def locator(self, selector):
        assert selector.startswith("aria-ref=")
        return FakeLocator(self, selector.split("=", 1)[1])

    async def title(self):
        return self._title


class FakeContext:
    def __init__(self):
        self.pages = [FakePage(self)]
        self.handlers = {}
        self.closed = False

    def set_default_timeout(self, ms):
        pass

    def set_default_navigation_timeout(self, ms):
        pass

    def on(self, event, handler):
        self.handlers[event] = handler

    async def new_page(self):
        page = FakePage(self)
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True

    def user_closes_window(self):
        self.closed = True
        self.handlers["close"](self)


class FakePlaywright:
    def __init__(self, launches):
        self.launches = launches
        self.stopped = False
        self.chromium = self

    async def launch_persistent_context(self, user_data_dir, **options):
        if options.get("fail"):
            raise RuntimeError("never")
        context = FakeContext()
        self.launches.append((user_data_dir, options, context))
        return context

    async def stop(self):
        self.stopped = True


@pytest.fixture
def fake_playwright(monkeypatch):
    launches = []

    async def start():
        return FakePlaywright(launches)
    monkeypatch.setattr(browser_module, "start_playwright", start)
    return launches


def test_browser_opens_lazily_with_its_own_visible_edge(fake_playwright, tmp_path):
    changes = []
    browser = Browser(profile_dir=tmp_path / "profile", on_change=changes.append)

    async def main():
        assert not browser.is_open and fake_playwright == []                 # 还没用：不打开
        url, title, _ = await browser.open("https://example.com")
        await browser.read()
        return url, title
    assert asyncio.run(main()) == ("https://example.com", "页面 https://example.com")
    assert len(fake_playwright) == 1 and changes == [True]                   # 只打开一次
    user_data_dir, options, _ = fake_playwright[0]
    assert user_data_dir == str(tmp_path / "profile") and (tmp_path / "profile").is_dir()
    assert options["channel"] == "msedge" and options["headless"] is False   # 系统 Edge，窗口看得见
    assert options["accept_downloads"] is False                              # 下载等 6.2c


def test_snapshot_hides_sensitive_values(fake_playwright, tmp_path):
    browser = Browser(profile_dir=tmp_path)

    async def main():
        await browser.open("https://example.com")
        page = fake_playwright[0][2].pages[0]
        page.snapshot = SNAPSHOT
        page.sensitive = {"e5", "f1e2"}
        page.broken = {"e6"}                                                 # 看不清是不是敏感的：也遮掉
        return (await browser.read())[2]
    text = asyncio.run(main())
    assert "张三" in text and "hunter2" not in text and "4111" not in text and "4242" not in text


def test_reopens_after_the_user_closes_the_window(fake_playwright, tmp_path):
    changes = []
    browser = Browser(profile_dir=tmp_path, on_change=changes.append)

    async def main():
        await browser.open("https://a.com")
        fake_playwright[0][2].user_closes_window()
        await asyncio.sleep(0)
        assert not browser.is_open
        await browser.open("https://b.com")
        await browser.close()
    asyncio.run(main())
    assert len(fake_playwright) == 2 and changes == [True, False, True, False]
    assert fake_playwright[1][2].closed                                      # 关助手时好好关


def test_errors_become_messages_for_the_model(fake_playwright, tmp_path, monkeypatch):
    browser = Browser(profile_dir=tmp_path)

    async def main():
        with pytest.raises(BrowserError, match="ERR_NAME_NOT_RESOLVED") as error:
            await browser.open("https://nowhere.example/")
        assert "Call log" not in str(error.value)                           # 只要第一行
        with pytest.raises(BrowserError, match="no previous page"):
            await browser.back()
    asyncio.run(main())


def test_missing_edge_is_explained(monkeypatch, tmp_path):
    class NoEdge(FakePlaywright):
        async def launch_persistent_context(self, user_data_dir, **options):
            raise RuntimeError('Chromium distribution "msedge" is not found at C:\\...')

    async def start():
        return NoEdge([])
    monkeypatch.setattr(browser_module, "start_playwright", start)
    with pytest.raises(BrowserError, match="Edge was not found"):
        asyncio.run(Browser(profile_dir=tmp_path).open("https://a.com"))


def test_tests_never_start_a_real_browser(tmp_path):
    with pytest.raises(BrowserError, match="不许真的开浏览器"):
        asyncio.run(Browser(profile_dir=tmp_path).open("https://a.com"))


# ---------- 三个工具 ----------

class StubBrowser:
    def __init__(self, snapshot="- heading \"你好\" [ref=e1]", fail=None):
        self.snapshot, self.fail, self.calls = snapshot, fail, []

    async def _answer(self, what):
        self.calls.append(what)
        if self.fail:
            raise BrowserError(self.fail)
        return "https://a.com", "标题", self.snapshot

    def open(self, url):
        return self._answer(("open", url))

    def read(self):
        return self._answer(("read",))

    def back(self):
        return self._answer(("back",))


def call(stub, name, args):
    tool = next(t for t in browser_tools(stub) if t.name == name)
    result = asyncio.run(tool.handler(args))
    return result.get("is_error", False), result["content"][0]["text"]


def test_browser_tools():
    stub = StubBrowser()
    error, text = call(stub, "browser_open", {"url": "example.com"})
    assert not error and stub.calls == [("open", "https://example.com")]    # 补上 https://
    assert "Title: 标题" in text and '"你好"' in text
    assert call(stub, "browser_open", {"url": "file:///C:/x"})[0]          # 兜底：不是网页不打开
    assert len(stub.calls) == 1
    assert not call(stub, "browser_read", {"part": 2})[0] and not call(stub, "browser_back", {})[0]
    error, text = call(StubBrowser(fail="net::ERR_NAME_NOT_RESOLVED"), "browser_open", {"url": "https://x.y"})
    assert error and "ERR_NAME_NOT_RESOLVED" in text


def test_tool_names_match_the_protocol():
    names = {f"mcp__yunyun__{t.name}" for t in browser_tools(StubBrowser())}
    assert names == {protocol.BROWSER_OPEN_TOOL, protocol.BROWSER_READ_TOOL, protocol.BROWSER_BACK_TOOL}


def test_brain_has_the_browser_tools_and_says_so():
    from yunyun.assistant import claude_brain
    from yunyun.assistant.settings import AssistantSettings
    brain = claude_brain.ClaudeBrain(AssistantSettings(), "k", "zh", lambda e: None)
    options = claude_brain.build_options(AssistantSettings(), "k", "zh", bridge=brain.bridge, browser=brain.browser)
    assert "browser_open" in options.system_prompt["append"] and "yunyun" in options.mcp_servers
    assert "browser_open" not in claude_brain.build_options(AssistantSettings(), "k", "zh").system_prompt["append"]


# ---------- 会话、总管 ----------

@pytest.fixture
def session(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    processes = []
    s = AssistantSession(make_process=lambda language, parent, resume="": processes.append(
        FakeProcess(language, parent, resume)) or processes[-1])
    s.processes = processes
    yield s
    s.close()


def test_session_knows_whether_the_browser_is_open(session):
    session.ask("打开 example.com")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.ToolStarted("t1", protocol.BROWSER_OPEN_TOOL, "example.com"))
    assert session.progress == "正在用浏览器打开…… example.com"
    process.say(protocol.BrowserChanged(True))
    assert session.browser_open
    process.say(protocol.BrowserChanged(False))
    assert not session.browser_open
    process.say(protocol.BrowserChanged(True))
    session.close()
    assert not session.browser_open                                          # 子进程一关，浏览器也没了


def test_action_log_names_the_browser_tools(qapp, monkeypatch):
    from datetime import datetime
    from yunyun.assistant.session import Action
    from yunyun.pet.assistant_reader import actions_html
    monkeypatch.setattr(i18n, "_current", "zh")
    html = actions_html([Action(datetime.now(), protocol.BROWSER_OPEN_TOOL, "https://a.com", "auto", True)])
    assert "浏览器打开" in html and "https://a.com" in html
