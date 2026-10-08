"""打开软件（阶段 6）：开始菜单里的软件列表、按名字找、打开。

两边都用（不 import Qt / SDK）：
- 助手子进程：find_app 工具按名字找（search），open_app 工具确认名字在列表里（find_exact），权限规则查名字（names）；
- 芸芸进程：真正打开（launch）。不在助手子进程里打开：它在作业对象里（proctree），从它那里开的软件会在
  "停止"、关掉助手时被一起结束。

列表来自 Windows 自带的 Get-StartApps（PowerShell）：开始菜单里的全部软件，包括计算器、记事本这类商店应用
（只扫开始菜单文件夹里的快捷方式找不到它们）。每一项是"名字 + AppID"，打开 = shell:AppsFolder\\<AppID>
（Windows "所有应用"文件夹里的一项，只有开始菜单里有的才打得开）。这台电脑上 275 个、约 1.2 秒，所以缓存起来。
"""
import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

CACHE_S = 600           # 列表缓存多久（装了新软件，最多这么久后能找到；按名字打开没找到时也会重新读一次）
LIST_TIMEOUT_S = 30
MAX_RESULTS = 10        # find_app 最多给模型几个
_PS_COMMAND = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
               "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")


@dataclass(frozen=True)
class App:
    name: str     # 开始菜单里显示的名字（"计算器"、"Visual Studio Code"）
    app_id: str   # Windows 的 AppID（商店应用 "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"，或者路径、网址……）


_lock = threading.Lock()
_cache = (0.0, ())   # (读到的时间, (App, …))


def _query_start_apps():
    """问 Windows 要开始菜单里的软件 → [{"Name": …, "AppID": …}]。读不到就抛 OSError。（测试里换成假的，见 conftest）"""
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_COMMAND],
                              capture_output=True, timeout=LIST_TIMEOUT_S,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        raise OSError(f"Get-StartApps 没跑成：{type(e).__name__}: {e}") from e
    if done.returncode != 0:
        raise OSError(f"Get-StartApps 退出码 {done.returncode}：{done.stderr[:300]!r}")
    try:
        data = json.loads(done.stdout.decode("utf-8", errors="replace") or "[]")
    except ValueError as e:
        raise OSError(f"Get-StartApps 的输出看不懂：{e}") from e
    return data if isinstance(data, list) else [data]   # 只有一个时 ConvertTo-Json 给的不是列表


def list_apps(refresh=False):
    """开始菜单里的软件（按名字排好）；读不到时是空的（记日志）。"""
    global _cache
    with _lock:
        read_at, apps = _cache
        if not refresh and apps and time.monotonic() - read_at < CACHE_S:
            return apps
        started = time.monotonic()
        try:
            raw = _query_start_apps()
        except OSError as e:
            log.warning("读不到开始菜单里的软件：%s", e)
            return apps   # 以前读到过就先用着
        found = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            name, app_id = str(item.get("Name") or "").strip(), str(item.get("AppID") or "").strip()
            if name and app_id and _launchable(app_id):
                found.setdefault((name.casefold(), app_id), App(name, app_id))
        apps = tuple(sorted(found.values(), key=lambda a: a.name.casefold()))
        _cache = (time.monotonic(), apps)
        log.info("开始菜单里有 %d 个软件（读了 %.1f 秒）", len(apps), time.monotonic() - started)
        return apps


def clear_cache():
    global _cache
    with _lock:
        _cache = (0.0, ())


def _key(text):
    """比较用：不分大小写，去掉空格和标点（"VS Code" ≈ "vscode"）。"""
    return re.sub(r"[\s\W_]+", "", str(text).casefold())


def names():
    """所有软件的名字（不分大小写），给权限规则查"有没有这个软件"。"""
    return {app.name.casefold() for app in list_apps()}


def find_exact(name):
    """名字完全对得上的那个（不分大小写）；没有就重新读一次列表再找（可能刚装的）；还没有就是 None。"""
    wanted = str(name).strip().casefold()
    for refresh in (False, True):
        for app in list_apps(refresh=refresh):
            if app.name.casefold() == wanted:
                return app
    return None


def search(query, apps=None, limit=MAX_RESULTS):
    """按名字找：完全一样 > 开头一样 > 包含 > 每个词都包含；同一档里短的名字在前。最多 limit 个。"""
    apps = list_apps() if apps is None else apps
    words = [_key(w) for w in str(query).split() if _key(w)]
    whole = _key(query)
    if not whole:
        return []
    scored = []
    for app in apps:
        name = _key(app.name)
        if name == whole:
            score = 0
        elif name.startswith(whole):
            score = 1
        elif whole in name:
            score = 2
        elif len(words) > 1 and all(w in name for w in words):
            score = 3
        else:
            continue
        scored.append((score, len(app.name), app.name.casefold(), app))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored[:limit]]


# ---------- 打开（只在芸芸进程里用） ----------

_startfile = getattr(os, "startfile", None)   # 测试里换成假的（conftest），免得真的打开软件


def _launchable(app_id):
    """AppID 看起来正常：不含引号、换行这类字符，路径里没有 ".."（只能是"所有应用"文件夹里的一项）。"""
    if not app_id or len(app_id) > 1000 or re.search(r'["\x00-\x1f]', app_id):
        return False
    return ".." not in re.split(r"[\\/]", app_id)


def launch(app_id):
    """打开开始菜单里的一个软件。返回 (成功, 原因)：原因是给模型看的英文，成功时是空的。"""
    if not _launchable(app_id):
        return False, "This app can't be opened."
    if _startfile is None:
        return False, "Opening apps only works on Windows."
    try:
        _startfile("shell:AppsFolder\\" + app_id)
    except OSError as e:
        log.warning("打开软件出错：%s", e.strerror or type(e).__name__)   # 不记 e 本身：里面有软件的路径
        return False, f"Windows could not open it ({e.strerror or type(e).__name__})."
    return True, ""
