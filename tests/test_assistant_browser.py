"""芸芸的浏览器（阶段 6.2a）：网址规则、快照里遮掉密码 / 卡号、分段、"停止"时放过浏览器、Browser 的开关（假 Playwright）、
三个工具、会话和总管。不开真的浏览器：conftest 把 browser.start_playwright 换成了报错；真的 Edge 在 tools/try_browser.py 里测。
"""
import asyncio

import pytest

from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import browser as browser_module
from yunyun.assistant import permissions, proctree, protocol
from yunyun.assistant.browser import HIDDEN, Browser, BrowserError, PageView, describe, mask_values, valued_refs
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
    (protocol.BROWSER_CLICK_TOOL, {"ref": "e1"}, DENY),                    # 没看过这个元素（element=None）：拒绝
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
    text = page_text(PageView("https://a.com", "标题", "- a\n- b\n" * 10), part=2)
    assert text.startswith("URL: https://a.com\nTitle: 标题\n(Part 2 of")
    assert "Part" not in page_text(PageView("https://a.com", "t", "- a"))   # 一段就够：不提分段
    assert "(Part 1 of" in page_text(PageView("https://a.com", "t", "- a\n" * 50), part=0)   # 段号不对就用第一段
    assert page_text(PageView("https://a.com", "t", "- a", "It opened in a new tab.")).startswith("It opened")


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
        if script is browser_module.FACTS_JS:
            return {"label": "", **self.page.facts.get(self.ref, {}), "sensitive": self.ref in self.page.sensitive}
        return self.ref in self.page.sensitive

    async def click(self):
        self.page.done.append(("click", self.ref))
        if self.ref == "popup":                                          # 这个链接在新标签页打开
            new = await self.page.context.new_page()
            new.url, new._title = "https://a.com/new", "新标签页"
        elif self.ref.startswith("dl"):                                  # 这个按钮会下载一个文件
            self.page.start_download("报告.pdf")
            if self.ref == "dlhub":                                      # Edge 第一次下载时自己开的下载列表
                hub = await self.page.context.new_page()
                hub.url = "edge://downloads-hub/"

    async def fill(self, text):
        self.page.done.append(("fill", self.ref, text))

    async def press(self, key):
        self.page.done.append(("press", self.ref, key))

    async def select_option(self, values):
        self.page.done.append(("select", self.ref, values))


class FakeDownload:
    def __init__(self, name, fail=False):
        self.suggested_filename, self.fail = name, fail
        self.cancelled, self.saved_to = False, None

    async def save_as(self, path):
        await asyncio.sleep(0)
        if self.cancelled or self.fail:
            raise RuntimeError("Download was canceled")
        with open(path, "w", encoding="utf-8") as f:
            f.write("文件内容")
        self.saved_to = path

    async def cancel(self):
        self.cancelled = True


class FakePage:
    def __init__(self, context):
        self.context = context
        self.url, self._title, self.closed = "about:blank", "", False
        self.snapshot, self.sensitive, self.broken = "- generic [ref=e1]", set(), set()
        self.facts, self.done, self.visible = {}, [], True
        self.history = []
        self.handlers = {}
        self.waiting_download = None   # expect_download 正在等的

    def is_closed(self):
        return self.closed

    def on(self, event, handler):
        self.handlers[event] = handler

    def start_download(self, name):
        """网页开始下载 name：expect_download 在等就交给它；浏览器的 download 事件照样发。"""
        download = FakeDownload(name)
        if self.waiting_download is not None and not self.waiting_download.done():
            self.waiting_download.set_result(download)
        self.handlers["download"](download)
        return download

    def expect_download(self, timeout=None):
        page = self

        class Waiting:
            async def __aenter__(self):
                page.waiting_download = asyncio.get_running_loop().create_future()
                return self

            async def __aexit__(self, *exc):
                return False

            @property
            def value(self):
                async def wait():
                    try:
                        return await asyncio.wait_for(page.waiting_download, 0.2)
                    except asyncio.TimeoutError:
                        raise TimeoutError("Timeout 15000ms exceeded while waiting for event \"download\"")
                return wait()
        return Waiting()

    async def evaluate(self, script):
        assert script == "document.visibilityState"
        return "visible" if self.visible else "hidden"

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
        if "page" in self.handlers:
            self.handlers["page"](page)
        return page

    async def close(self):
        self.closed = True

    async def cookies(self, url=None):
        if self.closed:
            raise RuntimeError("Target page, context or browser has been closed")
        return []

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
        view = await browser.open("https://example.com")
        await browser.read()
        return view.url, view.title
    assert asyncio.run(main()) == ("https://example.com", "页面 https://example.com")
    assert len(fake_playwright) == 1 and changes == [True]                   # 只打开一次
    user_data_dir, options, _ = fake_playwright[0]
    assert user_data_dir == str(tmp_path / "profile") and (tmp_path / "profile").is_dir()
    assert options["channel"] == "msedge" and options["headless"] is False   # 系统 Edge，窗口看得见
    assert options["accept_downloads"] is True                               # 下载由芸芸自己管（6.2c）


def test_snapshot_hides_sensitive_values(fake_playwright, tmp_path):
    browser = Browser(profile_dir=tmp_path)

    async def main():
        await browser.open("https://example.com")
        page = fake_playwright[0][2].pages[0]
        page.snapshot = SNAPSHOT
        page.sensitive = {"e5", "f1e2"}
        page.broken = {"e6"}                                                 # 看不清是不是敏感的：也遮掉
        return (await browser.read()).snapshot
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
        return PageView("https://a.com", "标题", self.snapshot)

    def open(self, url):
        return self._answer(("open", url))

    def read(self):
        return self._answer(("read",))

    def back(self):
        return self._answer(("back",))

    def click(self, ref):
        return self._answer(("click", ref))

    def type(self, ref, text, submit=False):
        return self._answer(("type", ref, text, submit))

    def select(self, ref, values):
        return self._answer(("select", ref, values))

    async def download(self, ref):
        await self._answer(("download", ref))
        return "a.zip", "C:\\D\\a.zip", 3


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
    assert names == {protocol.BROWSER_OPEN_TOOL, protocol.BROWSER_READ_TOOL, protocol.BROWSER_BACK_TOOL,
                     *protocol.BROWSER_ACTION_TOOLS}


def test_action_tools_pass_their_arguments():
    stub = StubBrowser()
    assert not call(stub, "browser_click", {"ref": "e5"})[0]
    assert not call(stub, "browser_type", {"ref": "e6", "text": "芸芸", "submit": True})[0]
    assert not call(stub, "browser_select", {"ref": "e7", "values": "乙"})[0]           # 只给一个字符串也行
    assert stub.calls == [("click", "e5"), ("type", "e6", "芸芸", True), ("select", "e7", ["乙"])]
    assert call(stub, "browser_select", {"ref": "e7", "values": []})[0]


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


# ================= 6.2b：点击、输入、选择 =================

from yunyun.assistant.permissions import ASK, ElementFacts, is_risky, site_of  # noqa: E402

CLICK, TYPE, SELECT = protocol.BROWSER_CLICK_TOOL, protocol.BROWSER_TYPE_TOOL, protocol.BROWSER_SELECT_TOOL
SITE = "bilibili.com"


def facts(**kw):
    return ElementFacts(site=SITE, element='button "确定"', **kw)


@pytest.mark.parametrize("tool, tool_input, element, grants, expected", [
    # 普通的点击、输入、选择：每次问，可以"这个网站都允许"
    (CLICK, {"ref": "e5"}, facts(label="下一页"), (), (ASK, "web_click", True, False)),
    (TYPE, {"ref": "e5", "text": "x"}, facts(), (), (ASK, "web_type", True, False)),
    (SELECT, {"ref": "e5", "values": ["乙"]}, facts(), (), (ASK, "web_select", True, False)),
    (CLICK, {"ref": "e5"}, facts(), (("web", SITE),), (ALLOW, "", False, False)),
    (CLICK, {"ref": "e5"}, facts(), (("web", "taobao.com"),), (ASK, "web_click", True, False)),   # 别的网站不算
    # 撤不回的：每次问、标红、不给"都允许"——就算这个网站已经"都允许"了
    (CLICK, {"ref": "e5"}, facts(submit=True), (("web", SITE),), (ASK, "web_click", False, True)),
    (CLICK, {"ref": "e5"}, facts(download=True), (("web", SITE),), (DENY, "", False, False)),   # 下载要用下载工具
    (protocol.BROWSER_DOWNLOAD_TOOL, {"ref": "e5"}, facts(download=True), (("web", SITE),),
     (ASK, "web_download", False, True)),                                                      # 下载：每次问、不给都允许
    (protocol.BROWSER_DOWNLOAD_TOOL, {"ref": "e5"}, facts(), (), (ASK, "web_download", False, True)),
    (CLICK, {"ref": "e5"}, facts(label="立即购买"), (("web", SITE),), (ASK, "web_click", False, True)),
    (CLICK, {"ref": "e5"}, facts(label="Place order"), (), (ASK, "web_click", False, True)),
    (CLICK, {"ref": "e5"}, facts(label="删除这条评论"), (), (ASK, "web_click", False, True)),
    (TYPE, {"ref": "e5", "text": "hi", "submit": True}, facts(), (("web", SITE),), (ASK, "web_type", False, True)),
    # 搜索不算提交
    (TYPE, {"ref": "e5", "text": "猫", "submit": True}, facts(search=True), (("web", SITE),), (ALLOW, "", False, False)),
    (CLICK, {"ref": "e5"}, facts(submit=True, search=True), (), (ASK, "web_click", True, False)),
    # 长长的一块内容里碰巧有"删除"：不当成按钮
    (CLICK, {"ref": "e5"}, facts(label="一大段文字，" * 10 + "删除"), (), (ASK, "web_click", True, False)),
    # 密码、卡号、验证码：一律不填（点一下没关系）
    (TYPE, {"ref": "e5", "text": "123456"}, facts(sensitive=True), (("web", SITE),), (DENY, "", False, False)),
    (CLICK, {"ref": "e5"}, facts(sensitive=True), (), (ASK, "web_click", True, False)),
    # 没看清这个元素：拒绝（原因告诉模型）
    (CLICK, {"ref": "e99"}, ElementFacts(problem="No element [ref=e99]"), (), (DENY, "", False, False)),
])
def test_web_action_rules(tool, tool_input, element, grants, expected):
    decision = permissions.decide(tool, tool_input, "C:\\work", frozenset(grants), element=element)
    assert (decision.action, decision.kind, bool(decision.grant), decision.danger) == expected


def test_web_grant_and_reasons():
    decision = permissions.decide(CLICK, {"ref": "e5"}, "C:\\work", element=facts())
    assert decision.grant == (("web", SITE),) and decision.scope == SITE
    denied = permissions.decide(TYPE, {"ref": "e5", "text": "x"}, "C:\\work", element=facts(sensitive=True))
    assert "ask the user" in denied.reason
    assert permissions.decide(CLICK, {}, "C:\\work", element=ElementFacts(problem="read again")).reason == "read again"
    assert is_risky(CLICK, {}, facts(label="Buy now")) and not is_risky(CLICK, {}, facts(label="Buyer guide"))


@pytest.mark.parametrize("url, site", [
    ("https://www.bilibili.com/video/1", "bilibili.com"),
    ("https://search.bilibili.com/all?keyword=x", "bilibili.com"),
    ("https://item.taobao.com.cn/x", "taobao.com.cn"),
    ("https://www.bbc.co.uk/news", "bbc.co.uk"),
    ("http://localhost:8000/a", "localhost"),
    ("http://127.0.0.1:5000/", "127.0.0.1"),
    ("https://example.com", "example.com"),
    ("about:blank", ""),
])
def test_site_of(url, site):
    assert site_of(url) == site


@pytest.mark.parametrize("url, expected", [
    ("https://a.com/done.html?name=%E6%9D%8E&pw=hunter2&card=4111&city=sh",
     f"https://a.com/done.html?name=%E6%9D%8E&pw={HIDDEN}&card={HIDDEN}&city=sh"),   # GET 提交的表单
    ("https://a.com/cb?code=abc&state=x#access_token=t0k", f"https://a.com/cb?code={HIDDEN}&state=x#access_token={HIDDEN}"),
    ("https://s.com/search?keyword=猫&q=狗", "https://s.com/search?keyword=猫&q=狗"),   # 搜索词照常
    ("https://a.com/path/token/x", "https://a.com/path/token/x"),                       # 只看参数
    ("about:blank", "about:blank"),
])
def test_clean_url_hides_secret_parameters(url, expected):
    from yunyun.assistant.browser import clean_url
    assert clean_url(url) == expected


def test_describe_takes_the_line_from_the_snapshot():
    snapshot = '- generic [ref=e1]:\n  - button "提交订单" [active] [ref=e12] [cursor=pointer]\n  - textbox "名字" [ref=e3]: 张三'
    assert describe(snapshot, "e12") == 'button "提交订单"'
    assert describe(snapshot, "e3") == 'textbox "名字"'                    # 值不带
    assert describe(snapshot, "e1") == "generic" and describe(snapshot, "e9") == ""


# ---------- Browser：看元素、点击、输入（假 Playwright） ----------

FORM_SNAPSHOT = '- textbox "密码" [ref=e5]: (hidden)\n- button "提交订单" [ref=e6]\n- link "新窗口" [ref=popup]'


def opened_browser(fake_playwright, tmp_path):
    browser = Browser(profile_dir=tmp_path)

    async def prepare():
        await browser.open("https://www.shop.com/cart")
        page = fake_playwright[0][2].pages[0]
        page.snapshot = FORM_SNAPSHOT
        page.sensitive = {"e5"}
        page.facts = {"e6": {"label": "提交订单", "submit": True}}
        await browser.read()
        return page
    return browser, prepare


def test_element_facts_come_from_the_page(fake_playwright, tmp_path):
    browser, prepare = opened_browser(fake_playwright, tmp_path)

    async def main():
        await prepare()
        return (await browser.element_facts("e6"), await browser.element_facts("e5"),
                await browser.element_facts("e6 >> css=body"), await browser.element_facts("e1"))
    button, password, bad, _ = asyncio.run(main())
    assert button == ElementFacts(site="shop.com", element='button "提交订单"', label="提交订单", submit=True)
    assert password.sensitive and password.element == 'textbox "密码"'
    assert "like e5" in bad.problem                                          # 编号只能是 e5 这样，拼不出别的选择器


def test_element_facts_do_not_open_the_browser(fake_playwright, tmp_path):
    browser = Browser(profile_dir=tmp_path)
    result = asyncio.run(browser.element_facts("e1"))
    assert "isn't open" in result.problem and fake_playwright == []


def test_missing_element_is_a_problem(fake_playwright, tmp_path):
    browser, prepare = opened_browser(fake_playwright, tmp_path)

    async def main():
        page = await prepare()
        page.broken = {"e6"}
        return await browser.element_facts("e6")
    assert "browser_read" in asyncio.run(main()).problem


def test_click_type_select_and_follow_a_new_tab(fake_playwright, tmp_path, monkeypatch):
    monkeypatch.setattr(browser_module, "AFTER_ACTION_S", 0)
    browser, prepare = opened_browser(fake_playwright, tmp_path)

    async def main():
        page = await prepare()
        await browser.type("e7", "芸芸", submit=True)
        await browser.select("e8", ["乙"])
        await browser.click("e6")
        view = await browser.click("popup")
        after = await browser.read()
        return page, view, after
    page, view, after = asyncio.run(main())
    assert page.done == [("fill", "e7", "芸芸"), ("press", "e7", "Enter"), ("select", "e8", ["乙"]),
                         ("click", "e6"), ("click", "popup")]
    assert view.title == "新标签页" and "new tab" in view.note                # 跟到新标签页
    assert after.url == "https://a.com/new"                                   # 之后读的也是它


def test_type_refuses_sensitive_fields_again(fake_playwright, tmp_path):
    browser, prepare = opened_browser(fake_playwright, tmp_path)

    async def main():
        page = await prepare()
        with pytest.raises(BrowserError, match="password"):
            await browser.type("e5", "hunter2")
        return page
    assert asyncio.run(main()).done == []                                     # 一个字都没填


def test_follows_the_tab_the_user_switched_to(fake_playwright, tmp_path):
    browser = Browser(profile_dir=tmp_path)

    async def main():
        await browser.open("https://a.com")
        context = fake_playwright[0][2]
        first = context.pages[0]
        second = await context.new_page()
        second.url, second._title = "https://b.com", "你切过去的"
        first.visible = False                                                  # 你在窗口里切到了第二个标签页
        return await browser.read()
    assert asyncio.run(main()).title == "你切过去的"


def test_actions_are_not_redone_when_the_browser_dies(fake_playwright, tmp_path):
    browser, prepare = opened_browser(fake_playwright, tmp_path)

    async def main():
        page = await prepare()
        context = fake_playwright[0][2]

        async def dies():
            context.closed = True
            raise RuntimeError("Target page, context or browser has been closed")
        page.locator = lambda selector: type("L", (), {"click": staticmethod(dies)})()
        with pytest.raises(BrowserError, match="browser was closed"):
            await browser.click("e6")
    asyncio.run(main())
    assert len(fake_playwright) == 1                                          # 没有重新打开再点一次


# ---------- PermissionGate：问浏览器、再问你 ----------

class FactsBrowser:
    def __init__(self, element):
        self.element, self.asked = element, []

    async def element_facts(self, ref):
        self.asked.append(ref)
        return self.element


def run_gate(element, tool=CLICK, tool_input=None, answer=None, grants=()):
    """走一遍 PreToolUse + can_use_tool；answer(request) → PermissionReply。返回 (hook 的决定, 发出的事件, 结果)。"""
    from yunyun.assistant.claude_brain import PermissionGate
    tool_input = tool_input or {"ref": "e5"}
    events = []
    gate = PermissionGate("C:\\work", events.append, browser=FactsBrowser(element))
    gate.grants.update(grants)

    async def main():
        hook = await gate.pre_tool_use({"tool_name": tool, "tool_input": tool_input}, "toolu_1", None)
        result = None
        if hook["hookSpecificOutput"]["permissionDecision"] == "ask":
            task = asyncio.create_task(gate.can_use_tool(tool, tool_input, type("C", (), {"tool_use_id": "toolu_1"})()))
            await asyncio.sleep(0)
            request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
            gate.reply(answer(request))
            result = await task
        return hook["hookSpecificOutput"], result
    hook, result = asyncio.run(main())
    return hook, events, result, gate


def test_gate_shows_the_element_yunyun_saw_and_remembers_the_site():
    hook, events, result, gate = run_gate(facts(label="下一页"),
                                          answer=lambda r: protocol.PermissionReply(r.request_id, True, "", True))
    request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
    assert (request.kind, request.scope, request.element, request.can_remember) == ("web_click", SITE,
                                                                                    'button "确定"', True)
    assert ("web", SITE) in gate.grants and type(result).__name__ == "PermissionResultAllow"
    decision = next(e for e in events if isinstance(e, protocol.ToolDecision))
    assert decision.decision == "remembered" and decision.detail == f'{SITE} · button "确定"'


def test_gate_risky_click_cannot_be_remembered():
    _, events, _, gate = run_gate(facts(submit=True), grants={("web", SITE)},
                                  answer=lambda r: protocol.PermissionReply(r.request_id, True, "", True))
    request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
    assert request.danger and not request.can_remember


def test_gate_refuses_to_type_a_password_without_asking():
    hook, events, _, _ = run_gate(facts(sensitive=True), tool=TYPE, tool_input={"ref": "e5", "text": "hunter2"})
    assert hook["permissionDecision"] == "deny" and "ask the user" in hook["permissionDecisionReason"]
    assert not any(isinstance(e, protocol.PermissionRequest) for e in events)
    decision = next(e for e in events if isinstance(e, protocol.ToolDecision))
    assert decision.decision == "blocked"


def test_gate_type_detail_has_the_text():
    _, events, _, _ = run_gate(facts(), tool=TYPE, tool_input={"ref": "e5", "text": "猫"}, grants={("web", SITE)})
    decision = next(e for e in events if isinstance(e, protocol.ToolDecision))
    assert decision.decision == "granted" and decision.detail.endswith("← 猫")


# ---------- 确认卡片 ----------

def web_request(kind="web_click", tool=CLICK, data=None, danger=False, element='button "提交订单"'):
    return protocol.PermissionRequest(1, tool, data or {"ref": "e6"}, kind, not danger, SITE, danger, "t", element)


def test_confirm_card_for_web_actions(qapp, monkeypatch):
    from yunyun.pet import assistant_confirm as card
    monkeypatch.setattr(i18n, "_current", "zh")
    click = web_request()
    assert i18n.tr(card.title_key(click)) == "芸芸想在网页上点一下"
    assert card.element_text(click.element) == "按钮「提交订单」"
    assert card.element_text("generic") == "generic"                         # 认不得的原样
    html = card.detail_html(click, "")
    assert SITE in html and "按钮「提交订单」" in html
    assert card.remember_text(click) == f"这次对话在 {SITE} 都允许" and card.note_key(click) is None
    risky = web_request(danger=True)
    assert card.note_key(risky) == "assistant_confirm_note_web_risky" and card.remember_text(risky) == ""
    typing = web_request("web_type", TYPE, {"ref": "e7", "text": "<b>猫</b>", "submit": True}, element='searchbox "搜索"')
    html = card.detail_html(typing, "")
    assert "&lt;b&gt;猫&lt;/b&gt;" in html and "输入后按回车" in html and "搜索框「搜索」" in html
    choosing = web_request("web_select", SELECT, {"ref": "e8", "values": ["甲", "乙"]}, element='combobox "城市"')
    assert "甲 · 乙" in card.detail_html(choosing, "")


# ================= 6.2c：下载 =================

from yunyun import paths  # noqa: E402
from yunyun.assistant.browser import safe_name, unique_path  # noqa: E402


@pytest.mark.parametrize("given, expected", [
    ("报告.pdf", "报告.pdf"),
    ("..\\..\\Windows\\evil.exe", "evil.exe"),                # 不许带路径
    ("a/b/c.zip", "c.zip"),
    ('what?"is<this>.txt', "what__is_this_.txt"),
    ("CON.txt", "download_CON.txt"),                          # Windows 的保留名
    ("", "download"), ("...", "download"),
    ("x" * 300 + ".zip", "x" * 140 + ".zip"),
])
def test_safe_name(given, expected):
    assert safe_name(given) == expected


def test_unique_path_numbers_like_a_browser(tmp_path):
    assert unique_path(tmp_path, "a.zip") == tmp_path / "a.zip"
    (tmp_path / "a.zip").write_text("1")
    (tmp_path / "a (1).zip").write_text("2")
    assert unique_path(tmp_path, "a.zip") == tmp_path / "a (2).zip"


def test_download_saves_to_the_downloads_folder(fake_playwright, tmp_path):
    downloads = []
    browser = Browser(profile_dir=tmp_path / "profile",
                      on_download=lambda name, path, by_user: downloads.append((name, path, by_user)))

    async def main():
        await browser.open("https://a.com")
        (paths.downloads_dir()).mkdir(parents=True, exist_ok=True)
        (paths.downloads_dir() / "报告.pdf").write_text("旧的")
        return await browser.download("dl1")
    name, path, size = asyncio.run(main())
    assert name == "报告 (1).pdf" and path == str(paths.downloads_dir() / "报告 (1).pdf") and size > 0   # 不覆盖旧的
    assert downloads == [("报告 (1).pdf", path, False)]


def test_download_that_never_starts(fake_playwright, tmp_path):
    browser = Browser(profile_dir=tmp_path)

    async def main():
        await browser.open("https://a.com")
        with pytest.raises(BrowserError, match="didn't start a download"):
            await browser.download("e1")
    asyncio.run(main())


def test_downloads_from_yunyuns_clicks_are_cancelled_and_reported(fake_playwright, tmp_path, monkeypatch):
    monkeypatch.setattr(browser_module, "AFTER_ACTION_S", 0)
    downloads = []
    browser = Browser(profile_dir=tmp_path, on_download=lambda *a: downloads.append(a))

    async def main():
        await browser.open("https://a.com")
        view = await browser.click("dl2")                 # 看不出是下载的按钮，点了却开始下载
        page = fake_playwright[0][2].pages[0]
        late = page.start_download("晚到的.zip")           # 点完一会儿才开始的：也算芸芸点出来的
        await asyncio.sleep(0)
        return view, late
    view, late = asyncio.run(main())
    assert "cancelled" in view.note and "browser_download" in view.note and "报告.pdf" in view.note
    assert late.cancelled and downloads == []
    assert not (paths.downloads_dir() / "报告.pdf").exists()


def test_edges_own_download_list_is_not_a_new_tab(fake_playwright, tmp_path, monkeypatch):
    """Edge 第一次下载时会开一个 edge://downloads-hub/：不跟过去，之后读的还是原来的网页（try_browser.py 实测发现的）。"""
    monkeypatch.setattr(browser_module, "AFTER_ACTION_S", 0)
    browser = Browser(profile_dir=tmp_path)

    async def main():
        await browser.open("https://a.com")
        view = await browser.click("dlhub")
        return view, await browser.read()
    view, after = asyncio.run(main())
    assert "new tab" not in view.note and after.url == "https://a.com"


def test_downloads_the_user_clicks_are_saved(fake_playwright, tmp_path, monkeypatch):
    downloads = []
    browser = Browser(profile_dir=tmp_path, on_download=lambda *a: downloads.append(a))

    async def main():
        await browser.open("https://a.com")
        browser._acted_at = 0                              # 芸芸好一会儿没动了：这是你自己在窗口里点的
        page = fake_playwright[0][2].pages[0]
        download = page.start_download("我自己点的.zip")
        for _ in range(5):
            await asyncio.sleep(0)
        return download
    download = asyncio.run(main())
    target = paths.downloads_dir() / "我自己点的.zip"
    assert download.saved_to == str(target) and target.exists()
    assert downloads == [("我自己点的.zip", str(target), True)]


def test_download_tool_results():
    class Downloads(StubBrowser):
        def __init__(self, size):
            super().__init__()
            self.size = size

        async def download(self, ref):
            return "a.zip", "C:\\D\\a.zip", self.size
    assert "Downloaded \"a.zip\" (2.0 MB)" in call(Downloads(2 * 1024 * 1024), "browser_download", {"ref": "e1"})[1]
    assert "Still downloading" in call(Downloads(None), "browser_download", {"ref": "e1"})[1]
    error, text = call(StubBrowser(fail="Clicking it didn't start a download."), "browser_download", {"ref": "e1"})
    assert error and "didn't start" in text


def test_gate_download_shows_the_link_and_is_never_remembered():
    _, events, _, gate = run_gate(facts(download=True, link="https://a.com/f.zip"), tool=protocol.BROWSER_DOWNLOAD_TOOL,
                                  grants={("web", SITE)},
                                  answer=lambda r: protocol.PermissionReply(r.request_id, True, "", True))
    request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
    assert (request.kind, request.link, request.can_remember, request.danger) == (
        "web_download", "https://a.com/f.zip", False, True)
    assert gate.grants == {("web", SITE)}                                   # 没有多记什么


def test_confirm_card_for_downloads(qapp, monkeypatch):
    from yunyun.pet import assistant_confirm as card
    monkeypatch.setattr(i18n, "_current", "zh")
    request = protocol.PermissionRequest(1, protocol.BROWSER_DOWNLOAD_TOOL, {"ref": "e5"}, "web_download", False, SITE,
                                         True, "t", 'link "下载安装包"', "https://a.com/%E5%AE%89%E8%A3%85.exe")
    assert i18n.tr(card.title_key(request)) == "芸芸想从网页下载一个文件"
    html = card.detail_html(request, "")
    assert "链接「下载安装包」" in html and "https://a.com/安装.exe" in html and str(paths.downloads_dir()) in html
    assert card.note_key(request) == "assistant_confirm_note_web_download" and card.remember_text(request) == ""


def test_session_passes_downloads_up(session):
    got = []
    session.downloaded.connect(got.append)
    session.ask("下载那个文件")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.Downloaded("a.zip", "C:\\D\\a.zip"))
    assert got == [protocol.Downloaded("a.zip", "C:\\D\\a.zip")]
