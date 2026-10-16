"""在你自己的 Claude Code 设置（~/.claude/settings.json）里加 / 删芸芸的 hook（阶段 8，设置 → 旁观）。

加的 hook 都是运行 relay.py（开发版：pythonw relay.py；打包版：芸芸.exe --watch-relay），事件见 EVENTS：
- 阶段 8：Stop 做完一轮；Notification 等你确认 / 让你选（还有"闲着"：被打断时靠它知道不忙了）；
- 8.6a（2026-10-16）芸芸跟着换样子：UserPromptSubmit 开始干活、PostToolUse / PostToolUseFailure 用完一个工具（批准完接着干）、
  PermissionRequest 要你批准（8.6b 芸芸的确认卡片）、
  StopFailure 出错停下、SessionEnd 会话结束。只用来换样子的放在后台跑（"async"），一点都不拖慢 Claude Code。
- **写成"程序 + 参数"**（"command" 是程序路径、"args" 是参数，Claude Code 2.1.139 起）：不经过命令行解释，
  路径里有空格、中文都没事。10-16 实测：有的地方 hook 的命令是交给 PowerShell 解释的（芸芸带的 Claude Code 就是；
  桌面版在装了 Git Bash 的电脑上用的是 Git Bash，老写法照样能跑），以前那种 `"路径" --watch-relay` 在 PowerShell 里
  是语法错误，hook 根本跑不起来（旧的连接在设置页显示"要重新连接"）。
  老版本不认 "args"、只运行程序：芸芸.exe 不带参数、标准输入是管道时也当 hook 处理（tools/exe_entry.py），不会开出一个芸芸。

规矩（改的是你自己的设置，要格外小心）：
- 改之前**先备份**整个文件（settings.json.yunyun-<时间>.bak，放在旁边）；设置页点"连接"前会先问你；
- 只加 / 只删芸芸自己的那几条（按命令里的 relay 认），你自己的 hook、别的设置一个字都不动；
- 文件不是合法的 JSON：什么都不改，告诉你（HooksError("broken")）；
- 写的时候先写临时文件再换过去，写到一半出错也不会把原文件写坏。
"""
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from yunyun.paths import resource_path

log = logging.getLogger(__name__)

NOTIFICATION_MATCHER = "permission_prompt|elicitation_dialog|idle_prompt"
EVENTS = {   # 事件 → (matcher, 最多跑几秒, 后台跑)。要等芸芸回话的两个比 relay.REPLY_EVENTS 长一点；别的 relay 几十毫秒就完
    "UserPromptSubmit": (None, 10, True),
    "PostToolUse": (None, 10, True),
    "PostToolUseFailure": (None, 10, True),   # 工具跑了但出错（8.6b：批准过的那件事做完了，卡片收起）
    "PermissionRequest": (None, 600, False),
    "Notification": (NOTIFICATION_MATCHER, 10, True),
    "Stop": (None, 90, False),
    "StopFailure": (None, 10, True),
    "SessionEnd": (None, 10, True),
}
OURS = ("watch/relay.py", "--watch-relay")   # 命令里有这些 = 芸芸加的


class HooksError(Exception):
    """改不了：kind = "broken"（设置文件不是合法的 JSON）/ "io"（读写出错）。"""

    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind


def settings_path():
    return Path.home() / ".claude" / "settings.json"


def relay_command():
    """hook 要运行的 (程序, 参数)（路径用正斜杠）。"""
    if getattr(sys, "frozen", False):   # 打包版：芸芸.exe --watch-relay（tools/exe_entry.py 一开始就分流，不 import Qt）
        return Path(sys.executable).as_posix(), ["--watch-relay"]
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")   # 不弹黑窗口
    return (pythonw if pythonw.exists() else python).as_posix(), [resource_path("yunyun", "watch", "relay.py").as_posix()]


def is_ours(entry):
    """一条 hook（或者它的命令）是不是芸芸加的。"""
    if isinstance(entry, dict):
        args = entry.get("args") if isinstance(entry.get("args"), list) else []
        entry = " ".join(str(part) for part in [entry.get("command"), *args])
    command = str(entry or "").replace("\\", "/")
    return any(mark in command for mark in OURS)


def _current(entry, command):
    """芸芸的这条是不是现在这个样子（程序 + 参数，命令对得上）。"""
    program, args = command
    return entry.get("command") == program and entry.get("args") == args


def _load(path):
    """读设置（没有这个文件就是空的）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise HooksError("io", type(e).__name__) from e
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as e:
        raise HooksError("broken", str(e)[:200]) from e
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise HooksError("broken", "not an object")
    return data


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".yunyun-tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _backup(path):
    """改之前先备份（原来没有这个文件就不用备份）；返回备份的路径或 None。"""
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.yunyun-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(path, backup)
    return backup


def _without_ours(hooks):
    """去掉芸芸加的那几条（组里空了就去掉组，事件空了就去掉事件）；返回新的 hooks 和去掉了几条。"""
    cleaned, removed = {}, 0
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            cleaned[event] = groups
            continue
        kept_groups = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept = [h for h in inner if not (isinstance(h, dict) and is_ours(h))]
            removed += len(inner) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            cleaned[event] = kept_groups
    return cleaned, removed


def status(path=None, command=None):
    """"connected"（都在、都是现在的样子）/ "outdated"（芸芸更新过：比如以前只连了两个、或者是老写法，要重新连接一下）/
    "partial"（少了几个，比如你手动删过）/ "disconnected" / "broken"（设置文件看不懂）。"""
    try:
        hooks = _load(path or settings_path()).get("hooks", {})
    except HooksError as e:
        return "broken" if e.kind == "broken" else "disconnected"
    command = command or relay_command()
    found = {(event, _current(h, command)) for event, groups in hooks.items() if isinstance(groups, list)
             for group in groups if isinstance(group, dict) and isinstance(group.get("hooks"), list)
             for h in group["hooks"] if isinstance(h, dict) and is_ours(h)}
    if not found:
        return "disconnected"
    if found == {(event, True) for event in EVENTS}:
        return "connected"
    if any(not current for _, current in found) or {"Stop", "Notification"} <= {event for event, _ in found}:
        return "outdated"   # 老写法，或者阶段 8 那两个都在、只是缺了 8.6 加的
    return "partial"


def connect(path=None, command=None):
    """加上芸芸的 hook（已经有了就换成新的：比如芸芸挪了位置、更新过）。返回备份文件的路径（原来没有设置文件是 None）。
    command：(程序, 参数)，不给就是 relay_command()。"""
    path = path or settings_path()
    data = _load(path)
    program, args = command or relay_command()
    backup = _backup(path)
    hooks, _ = _without_ours(data.get("hooks", {}))
    for event, (matcher, timeout, background) in EVENTS.items():
        entry = {"type": "command", "command": program, "args": list(args), "timeout": timeout}
        if background:
            entry["async"] = True
        hooks.setdefault(event, []).append({"matcher": matcher, "hooks": [entry]} if matcher else {"hooks": [entry]})
    data["hooks"] = hooks
    try:
        _save(path, data)
    except OSError as e:
        raise HooksError("io", type(e).__name__) from e
    log.info("旁观模式：在 Claude Code 的设置里加了 hook（备份：%s）", backup.name if backup else "原来没有设置文件")
    return backup


def disconnect(path=None):
    """删掉芸芸加的 hook（别的不动）。返回备份文件的路径；本来就没有芸芸的 hook 时什么都不改，返回 None。"""
    path = path or settings_path()
    data = _load(path)
    hooks, removed = _without_ours(data.get("hooks", {}))
    if not removed:
        return None
    backup = _backup(path)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    try:
        _save(path, data)
    except OSError as e:
        raise HooksError("io", type(e).__name__) from e
    log.info("旁观模式：从 Claude Code 的设置里删了 %d 条 hook", removed)
    return backup
