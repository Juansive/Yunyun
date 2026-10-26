"""芸芸的记忆放在哪、原话存多久（阶段 12.5，设置 → 记事本 →"存放和清空"）。设计见 docs/助手模式计划.md 阶段 12。

- 记忆 = FILES 这六个文件（记事本、相处笔记、回忆、原文存档、日记、我是谁）；默认在 %APPDATA%\\Yunyun\\Yunyun\\assistant，
  用户可以换到别的文件夹（config 的 assistant.memory_dir）；换的时候搬过去（move_to）；
- 原话存几年（assistant.archive_years，YEAR_CHOICES，默认一直留）：每日回顾时按它清一次（reflect.py）；
- 问答卡片、Claude Code 的对话记录、浏览器这些不算记忆，照旧在默认文件夹。

芸芸进程（设置页）和助手子进程（读写记忆）都用：不 import Qt，也不 import SDK。每次都读 config（文件很小）。
"""
import logging
import os
import shutil
from pathlib import Path

from yunyun import config as cfg
from yunyun.paths import data_dir

log = logging.getLogger(__name__)

FILES = ("memory.json", "rapport.json", "recollections.json", "archive.sqlite", "diary.json", "whoami.json")
YEAR_CHOICES = (0, 1, 3)   # 原话存几年；0 = 一直留（默认，2026-10-26 定）


def default_folder():
    return data_dir() / "assistant"


def _section():
    try:
        section = cfg.load_config_or_empty().get("assistant")
    except cfg.ConfigError:
        return {}
    return section if isinstance(section, dict) else {}


def _update(values):
    config = cfg.load_config_or_empty()
    section = config.get("assistant")
    config["assistant"] = {**(section if isinstance(section, dict) else {}), **values}
    cfg.save_config(config)


def folder():
    """现在的记忆文件夹（设置里的那个；没设、或者不是文件夹了就用默认的）。"""
    chosen = _section().get("memory_dir")
    if isinstance(chosen, str) and chosen and os.path.isdir(chosen):
        return Path(chosen)
    return default_folder()


def archive_years():
    years = _section().get("archive_years")
    return years if years in YEAR_CHOICES else 0


def set_archive_years(years):
    _update({"archive_years": years if years in YEAR_CHOICES else 0})


def has_memory(path):
    return any((Path(path) / name).exists() for name in FILES)


def move_to(new, use_existing=False):
    """换记忆文件夹：把六个文件搬过去（先拷、都拷好了再删旧的），再记进设置。
    use_existing：新文件夹里已经有芸芸的记忆（has_memory），就用那里的、这里的不动。出错抛 OSError（什么都不改）。"""
    new, old = Path(new), folder()
    if os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(old)):
        return
    if not use_existing:
        new.mkdir(parents=True, exist_ok=True)
        moved = [name for name in FILES if (old / name).exists()]
        for name in moved:
            shutil.copy2(old / name, new / name)
        for name in moved:
            try:
                (old / name).unlink()
            except OSError as e:   # 拷好了、旧的删不掉（被占着）：留着不碍事
                log.warning("记忆搬走了，旧文件删不掉（%s）", type(e).__name__)
        log.info("记忆文件夹换了，搬了 %d 个文件", len(moved))
    _update({"memory_dir": "" if new == default_folder() else str(new)})
