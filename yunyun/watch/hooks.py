"""在你自己的 Claude Code 设置（~/.claude/settings.json）里加 / 删芸芸的 hook（阶段 8，设置 → 旁观）。

加的两个 hook 都是运行 relay.py（开发版：pythonw relay.py；打包版：芸芸.exe --watch-relay）：
- Stop：做完一轮；
- Notification，matcher = permission_prompt|elicitation_dialog：等你确认 / 让你选。

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

NOTIFICATION_MATCHER = "permission_prompt|elicitation_dialog"
TIMEOUT_S = 10            # hook 最多跑多久（relay 正常几十毫秒就完）
OURS = ("watch/relay.py", "--watch-relay")   # 命令里有这些 = 芸芸加的


class HooksError(Exception):
    """改不了：kind = "broken"（设置文件不是合法的 JSON）/ "io"（读写出错）。"""

    def __init__(self, kind, detail=""):
        super().__init__(detail or kind)
        self.kind = kind


def settings_path():
    return Path.home() / ".claude" / "settings.json"


def relay_command():
    """hook 要运行的命令（路径用正斜杠：Git Bash、cmd 都认）。"""
    if getattr(sys, "frozen", False):   # 打包版：芸芸.exe --watch-relay（tools/exe_entry.py 一开始就分流，不 import Qt）
        return f'"{Path(sys.executable).as_posix()}" --watch-relay'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")   # 不弹黑窗口
    script = resource_path("yunyun", "watch", "relay.py")
    return f'"{(pythonw if pythonw.exists() else python).as_posix()}" "{script.as_posix()}"'


def is_ours(command):
    command = str(command or "").replace("\\", "/")
    return any(mark in command for mark in OURS)


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
            kept = [h for h in inner if not (isinstance(h, dict) and is_ours(h.get("command")))]
            removed += len(inner) - len(kept)
            if kept:
                kept_groups.append({**group, "hooks": kept})
        if kept_groups:
            cleaned[event] = kept_groups
    return cleaned, removed


def status(path=None):
    """"connected"（两个都在）/ "partial"（只剩一个，比如你手动删过）/ "disconnected" / "broken"（设置文件看不懂）。"""
    try:
        hooks = _load(path or settings_path()).get("hooks", {})
    except HooksError as e:
        return "broken" if e.kind == "broken" else "disconnected"
    found = {event for event, groups in hooks.items() if isinstance(groups, list)
             for group in groups if isinstance(group, dict) and isinstance(group.get("hooks"), list)
             for h in group["hooks"] if isinstance(h, dict) and is_ours(h.get("command"))}
    if {"Stop", "Notification"} <= found:
        return "connected"
    return "partial" if found else "disconnected"


def connect(path=None, command=None):
    """加上芸芸的两个 hook（已经有了就换成新的命令：比如芸芸挪了位置）。返回备份文件的路径（原来没有设置文件是 None）。"""
    path = path or settings_path()
    data = _load(path)
    command = command or relay_command()
    backup = _backup(path)
    hooks, _ = _without_ours(data.get("hooks", {}))
    entry = {"type": "command", "command": command, "timeout": TIMEOUT_S}
    hooks.setdefault("Stop", []).append({"hooks": [dict(entry)]})
    hooks.setdefault("Notification", []).append({"matcher": NOTIFICATION_MATCHER, "hooks": [dict(entry)]})
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
