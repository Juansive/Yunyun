"""操作桌面软件（阶段 11.6b）：用 Windows 自带的 UI 自动化（UIAutomationCore，comtypes 调）读窗口里的按钮、输入框，
按编号点击、输入、按键。只在助手子进程里用（tools/desktop.py）；芸芸本体不 import 它。

- 读：一次跨进程拿整个窗口（CacheRequest + 子树，11.6a 实测几十到一两百毫秒），交给模型的是一行一个控件，
  能操作的带编号 [ref=u5]（像浏览器的快照）；没名字的框框不列（里面的往上提一层）；屏幕外的只报个数
  （Win11 记事本会恢复上次的几十个标签页）；密码框的值不读。
- 点：用控件自己的"按一下"（Invoke / Toggle / 选中 / 展开），不动鼠标；都没有就说做不到（用鼠标点在 11.6c）。
- 输入：输入框支持就直接把字放进去（替换原来的）；不支持的先把焦点放进去，再一个字一个字地发——换行用 Shift+回车，
  **不按真的回车**（2026-10-15 定的：免得在聊天软件里打着打着就发出去了）。
- 按键：发给最后读的那个窗口。先请芸芸本体把它切到前面（只有前台程序能切窗口，tools/desktop.py 走 Bridge），
  发之前再看一眼它是不是真的在最前面——不是就不发（不然按键会落到你正在用的别的窗口里）。

UI 自动化的调用都在一条专门的后台线程里（COM 要多线程模式；读到卡住的软件最多 2 秒就超时，不会卡住助手）。
真的去碰 Windows 的只有 _Backend：测试里换成假的（conftest），绝不动你真的窗口。日志不写窗口标题和控件上的字。
"""
import asyncio
import ctypes
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from dataclasses import dataclass, field, replace

from yunyun.assistant.permissions import KEYS, MODIFIERS, ElementFacts, classify_app, parse_keys
from yunyun.assistant.protocol import UI_KEY_TOOL, UI_READ_TOOL

log = logging.getLogger(__name__)

SETTLE_S = 0.5          # 点 / 输入 / 按键之后等多久再读（让软件把界面改好）
NAME_CHARS = 120        # 控件的名字、值太长就截短（多半是一大段内容）
VALUE_CHARS = 300
CONTEXT_CHARS = 400     # 窗口里的说明文字留多少（点"确定 / 是"时看是不是撤不回的事，permissions.is_risky_ui）
MAX_NODES = 3000        # 一个窗口最多读这么多个控件（再多的多半是超长列表）
MAX_DEPTH = 60
HIDDEN = "(hidden)"
INPUT_TAG = 0x59554E59  # 芸芸发的按键都带这个记号（"YUNY"）：11.6c 分辨鼠标键盘是芸芸动的还是你动的


class DesktopError(Exception):
    """没做成：说明是给模型看的英文。"""


@dataclass
class Window:
    hwnd: int
    title: str
    app: str                # 程序文件名；商店应用是里面真正的程序（CalculatorApp.exe），不是外框的 ApplicationFrameHost.exe
    pid: int
    window_class: str = ""


@dataclass
class Node:
    """窗口里的一个控件（_Backend 读出来的）。"""
    role: str               # UI 自动化的控件种类，小写："button" / "edit" / "text" / "listitem" ……
    name: str = ""
    depth: int = 0          # 在窗口里第几层（窗口本身是 0）
    actions: tuple = ()     # 能怎么操作："invoke" / "toggle" / "select" / "expand" / "value"（能直接放字进去）/
                            # "focus"（不能直接放字、但能把焦点放进去一个个打的编辑框，比如自己画的编辑器）
    enabled: bool = True
    offscreen: bool = False
    password: bool = False
    value: str = ""         # 输入框里的字（密码框一律是空的）
    state: str = ""         # "checked" / "unchecked" / "expanded" / "collapsed" / "selected"
    handle: object = field(default=None, repr=False, compare=False)   # 真的控件（_Backend 自己用）


@dataclass
class UiView:
    """给模型看的一个窗口。"""
    window: str             # "w1234"
    app: str
    title: str
    snapshot: str
    note: str = ""


def window_id(hwnd):
    return f"w{hwnd}"


def parse_window_id(text):
    text = str(text or "").strip()
    return int(text[1:]) if text[:1] == "w" and text[1:].isdigit() else None


# ---------- 读出来的控件 → 给模型看的几行（纯函数） ----------

SKIPPED_ROLES = {"scrollbar", "thumb", "titlebar", "separator"}   # 连同里面的都不列（滚动用按键）


def _short(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def describe(node, ref=""):
    """一行：- button "保存" [ref=u3]、- edit "搜索" [ref=u5] value="芸芸"、- checkbox "自动换行" [ref=u7] (checked)。"""
    text = f"- {node.role}"
    if node.name.strip():
        text += f' "{_short(node.name, NAME_CHARS)}"'
    if ref:
        text += f" [ref={ref}]"
    if node.password:
        text += f" value={HIDDEN}"
    elif node.value.strip() and node.value.strip() != node.name.strip():
        text += f' value="{_short(node.value, VALUE_CHARS)}"'
    if node.state:
        text += f" ({node.state})"
    if not node.enabled:
        text += " (disabled)"
    return text


def render(nodes):
    """窗口里的控件（不含窗口本身）→ (几行字, {编号: 控件}, 屏幕外有几个)。
    没名字、不能操作的框框不列，里面的往上提一层；屏幕外的和滚动条连同里面的都不列。"""
    lines, refs, offscreen = [], {}, 0
    stack, skip_below = [], None   # stack：祖先 (层数, 列出来了没有)
    for node in nodes:
        if skip_below is not None:
            if node.depth > skip_below:
                continue
            skip_below = None
        if node.offscreen or node.role in SKIPPED_ROLES:
            offscreen += node.offscreen
            skip_below = node.depth
            continue
        while stack and stack[-1][0] >= node.depth:
            stack.pop()
        actions = node.actions if node.enabled else ()
        shown = bool(node.name.strip() or actions or node.value.strip())
        if shown:
            ref = f"u{len(refs) + 1}" if actions else ""
            if ref:
                refs[ref] = node
            lines.append("  " * sum(1 for _, s in stack if s) + describe(node, ref))
        stack.append((node.depth, shown))
    return "\n".join(lines), refs, offscreen


def context_text(nodes):
    """窗口里的说明文字（看得见的 text 控件），给"确定 / 是"判断撤不回用。"""
    return _short(" ".join(n.name for n in nodes if n.role == "text" and not n.offscreen and n.name.strip()),
                  CONTEXT_CHARS)


# ---------- 给助手用的（asyncio；UI 自动化在专门的线程里） ----------

class Desktop:
    def __init__(self, own_pids=None):
        """own_pids：芸芸自己的进程（本体 + 助手子进程），它们的窗口一律不碰。"""
        self.own_pids = set(own_pids) if own_pids is not None else {os.getpid(), os.getppid()}
        self._executor = None
        self._backend = None
        self._window = None     # 最后读的窗口（Window）
        self._refs = {}         # 那次读出来的编号 → Node
        self._context = ""

    async def _run(self, func):
        """在 UI 自动化的线程里做 func(backend)。"""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(1, thread_name_prefix="ui-automation")
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._in_thread, func)

    def _in_thread(self, func):
        try:
            if self._backend is None:
                self._backend = _Backend()
            return func(self._backend)
        except DesktopError:
            raise
        except Exception as e:   # COM 的错（超时、控件没了、权限不够）→ 给模型看的说明
            log.info("UI 自动化出错：%s", type(e).__name__)
            raise DesktopError(explain(e)) from e

    def close(self):
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    # ---- 哪个窗口 ----

    def _rule(self, window):
        """(一律不碰的原因, 是不是聊天软件)。"""
        if window.pid in self.own_pids:
            return "own", False
        kind = classify_app(window.app, window.title, window.window_class)
        return ("" if kind == "chat" else kind), kind == "chat"

    async def _target(self, wanted=""):
        """wanted：list_windows 给的编号；空 = 最后读的那个（还在的话），再不然是你最近用的窗口（芸芸的不算）。"""
        windows = await self._run(lambda b: b.windows())
        if wanted:
            hwnd = parse_window_id(wanted)
            found = next((w for w in windows if w.hwnd == hwnd), None)
            if found is None:
                raise DesktopError(f"There is no window {wanted!r} (it may have been closed). Use list_windows to see "
                                   "the open windows.")
            return found
        if self._window is not None:
            found = next((w for w in windows if w.hwnd == self._window.hwnd), None)
            if found is not None:
                return found
        found = next((w for w in windows if w.pid not in self.own_pids), None)
        if found is None:
            raise DesktopError("There is no window to read.")
        return found

    async def _current(self):
        """最后读的窗口（还开着的话）。"""
        if self._window is None:
            raise DesktopError("Read a window with ui_read first.")
        windows = await self._run(lambda b: b.windows())
        found = next((w for w in windows if w.hwnd == self._window.hwnd), None)
        if found is None:
            self._window, self._refs = None, {}
            raise DesktopError("The window you read last has been closed. Use list_windows and ui_read again.")
        return found

    def _node(self, ref):
        node = self._refs.get(str(ref or "").strip())
        if node is None:
            raise DesktopError(f"There is no element {ref!r} in the window you read last. Read it again with "
                               "ui_read (refs change every time).")
        return node

    # ---- 权限规则要看的（permissions.decide 的 element） ----

    async def facts(self, tool, tool_input):
        """这次要读 / 点 / 输入 / 按键的窗口和控件是什么（芸芸自己看的，不信模型的说法）。"""
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        try:
            if tool == UI_READ_TOOL:
                window = await self._target(str(tool_input.get("window") or ""))
            else:
                window = await self._current()
                ref = str(tool_input.get("ref") or "")
                node = self._node(ref) if ref or tool != UI_KEY_TOOL else None
            forbidden, chat = self._rule(window)
            facts = ElementFacts(site=window.app, forbidden=forbidden, chat=chat)
            if tool != UI_READ_TOOL and node is not None:
                facts = replace(facts, element=describe(node), label=node.name, sensitive=node.password,
                                context=self._context)
            return facts
        except DesktopError as e:
            return ElementFacts(problem=str(e))

    # ---- 读、点、输入、按键 ----

    async def read(self, wanted=""):
        return await self._read(await self._target(wanted))

    async def _read(self, window, note=""):
        nodes = await self._run(lambda b: b.read(window.hwnd))
        text, refs, offscreen = render(nodes[1:])
        self._window, self._refs, self._context = window, refs, context_text(nodes)
        notes = [note] if note else []
        if offscreen:
            notes.append(f"({offscreen} more items are off screen; scroll to see them.)")
        if not refs:
            notes.append("(Nothing here can be operated through UI Automation: this app may draw its own interface. "
                         "Take a screenshot to see it.)")
        log.info("读窗口：%s，%d 个控件、%d 个能操作", window.app or "?", len(nodes), len(refs))
        return UiView(window_id(window.hwnd), window.app, window.title, text, "\n".join(notes))

    async def click(self, ref):
        window = await self._current()
        node = self._node(ref)
        before = await self._hwnds()
        how = await self._run(lambda b: b.press(node))
        return await self._after(window, before, f"Pressed {ref} ({how}).")

    async def type(self, ref, text, bring_to_front):
        """bring_to_front(window)：请芸芸本体把窗口切到前面（不能直接放字时，要一个字一个字地发）。"""
        window = await self._current()
        node = self._node(ref)
        if node.password:   # 权限规则已经挡过，这里兜底
            raise DesktopError("That is a password field; Yunyun never types into it.")
        before = await self._hwnds()
        if "value" in node.actions:
            await self._run(lambda b: b.set_value(node, text))
            how = f"Put the text into {ref} (replacing what was there)."
        else:
            await self._to_front(window, bring_to_front)
            await self._run(lambda b: b.focus(node))
            await self._run(lambda b: b.send_text(text))
            how = f"Typed the text into {ref} at the cursor."
        return await self._after(window, before, how)

    async def key(self, keys, ref, bring_to_front):
        parsed = parse_keys(keys)
        if parsed is None:
            raise DesktopError(f"Unknown keys {keys!r}.")
        window = await self._current()
        node = self._node(ref) if ref else None
        before = await self._hwnds()
        await self._to_front(window, bring_to_front)
        if node is not None:
            await self._run(lambda b: b.focus(node))
        await self._run(lambda b: b.send_keys(*parsed))
        return await self._after(window, before, f"Pressed {keys}.")

    async def _to_front(self, window, bring_to_front):
        await bring_to_front(window)
        front = await self._run(lambda b: b.foreground())
        if front != window.pid:   # 你在用别的窗口：不发，不然按键会落到那里
            raise DesktopError("The window isn't in front (the user may be using another window), so nothing was "
                               "typed or pressed. Ask the user, or try again.")

    async def _hwnds(self):
        return {w.hwnd for w in await self._run(lambda b: b.windows())}

    async def _after(self, window, before, done):
        """做完以后：等一下，看有没有新开的窗口（对话框），再把这个窗口读一遍交回去。"""
        await asyncio.sleep(SETTLE_S)
        windows = await self._run(lambda b: b.windows())
        notes = [done] + [f"A new window opened: {window_id(w.hwnd)} · {w.app or '?'} · {w.title} (read it with "
                          f"ui_read window={window_id(w.hwnd)})."
                          for w in windows if w.hwnd not in before and w.pid not in self.own_pids]
        now = next((w for w in windows if w.hwnd == window.hwnd), None)
        if now is None:
            self._window, self._refs = None, {}
            return UiView(window_id(window.hwnd), window.app, window.title, "",
                          "\n".join(notes + ["That window is closed now."]))
        return await self._read(now, "\n".join(notes))


# ---------- COM 的错 → 给模型看的说明 ----------

_HRESULTS = {
    0x800705B4: "The app is not responding (timed out).",                                   # ERROR_TIMEOUT
    0x80040201: "That element is gone (the window changed). Read the window again with ui_read.",   # 控件没了
    0x80040200: "That element is disabled right now.",                                     # UIA_E_ELEMENTNOTENABLED
    0x80070005: "Windows doesn't let Yunyun operate this app (it probably runs as administrator).",  # E_ACCESSDENIED
    0x80131509: "That element can't do this right now.",                                   # InvalidOperation
}


def explain(error):
    code = getattr(error, "hresult", None)
    if code is None and getattr(error, "args", None) and isinstance(error.args[0], int):
        code = error.args[0]
    if isinstance(code, int):
        code &= 0xFFFFFFFF
        return _HRESULTS.get(code, f"UI Automation error 0x{code:08X}.")
    return f"UI Automation failed ({type(error).__name__})."


# ---------- 真的去碰 Windows 的地方（只在 UI 自动化那条线程里用；测试里换成假的） ----------

if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _MOUSEINPUT(ctypes.Structure):   # 不用，但 INPUT 的大小要按它算（三种里它最大）
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    for _func, _restype, _argtypes in (
        (user32.SendInput, wintypes.UINT, [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]),
        (user32.GetClassNameW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]),
        (user32.FindWindowExW, wintypes.HWND, [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]),
        (user32.SetThreadDpiAwarenessContext, ctypes.c_void_p, [ctypes.c_void_p]),
    ):
        _func.restype, _func.argtypes = _restype, _argtypes

INPUT_KEYBOARD, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x1, 0x2, 0x4
TYPING_ROLES = {"edit", "document", "custom"}   # 没有 Value 时，能拿到焦点的这几种也给编号（一个个打字）
EXTENDED_KEYS = {"up", "down", "left", "right", "home", "end", "insert", "delete", "pageup", "pagedown", "menu"}
FRAME_HOST = "applicationframehost.exe"   # 商店应用的外框：里面真正的程序在 CoreWindow 子窗口里


def _key_input(vk=0, scan=0, flags=0):
    item = _INPUT(type=INPUT_KEYBOARD)
    item.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=INPUT_TAG)
    return item


def _send(items):
    array = (_INPUT * len(items))(*items)
    if user32.SendInput(len(items), array, ctypes.sizeof(_INPUT)) != len(items):
        raise DesktopError("Windows blocked the key presses (the app may run as administrator).")


def _class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


class _Backend:
    """UI 自动化 + 按键。在 UI 自动化那条线程里创建和使用（COM 多线程模式）。"""

    def __init__(self):
        sys.coinit_flags = 0   # comtypes import 时就初始化 COM：要多线程模式（单线程模式会卡在界面消息上）
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:   # 这条线程已经是别的模式了：照样能用，记一笔
            log.info("这条线程的 COM 已经初始化过了")
        try:   # 打包时带着生成好的模块（tools/build_exe.py 的 HIDDEN_IMPORTS）；开发时没生成过就现生成
            from comtypes.gen import UIAutomationClient as uia
        except ImportError:
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # 坐标用真实像素（和截图一致，150% 缩放下也对）
        self.uia = uia
        self.automation = comtypes.client.CreateObject(uia.CUIAutomation8, interface=uia.IUIAutomation2)
        self.automation.ConnectionTimeout, self.automation.TransactionTimeout = 2000, 5000
        self.roles = {getattr(uia, n): n[4:-13].lower() for n in dir(uia)
                      if n.startswith("UIA_") and n.endswith("ControlTypeId")}
        self.patterns = (("invoke", uia.UIA_IsInvokePatternAvailablePropertyId),
                         ("toggle", uia.UIA_IsTogglePatternAvailablePropertyId),
                         ("select", uia.UIA_IsSelectionItemPatternAvailablePropertyId),
                         ("expand", uia.UIA_IsExpandCollapsePatternAvailablePropertyId),
                         ("value", uia.UIA_IsValuePatternAvailablePropertyId))
        self.cache = self.automation.CreateCacheRequest()
        for prop in (uia.UIA_ControlTypePropertyId, uia.UIA_NamePropertyId, uia.UIA_IsEnabledPropertyId,
                     uia.UIA_IsOffscreenPropertyId, uia.UIA_IsPasswordPropertyId, uia.UIA_ValueValuePropertyId,
                     uia.UIA_IsKeyboardFocusablePropertyId,
                     uia.UIA_ValueIsReadOnlyPropertyId, uia.UIA_ToggleToggleStatePropertyId,
                     uia.UIA_ExpandCollapseExpandCollapseStatePropertyId, uia.UIA_SelectionItemIsSelectedPropertyId,
                     *(prop for _, prop in self.patterns)):
            self.cache.AddProperty(prop)
        self.cache.TreeScope = uia.TreeScope_Subtree
        self.cache.TreeFilter = self.automation.ControlViewCondition

    # ---- 窗口 ----

    def windows(self):
        """任务栏上会有的那些窗口，按叠放顺序从上到下。"""
        from yunyun.pet import winapi
        found = []
        for hwnd in winapi.top_level_windows():
            if winapi.is_normal_window(hwnd):
                pid = winapi.process_id(hwnd)
                app = winapi.process_name(pid)
                if app.casefold() == FRAME_HOST:   # 商店应用：认里面真正的程序
                    inner = user32.FindWindowExW(hwnd, None, "Windows.UI.Core.CoreWindow", None)
                    if inner:
                        pid = winapi.process_id(inner)
                        app = winapi.process_name(pid) or app
                found.append(Window(int(hwnd), winapi.window_title(hwnd), app, pid, _class_name(hwnd)))
        return found

    def foreground(self):
        """最前面那个窗口属于哪个进程（商店应用认里面真正的程序）。"""
        from yunyun.pet import winapi
        hwnd = winapi.foreground_window()
        found = next((w for w in self.windows() if w.hwnd == hwnd), None)
        return found.pid if found else winapi.process_id(hwnd)

    # ---- 读 ----

    def read(self, hwnd):
        root = self.automation.ElementFromHandleBuildCache(hwnd, self.cache)
        nodes = []

        def walk(element, depth):
            if len(nodes) >= MAX_NODES or depth > MAX_DEPTH:
                return
            nodes.append(self._node(element, depth))
            children = element.GetCachedChildren()
            for i in range(children.Length if children else 0):
                walk(children.GetElement(i), depth + 1)

        walk(root, 0)
        return nodes

    def _node(self, element, depth):
        uia, get = self.uia, element.GetCachedPropertyValue
        actions = [name for name, prop in self.patterns if get(prop) is True]
        if "value" in actions and get(uia.UIA_ValueIsReadOnlyPropertyId) is True:
            actions.remove("value")
        role = self.roles.get(get(uia.UIA_ControlTypePropertyId), "custom")
        if not actions and role in TYPING_ROLES and get(uia.UIA_IsKeyboardFocusablePropertyId) is True:
            actions.append("focus")
        password = get(uia.UIA_IsPasswordPropertyId) is True
        value = get(uia.UIA_ValueValuePropertyId)
        state = ""
        if "toggle" in actions:
            state = {0: "unchecked", 1: "checked", 2: "mixed"}.get(get(uia.UIA_ToggleToggleStatePropertyId), "")
        elif "expand" in actions:
            state = {0: "collapsed", 1: "expanded", 2: "expanded"}.get(
                get(uia.UIA_ExpandCollapseExpandCollapseStatePropertyId), "")
        elif "select" in actions and get(uia.UIA_SelectionItemIsSelectedPropertyId) is True:
            state = "selected"
        name = get(uia.UIA_NamePropertyId)
        return Node(role, name if isinstance(name, str) else "", depth, tuple(actions),
                    get(uia.UIA_IsEnabledPropertyId) is not False, get(uia.UIA_IsOffscreenPropertyId) is True,
                    password, "" if password or not isinstance(value, str) else value, state, element)

    # ---- 点、输入、按键 ----

    def _pattern(self, node, pattern_id, interface):
        return node.handle.GetCurrentPattern(pattern_id).QueryInterface(interface)

    def press(self, node):
        uia = self.uia
        try:
            if "invoke" in node.actions:
                self._pattern(node, uia.UIA_InvokePatternId, uia.IUIAutomationInvokePattern).Invoke()
                return "invoke"
            if "toggle" in node.actions:
                self._pattern(node, uia.UIA_TogglePatternId, uia.IUIAutomationTogglePattern).Toggle()
                return "toggle"
            if "select" in node.actions:
                self._pattern(node, uia.UIA_SelectionItemPatternId, uia.IUIAutomationSelectionItemPattern).Select()
                return "select"
            if "expand" in node.actions:
                pattern = self._pattern(node, uia.UIA_ExpandCollapsePatternId, uia.IUIAutomationExpandCollapsePattern)
                pattern.Collapse() if node.state == "expanded" else pattern.Expand()
                return "collapse" if node.state == "expanded" else "expand"
            if "focus" in node.actions or "value" in node.actions:   # 编辑框：点一下 = 把光标放进去
                node.handle.SetFocus()
                return "focus"
        except Exception as e:
            # 按钮打开了一个对话框（"另存为"）时，Invoke 会一直等到对话框关掉才回来 → 超时。其实已经按下去了
            if explain(e) == _HRESULTS[0x800705B4]:
                return "pressed; the app is still busy, maybe a dialog opened"
            raise
        raise DesktopError("This element can't be pressed through UI Automation (Yunyun can't click with the mouse "
                           "yet). Try a keyboard shortcut with ui_key, or ask the user.")

    def set_value(self, node, text):
        self._pattern(node, self.uia.UIA_ValuePatternId, self.uia.IUIAutomationValuePattern).SetValue(text)

    def focus(self, node):
        node.handle.SetFocus()

    def send_keys(self, mods, key):
        down = [_key_input(MODIFIERS[m], flags=KEYEVENTF_EXTENDEDKEY if m == "win" else 0)
                for m in ("ctrl", "alt", "shift", "win") if m in mods]
        flag = KEYEVENTF_EXTENDEDKEY if key in EXTENDED_KEYS else 0
        press = [_key_input(KEYS[key], flags=flag), _key_input(KEYS[key], flags=flag | KEYEVENTF_KEYUP)]
        up = [_key_input(item.ki.wVk, flags=item.ki.dwFlags | KEYEVENTF_KEYUP) for item in reversed(down)]
        _send(down + press + up)

    def send_text(self, text):
        """一个字一个字地发（不经输入法）。换行 = Shift+回车（不按真的回车）。"""
        for i, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
            if i:
                self.send_keys(frozenset({"shift"}), "enter")
            units = line.encode("utf-16-le")   # 中文、表情都按 UTF-16 一个个码元发
            items = []
            for j in range(0, len(units), 2):
                unit = int.from_bytes(units[j:j + 2], "little")
                items += [_key_input(scan=unit, flags=KEYEVENTF_UNICODE),
                          _key_input(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
            if items:
                _send(items)
