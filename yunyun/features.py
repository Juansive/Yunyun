"""这个版本里哪些功能能用（芸芸进程里用，不 import 任何重的东西）。"""
import importlib.util
from functools import cache


@cache
def assistant_available():
    """助手模式要用 Claude Agent SDK（自带 Claude Code，两百多 MB）。

    开发版装了就有；打包版要等阶段 9 把它一起打进 exe，在那之前右键菜单和设置窗口里都不显示助手的入口，
    免得朋友点了只看到报错。find_spec 只找、不 import，不会把 SDK 载入芸芸进程。
    """
    return importlib.util.find_spec("claude_agent_sdk") is not None
