"""芸芸自己的工具：进程内 MCP（SDK 的 create_sdk_mcp_server），服务器叫 yunyun，
在 Claude Code 里的名字是 mcp__yunyun__<工具名>（tools/try_notebook.py 实测：Claude 官方、DeepSeek 都能用，
每次调用一样先过 PreToolUse hook，也就是芸芸的权限规则 permissions.decide）。

**只在助手子进程里 import**（会 import SDK）。现在有：记事本的三个工具（notebook.py，阶段 10）、截图（screen.py，阶段 5）、
找软件 / 打开软件（apps.py，阶段 6）、芸芸的浏览器（browser.py，阶段 6.2）、翻回忆（recall_chats.py，阶段 11.4）、
提醒、剪贴板、窗口（reminders.py、clipboard.py、windows.py，阶段 11.5）。截图和打开软件要请芸芸进程去做，走 bridge.py。
新加工具：在这里挂上，在 permissions.decide 里加规则（不然一律拒绝），界面上的名字加进 session / 阅读框的对照表。
"""
from claude_agent_sdk import create_sdk_mcp_server

from yunyun.assistant.tools.apps import app_tools
from yunyun.assistant.tools.bridge import Bridge  # noqa: F401（给 claude_brain 用）
from yunyun.assistant.tools.browser import browser_tools
from yunyun.assistant.tools.clipboard import clipboard_tools
from yunyun.assistant.tools.notebook import notebook_tools
from yunyun.assistant.tools.recall_chats import recall_chats_tools
from yunyun.assistant.tools.reminders import reminder_tools
from yunyun.assistant.tools.screen import screen_tools
from yunyun.assistant.tools.windows import window_tools

SERVER = "yunyun"


def yunyun_server(workspace, notebook=True, bridge=None, screen=False, browser=None, chats=None, language="zh"):
    """芸芸的工具服务器（给 ClaudeAgentOptions.mcp_servers）；一个工具都没有时是 None。
    bridge：tools.bridge.Bridge（有它才有打开软件的工具）；screen：服务商能看图片（有 bridge 时才有截图工具）；
    browser：assistant.browser.Browser（有它才有浏览器的工具）；chats：Claude Code 的对话记录文件夹
    （有它才有"翻回忆"的工具，阶段 11.4；回想以前的对话关着时不给）。"""
    tools = notebook_tools(workspace) if notebook else []
    if chats is not None:
        tools += recall_chats_tools(chats, language)
    if bridge is not None:
        tools += (app_tools(bridge) + reminder_tools(bridge) + clipboard_tools() + window_tools(bridge)
                  + (screen_tools(bridge) if screen else []))
    if browser is not None:
        tools += browser_tools(browser)
    return create_sdk_mcp_server(SERVER, tools=tools) if tools else None
