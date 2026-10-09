"""芸芸自己的浏览器（阶段 6.2）：Python 版 Playwright + 系统自带的 Edge + 芸芸自己的配置文件夹，窗口看得见。

**只在助手子进程里用**；playwright 到第一次用浏览器时才 import（启动约 2 秒：驱动 1.6 秒 + Edge 0.3 秒）。
- 不碰你 Chrome / Edge 里的登录：配置文件夹是 data_dir()/assistant/browser，你在这个窗口里登录过的网站下次还在。
- 进程：助手子进程 → node.exe（Playwright 的驱动）→ msedge.exe。都在助手的作业对象里：关掉助手时跟着结束；
  点"停止"时 proctree 放过驱动这一支（不然浏览器会被一起关掉）。实测驱动 + Edge 约 400 MB 私有内存。
- 看网页用 Playwright 的 aria 快照（mode="ai"）：每个元素带编号 [ref=e5]，按编号能找到元素（框架里的也行，f1e2）。
  **快照里输入框的值是原样的（密码、卡号也是）**：交给模型之前把敏感输入框的值换成 (hidden)（mask_values）。
- 你自己关掉浏览器窗口也没关系：下次用时重新打开。
"""
import asyncio
import logging
import re

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

# 快照里带值的一行：  - textbox "名字" [ref=e3]: 张三    （名字在 [ref=…] 前面，所以名字里有冒号也不怕）
_VALUE_LINE = re.compile(r"^(?P<head>\s*- .*?\[ref=(?P<ref>[A-Za-z0-9]+)\][^\n:]*): (?P<value>.+)$", re.M)


class BrowserError(Exception):
    """浏览器没做成：message 是给模型看的英文。"""


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

    def _current(self):
        """现在用的标签页：原来那个关了就用最后一个。"""
        if self._page is not None and not self._page.is_closed():
            return self._page
        pages = [p for p in self._context.pages if not p.is_closed()] if self._context else []
        self._page = pages[-1] if pages else None
        return self._page

    async def _page_now(self):
        await self._ensure()
        page = self._current()
        if page is None:
            page = self._page = await self._context.new_page()
        return page

    async def _run(self, action):
        """做一件事；浏览器半路没了（窗口被关、驱动被结束）就重新打开再试一次。"""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    return await action(await self._page_now())
                except BrowserError:
                    raise
                except Exception as e:
                    closed = self._context is None or "closed" in str(e).lower()
                    if attempt == 1 and closed:
                        log.info("浏览器没了，重新打开")
                        self._context = self._page = None
                        await self._stop_playwright()
                        continue
                    raise BrowserError(_friendly(e)) from e

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

    async def _snapshot(self, page):
        """(网址, 标题, 快照)：敏感输入框的值换成 (hidden)；看不清是不是敏感的，也当敏感。"""
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
        return page.url, await page.title(), mask_values(text, hidden)


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
