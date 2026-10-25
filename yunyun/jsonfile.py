"""芸芸自己存的小 JSON 文件（记事本、相处笔记、回忆、提醒、记账本、助手的历史对话）共用的读写。"""
import json
import logging
import os

log = logging.getLogger(__name__)


def read(path, what):
    """读出一个 dict。没有文件是 None；读不了、不是 JSON、不是对象也是 None（记日志，what 是给人看的名字）。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("读不了%s（%s），当作没有", what, type(e).__name__)
        return None
    return data if isinstance(data, dict) else None


def write(path, data, indent=1):
    """先写临时文件再换过去：写到一半断电也不会把原来的文件写坏。写不进去抛 OSError。"""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(temp, path)
