"""阶段 11.6a 摸底：用 Windows 自带的 UI 自动化（UIAutomationCore，comtypes 调）读一个窗口里有什么。

    python tools/probe_uia.py --process notepad.exe          # 按进程名找窗口
    python tools/probe_uia.py --title 计算器 --dpi aware      # 按标题找；--dpi 看 150% 缩放下坐标对不对
    python tools/probe_uia.py --process WeChat.exe --counts   # 只数数（控件种类、有没有名字），不打印任何文字

默认只打印"界面外壳"（按钮、菜单这类）的名字；文字、输入框、列表项、文档这类可能是用户内容的，只打印"«N 字»"。
--show 才原样打印（只对自己开的测试窗口用）。不读输入框的值。
"""
import argparse
import ctypes
import os
import re
import sys
import time
from collections import Counter
from ctypes import wintypes

sys.coinit_flags = 0   # comtypes import 时就初始化 COM：UI 自动化的客户端要用多线程模式（MTA），单线程模式会卡在界面消息上
import comtypes  # noqa: E402
import comtypes.client  # noqa: E402

comtypes.client.GetModule("UIAutomationCore.dll")
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

CONTROL_TYPES = {getattr(UIA, n): n[4:-13] for n in dir(UIA) if n.startswith("UIA_") and n.endswith("ControlTypeId")}
# 名字多半是用户内容的控件：只打印字数
CONTENT_TYPES = {"Text", "Edit", "Document", "ListItem", "TabItem", "TreeItem", "DataItem", "Hyperlink", "Window",
                 "Pane", "Group", "Custom", "Image", "Table", "List", "Tree", "DataGrid", "TitleBar"}
PATTERNS = {"invoke": UIA.UIA_IsInvokePatternAvailablePropertyId, "value": UIA.UIA_IsValuePatternAvailablePropertyId,
            "toggle": UIA.UIA_IsTogglePatternAvailablePropertyId,
            "select": UIA.UIA_IsSelectionItemPatternAvailablePropertyId,
            "expand": UIA.UIA_IsExpandCollapsePatternAvailablePropertyId,
            "scroll": UIA.UIA_IsScrollPatternAvailablePropertyId}
PROPS = [UIA.UIA_ControlTypePropertyId, UIA.UIA_NamePropertyId, UIA.UIA_AutomationIdPropertyId,
         UIA.UIA_ClassNamePropertyId, UIA.UIA_IsEnabledPropertyId, UIA.UIA_IsOffscreenPropertyId,
         UIA.UIA_BoundingRectanglePropertyId, UIA.UIA_IsKeyboardFocusablePropertyId,
         UIA.UIA_IsPasswordPropertyId, UIA.UIA_FrameworkIdPropertyId, UIA.UIA_ProcessIdPropertyId,
         *PATTERNS.values()]


def process_name(pid):
    handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "?"
    try:
        buf, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
        return os.path.basename(buf.value) if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)) else "?"
    finally:
        kernel32.CloseHandle(handle)


def windows():
    found = []

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if buf.value:
                found.append((hwnd, buf.value, pid.value, process_name(pid.value)))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def window_rect(hwnd):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")


def label(type_name, name, show):
    if not name:
        return ""
    if show or type_name not in CONTENT_TYPES:
        # 按钮名字里也会有用户信息（系统设置左上角的账户按钮 = 名字 + 邮箱），邮箱一律打码
        return repr(EMAIL.sub("«邮箱»", name)[:40])
    return f"«{len(name)} 字»"


def walk(element, depth, out, max_depth):
    out.append((depth, element))
    if depth < max_depth:
        children = element.GetCachedChildren()
        if children:
            for i in range(children.Length):
                walk(children.GetElement(i), depth + 1, out, max_depth)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--process")
    parser.add_argument("--title")
    parser.add_argument("--dpi", choices=["aware", "unaware"], default="unaware")
    parser.add_argument("--depth", type=int, default=40)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--lines", type=int, default=60)
    parser.add_argument("--default-timeouts", action="store_true", help="不改 UI 自动化的超时（看卡住的软件会卡多久）")
    args = parser.parse_args()
    if args.dpi == "aware":
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))   # PER_MONITOR_AWARE_V2

    targets = [w for w in windows()
               if (not args.process or w[3].lower() == args.process.lower())
               and (not args.title or args.title in w[1])]
    if not targets:
        print("没找到窗口")
        return 1
    hwnd, title, pid, proc = targets[0]
    print(f"窗口：{proc} pid={pid} 标题{label('Window', title, args.show)} 窗口矩形={window_rect(hwnd)}")

    automation = comtypes.client.CreateObject(UIA.CUIAutomation8, interface=UIA.IUIAutomation2)
    print(f"超时（毫秒）：连接 {automation.ConnectionTimeout}、一次操作 {automation.TransactionTimeout}")
    if not args.default_timeouts:
        automation.ConnectionTimeout, automation.TransactionTimeout = 2000, 5000
    cache = automation.CreateCacheRequest()
    for prop in PROPS:
        cache.AddProperty(prop)
    cache.TreeScope = UIA.TreeScope_Subtree
    cache.TreeFilter = automation.ControlViewCondition

    started = time.perf_counter()
    root = automation.ElementFromHandleBuildCache(hwnd, cache)
    took = time.perf_counter() - started
    nodes = []
    walk(root, 0, nodes, args.depth)

    types, frameworks, procs = Counter(), Counter(), Counter()
    interactive = named = 0
    lines = []
    for depth, el in nodes:
        type_name = CONTROL_TYPES.get(el.CachedControlType, str(el.CachedControlType))
        types[type_name] += 1
        frameworks[el.GetCachedPropertyValue(UIA.UIA_FrameworkIdPropertyId) or "-"] += 1
        procs[process_name(el.GetCachedPropertyValue(UIA.UIA_ProcessIdPropertyId))] += 1
        patterns = [p for p, pid_ in PATTERNS.items() if el.GetCachedPropertyValue(pid_)]
        name = el.CachedName or ""
        acts = [p for p in patterns if p != "scroll"]
        if acts or type_name in ("Button", "Edit", "MenuItem", "CheckBox", "RadioButton", "ComboBox", "Hyperlink"):
            interactive += 1
            named += bool(name.strip())
        if len(lines) < args.lines and not args.counts:
            r = el.CachedBoundingRectangle
            flags = "".join(("" if el.CachedIsEnabled else " 灰", " 屏外" if el.CachedIsOffscreen else "",
                             " 密码" if el.CachedIsPassword else ""))
            lines.append(f"{'  ' * min(depth, 12)}{type_name} {label(type_name, name, args.show)} "
                         f"[{r.left},{r.top},{r.right},{r.bottom}] {','.join(patterns)}{flags}")
    print("\n".join(lines))
    print(f"\n读一次 {took * 1000:.0f} ms（一次跨进程拿全部），共 {len(nodes)} 个节点；能操作的 {interactive} 个，其中有名字的 {named} 个")
    print("控件：", dict(types.most_common(12)))
    print("界面框架：", dict(frameworks), "  属于进程：", dict(procs))
    r = root.CachedBoundingRectangle
    print(f"根节点矩形 [{r.left},{r.top},{r.right},{r.bottom}]（和上面的窗口矩形比，看缩放）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
