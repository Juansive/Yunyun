"""芸芸自己的工具：进程内 MCP（SDK 的 create_sdk_mcp_server），服务器叫 yunyun，
在 Claude Code 里的名字是 mcp__yunyun__<工具名>（tools/try_notebook.py 实测：Claude 官方、DeepSeek 都能用，
每次调用一样先过 PreToolUse hook，也就是芸芸的权限规则 permissions.decide）。

**只在助手子进程里 import**（会 import SDK）。现在有：记事本的三个工具（notebook.py，阶段 10）、截图（screen.py，阶段 5）、
找软件 / 打开软件（apps.py，阶段 6）。截图和打开软件要请芸芸进程去做，走 bridge.py。
新加工具：在这里挂上，在 permissions.decide 里加规则（不然一律拒绝），界面上的名字加进 session / 阅读框的对照表。
"""
from claude_agent_sdk import create_sdk_mcp_server

from yunyun.assistant.tools.apps import app_tools
from yunyun.assistant.tools.bridge import Bridge  # noqa: F401（给 claude_brain 用）
from yunyun.assistant.tools.notebook import notebook_tools
from yunyun.assistant.tools.screen import screen_tools

SERVER = "yunyun"


def yunyun_server(workspace, notebook=True, bridge=None, screen=False):
    """芸芸的工具服务器（给 ClaudeAgentOptions.mcp_servers）；一个工具都没有时是 None。
    bridge：tools.bridge.Bridge（有它才有打开软件的工具）；screen：服务商能看图片（有 bridge 时才有截图工具）。"""
    tools = notebook_tools(workspace) if notebook else []
    if bridge is not None:
        tools += app_tools(bridge) + (screen_tools(bridge) if screen else [])
    return create_sdk_mcp_server(SERVER, tools=tools) if tools else None
