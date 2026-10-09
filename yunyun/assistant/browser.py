"""芸芸自己的浏览器（阶段 6.2）：Python 版 Playwright + 系统自带的 Edge + 芸芸自己的配置文件夹，窗口看得见。

**只在助手子进程里用**；playwright 到第一次用浏览器时才 import（启动约 2 秒：驱动 1.6 秒 + Edge 0.3 秒）。
- 不碰你 Chrome / Edge 里的登录：配置文件夹是 data_dir()/assistant/browser，你在这个窗口里登录过的网站下次还在。
- 进程：助手子进程 → node.exe（Playwright 的驱动）→ msedge.exe。都在助手的作业对象里：关掉助手时跟着结束；
  点"停止"时 proctree 放过驱动这一支（不然浏览器会被一起关掉）。实测驱动 + Edge 约 400 MB 私有内存。
- 看网页用 Playwright 的 aria 快照（mode="ai"）：每个元素带编号 [ref=e5]，按编号能找到元素（框架里的也行，f1e2）。
  **快照里输入框的值是原样的（密码、卡号也是）**：交给模型之前把敏感输入框的值换成 (hidden)（mask_values）；
  网址里像密码 / 卡号 / 令牌的参数也一样（clean_url：GET 提交的表单会把密码放进网址，try_browser.py 实测发现的）。
- 点击、输入、选下拉框（6.2b）：先由权限规则问过你（element_facts 告诉规则这个元素是什么：提交按钮？下载？搜索？密码框？）；
  密码框这类在 type() 里再查一遍，一律不填。点了打开新标签页就跟过去；你在窗口里切了标签页，也跟着你看的那个。
- 你自己关掉浏览器窗口也没关系：下次用时重新打开。
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import unquote_plus

from yunyun.assistant.permissions import ElementFacts, site_of
from yunyun.paths import data_dir

log = logging.getLogger(__name__)

PROFILE_DIR = data_dir() / "assistant" / "browser"
NAV_TIMEOUT_MS = 20_000      # 打开网页最多等多久
ACTION_TIMEOUT_MS = 10_000   # 别的操作（快照、看元素……）
SETTLE_MS = 2_000            # 打开后再等一会儿，让脚本把内容画出来（等不到也照常读）
HIDDEN = "(hidden)"

# 敏感的输入框：值不给模型看（6.2b 起也不许往里输入）。密码、卡号、安全码、验证码……
SENSITIVE_JS = r"""e => {
  const type = (e.type || '').toLowerCase();
  if (type === 'password') return true;
  const ac = (e.getAttribute && e.getAttribute('autocomplete') || '').toLowerCase();
  if (/(^|\s)(cc-|current-password|new-password|one-time-code)/.test(ac)) return true;
  const hint = [e.name, e.id, e.getAttribute && e.getAttribute('aria-label'), e.placeholder]
    .filter(Boolean).join(' ').toLowerCase();
  return /(passw|pwd|密码|口令|card.?(num|no)|cc.?num|cvv|cvc|security.?code|卡号|安全码|验证码|otp|\bpin\b)/.test(hint);
}"""

# 要点 / 输入的元素是什么（6.2b，给权限规则用，见 permissions.ElementFacts）。搜索框、搜索表单单独认出来：搜索不算提交
FACTS_JS = r"""e => {
  const isSensitive = """ + SENSITIVE_JS + r""";
  const tag = e.tagName.toLowerCase();
  const type = (e.type || '').toLowerCase();
  const label = [e.innerText, typeof e.value === 'string' && type !== 'password' ? e.value : '',
                 e.getAttribute('aria-label'), e.getAttribute('title'), e.getAttribute('alt')]
    .filter(x => typeof x === 'string' && x.trim()).map(x => x.trim()).join(' ').slice(0, 200);
  const form = e.form || e.closest('form');
  const submit = !!form && ((tag === 'button' && type === 'submit') || (tag === 'input' && (type === 'submit' || type === 'image')));
  const link = e.closest('a[href]');
  const download = !!link && (link.hasAttribute('download')
    || /\.(zip|rar|7z|tar|gz|exe|msi|dmg|apk|iso|bin|torrent)(\?|#|$)/i.test(link.href));
  const hint = s => /search|搜索|搜尋|查询|查找|検索/i.test(typeof s === 'string' ? s : '');
  const search = type === 'search' || e.getAttribute('role') === 'searchbox'
    || /^(q|query|wd|kw|keyword|keywords|search|s)$/i.test(e.name || '') || hint(e.placeholder) || hint(e.getAttribute('aria-label'))
    || (!!form && (form.getAttribute('role') === 'search' || !!form.querySelector('input[type=search]')
                   || hint(form.getAttribute('action')) || hint(form.id) || hint(form.getAttribute('class'))))
    || (submit && hint(label));
  return {label, submit, download, search, sensitive: isSensitive(e)};
}"""

# 快照里带值的一行：  - textbox "名字" [ref=e3]: 张三    （名字在 [ref=…] 前面，所以名字里有冒号也不怕）
_VALUE_LINE = re.compile(r"^(?P<head>\s*- .*?\[ref=(?P<ref>[A-Za-z0-9]+)\][^\n:]*): (?P<value>.+)$", re.M)


AFTER_ACTION_S = 0.3         # 点击、输入之后先等一下，让网页的脚本把变化画出来
_REF = re.compile(r"^[A-Za-z0-9]{1,20}$")   # 元素编号只能是 e5、f1e2 这样（不让拼出别的选择器）


class BrowserError(Exception):
    """浏览器没做成：message 是给模型看的英文。"""


@dataclass(frozen=True)
class PageView:
    """做完一件事以后的网页：网址、标题、快照（敏感输入框的值已经遮掉）；note：给模型的说明（"打开了新标签页"）。"""
    url: str
    title: str
    snapshot: str
    note: str = ""


def start_playwright():
    """启动 Playwright 的驱动（测试里换成假的，见 conftest：绝不真的开浏览器）。"""
    from playwright.async_api import async_playwright
    return async_playwright().start()


def valued_refs(snapshot):
    """快照里带值的那些元素的编号（输入框、下拉框……）。"""
    return [m.group("ref") for m in _VALUE_LINE.finditer(snapshot)]


def mask_values(snapshot, refs):
    """把这些编号的元素的值换成 (hidden)。"""
    refs = set(refs)
    return _VALUE_LINE.sub(lambda m: f"{m.group('head')}: {HIDDEN}" if m.group("ref") in refs else m.group(0),
                           snapshot)


# 网址里像密码、卡号、令牌的参数（GET 提交的表单会把密码放进网址；登录回调带着 code / token）：值不给模型看
_SECRET_PARAM = re.compile(r"pass|pwd|^pw$|secret|token|card|^cc|cvv|cvc|otp|^pin$|^code$|auth|session|^sid$"
                           r"|^key$|api.?key|signature|^sig$", re.I)


def clean_url(url):
    """交给模型的网址：参数名像密码 / 卡号 / 令牌的，值换成 (hidden)。"""
    url = str(url or "")
    cuts = [i for i in (url.find("?"), url.find("#")) if i >= 0]
    if not cuts:
        return url

    def hide(match):
        name = unquote_plus(match.group(2))
        return f"{match.group(1)}{match.group(2)}={HIDDEN}" if _SECRET_PARAM.search(name) else match.group(0)
    return url[:min(cuts)] + re.sub(r"([?&#;])([^=&#;]*)=([^&#;]*)", hide, url[min(cuts):])


def describe(snapshot, ref):
    """快照里这个编号的那一行，去掉编号这些标记：button "提交订单"、textbox "搜索"。找不到是 ""。"""
    for line in snapshot.splitlines():
        if f"[ref={ref}]" in line:
            head = line.strip().lstrip("- ").split(f"[ref={ref}]")[0]
            return re.sub(r"\s*\[[^\]]*\]", "", head).strip()[:120]
    return ""


def _friendly(error):
    """Playwright 的报错 → 给模型看的一句话（只取第一行，去掉调用日志）。"""
    text = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return text[:300]


class Browser:
    def __init__(self, profile_dir=None, headless=False, on_change=None):
        """profile_dir：配置文件夹（默认 PROFILE_DIR）；on_change(open)：浏览器开了 / 关了（告诉芸芸：浏览器开着时
        不闲置释放）；headless：不显示窗口（剧本里用）。"""
        self.profile_dir = profile_dir or PROFILE_DIR
        self.headless = headless
        self.on_change = on_change
        self._lock = asyncio.Lock()   # 一次只做一件事（模型可能同时调用几个工具）
        self._playwright = None
        self._context = None
        self._page = None
        self._last = (None, "")       # 最近一次快照：(哪个标签页, 快照)。确认卡片上写元素是什么时用

    @property
    def is_open(self):
        return self._context is not None

    # ---------- 开、关 ----------

    async def _ensure(self):
        if self._context is not None:
            return
        try:
            self._playwright = await start_playwright()
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            context = await self._playwright.chromium.launch_persistent_context(
                str(self.profile_dir), channel="msedge", headless=self.headless,
                no_viewport=True,          # 网页跟着窗口大小走（不然窗口里是一块固定大小的画面）
                accept_downloads=False,    # 下载等 6.2c（每次问你）
            )
        except Exception as e:
            log.warning("浏览器没打开：%s", _friendly(e))
            await self._stop_playwright()
            text = str(e)
            if "msedge" in text and ("not found" in text or "doesn't exist" in text):
                raise BrowserError("Microsoft Edge was not found on this computer.") from e
            if "user data directory is already in use" in text.lower():
                raise BrowserError("Yunyun's browser is already open in another window.") from e
            raise BrowserError(f"Could not start the browser: {_friendly(e)}") from e
        context.set_default_timeout(ACTION_TIMEOUT_MS)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        context.on("close", lambda _: self._forget(context))
        self._context = context
        self._page = context.pages[0] if context.pages else await context.new_page()
        log.info("浏览器打开了")
        self._changed(True)

    def _forget(self, context):
        """浏览器关了（你关掉了窗口，或者驱动没了）：下次用时重新打开。"""
        if context is not self._context:
            return
        self._context = self._page = None
        log.info("浏览器关了")
        self._changed(False)
        playwright, self._playwright = self._playwright, None   # 先拿走：下次打开时会换一个新的，别关错了
        asyncio.get_running_loop().create_task(_stop(playwright))

    def _changed(self, is_open):
        if self.on_change is not None:
            try:
                self.on_change(is_open)
            except Exception:
                log.exception("通知浏览器开关时出错")

    async def _stop_playwright(self):
        playwright, self._playwright = self._playwright, None
        await _stop(playwright)

    async def close(self):
        """关掉浏览器（助手关掉时）。好好关，下次打开 Edge 不会说"没有正常关闭"。
        不等正在做的事（不拿锁）：浏览器一关，它自己就出错结束了。"""
        context, self._context, self._page = self._context, None, None
        if context is not None:
            try:
                await asyncio.wait_for(context.close(), 10)
            except Exception as e:
                log.info("关浏览器时出错（不要紧）：%s", _friendly(e))
            self._changed(False)
        await self._stop_playwright()

    async def _page_now(self):
        """现在用哪个标签页：你在窗口里切到了别的标签页就跟过去（看得见的那个）；原来那个关了就用最后一个。"""
        await self._ensure()
        pages = [p for p in self._context.pages if not p.is_closed()]
        if not pages:
            self._page = await self._context.new_page()
            return self._page
        if len(pages) > 1:
            visible = [p for p in pages if await _is_visible(p)]
            if visible and self._page not in visible:
                self._page = visible[-1]
        if self._page is None or self._page.is_closed():
            self._page = pages[-1]
        return self._page

    async def _run(self, action, retry=True):
        """做一件事。浏览器半路没了（窗口被关、驱动被结束）：retry 就重新打开再做一次（只给打开、读、后退用——
        点击、输入不重做，免得做两遍）。"""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    return await action(await self._page_now())
                except BrowserError:
                    raise
                except Exception as e:
                    if await self._alive():   # 浏览器好好的：就是这件事没做成
                        raise BrowserError(_friendly(e)) from e
                    if self._context is not None:   # 还没收到 close 事件（_forget）：自己收拾
                        log.info("浏览器没了")
                        self._context = self._page = None
                        self._changed(False)
                        await self._stop_playwright()
                    if not (retry and attempt == 1):
                        raise BrowserError("The browser was closed. Open the page again with browser_open.") from e

    async def _alive(self):
        if self._context is None:
            return False
        try:
            await asyncio.wait_for(self._context.cookies("https://example.invalid"), 3)
            return True
        except Exception:
            return False

    # ---------- 看网页 ----------

    async def open(self, url):
        """在现在的标签页打开 url（只接受 http / https，见 permissions.browser_url）→ 快照。"""
        async def go(page):
            await page.bring_to_front()
            await page.goto(url, wait_until="domcontentloaded")
            await _settle(page)
            return await self._snapshot(page)
        return await self._run(go)

    async def read(self):
        """现在这一页的快照（页面变了以后重新看）。"""
        async def look(page):
            return await self._snapshot(page)
        return await self._run(look)

    async def back(self):
        async def go_back(page):
            moved = await page.go_back(wait_until="domcontentloaded")
            if moved is None:
                raise BrowserError("There is no previous page.")
            await _settle(page)
            return await self._snapshot(page)
        return await self._run(go_back)

    # ---------- 点击、输入、选择（6.2b：权限规则先问过你了） ----------

    async def element_facts(self, ref):
        """编号 ref 的元素是什么（permissions.ElementFacts）：权限规则据此决定问不问、怎么问。
        浏览器没开就不开（problem 里说一声）；编号不对 / 过期了也在 problem 里说。"""
        if not _REF.match(str(ref or "")):
            return ElementFacts(problem="Give the element's ref from the latest snapshot, like e5.")
        if not self.is_open:
            return ElementFacts(problem="Yunyun's browser isn't open. Open a page with browser_open first.")
        async with self._lock:
            try:
                page = await self._page_now()
                facts = await page.locator(f"aria-ref={ref}").evaluate(FACTS_JS, timeout=3_000)
            except Exception as e:
                return ElementFacts(problem=f"No element [ref={ref}] on the current page ({_friendly(e)}). "
                                            "Read the page again with browser_read to get fresh refs.")
            snapped_page, snapshot = self._last
            element = describe(snapshot, ref) if snapped_page is page else ""
            label = str(facts.get("label") or "")
            return ElementFacts(site=site_of(page.url), element=element or label[:60], label=label,
                                submit=bool(facts.get("submit")), download=bool(facts.get("download")),
                                search=bool(facts.get("search")), sensitive=bool(facts.get("sensitive")))

    async def click(self, ref):
        async def do(page):
            before = set(self._context.pages)
            await page.locator(_ref_selector(ref)).click()
            return await self._after_action(page, before)
        return await self._run(do, retry=False)

    async def type(self, ref, text, submit=False):
        """把输入框的内容换成 text；submit：再按一下回车。密码、卡号这类再查一遍，一律不填。"""
        async def do(page):
            box = page.locator(_ref_selector(ref))
            if await box.evaluate(SENSITIVE_JS):
                raise BrowserError("This looks like a password, payment or verification-code field; "
                                   "ask the user to type it themselves.")
            before = set(self._context.pages)
            await box.fill(str(text))
            if submit:
                await box.press("Enter")
            return await self._after_action(page, before)
        return await self._run(do, retry=False)

    async def select(self, ref, values):
        """下拉框选上 values（按选项的值或显示的字都行）。"""
        async def do(page):
            before = set(self._context.pages)
            await page.locator(_ref_selector(ref)).select_option([str(v) for v in values])
            return await self._after_action(page, before)
        return await self._run(do, retry=False)

    async def _after_action(self, page, before):
        """点击 / 输入之后：等网页变好；打开了新标签页就跟过去（以后读的都是它）。"""
        await asyncio.sleep(AFTER_ACTION_S)
        await _settle(page)
        opened = [p for p in self._context.pages if p not in before and not p.is_closed()]
        if opened:
            self._page = opened[-1]
            await _settle(self._page)
            view = await self._snapshot(self._page)
            return PageView(view.url, view.title, view.snapshot, "It opened in a new tab; you are now on that tab.")
        if page.is_closed():   # 点了以后这一页自己关了（有些"关闭"按钮）
            return await self._snapshot(await self._page_now())
        return await self._snapshot(page)

    async def _snapshot(self, page):
        """PageView：敏感输入框的值换成 (hidden)；看不清是不是敏感的，也当敏感。"""
        text = await page.aria_snapshot(mode="ai", timeout=ACTION_TIMEOUT_MS)
        hidden = []
        for ref in valued_refs(text):
            try:
                if await page.locator(f"aria-ref={ref}").evaluate(SENSITIVE_JS, timeout=2_000):
                    hidden.append(ref)
            except Exception:
                hidden.append(ref)
        if hidden:
            log.info("快照里遮掉了 %d 个输入框的值", len(hidden))
        text = mask_values(text, hidden)
        self._last = (page, text)
        return PageView(clean_url(page.url), await page.title(), text)


def _ref_selector(ref):
    if not _REF.match(str(ref or "")):
        raise BrowserError("Give the element's ref from the latest snapshot, like e5.")
    return f"aria-ref={ref}"


async def _is_visible(page):
    """这个标签页是不是窗口里正显示着的（只有正显示的那个是 visible）。"""
    try:
        return await asyncio.wait_for(page.evaluate("document.visibilityState"), 1) == "visible"
    except Exception:
        return False


async def _stop(playwright):
    if playwright is not None:
        try:
            await playwright.stop()
        except Exception as e:   # 驱动已经没了
            log.info("关 Playwright 时出错（不要紧）：%s", _friendly(e))


async def _settle(page):
    """打开后等页面差不多稳定（load + 网络安静一会儿），最多 SETTLE_MS；等不到就算了。"""
    for state in ("load", "networkidle"):
        try:
            await page.wait_for_load_state(state, timeout=SETTLE_MS)
        except Exception:
            return
