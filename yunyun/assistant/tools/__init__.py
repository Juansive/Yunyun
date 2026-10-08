"""芸芸自己的工具：进程内 MCP（SDK 的 create_sdk_mcp_server），服务器叫 yunyun，
在 Claude Code 里的名字是 mcp__yunyun__<工具名>（tools/try_memory_tool.py 实测：Claude 官方、DeepSeek 都能用，
每次调用一样先过 PreToolUse hook，也就是芸芸的权限规则 permissions.decide）。

**只在助手子进程里 import**（会 import SDK）。现在有记事本的三个工具（notebook.py，阶段 10）；
阶段 5 的截图、剪贴板也放这里。
"""
from claude_agent_sdk import create_sdk_mcp_server

from yunyun.assistant.tools.notebook import notebook_tools

SERVER = "yunyun"


def yunyun_server(workspace, notebook=True):
    """芸芸的工具服务器（给 ClaudeAgentOptions.mcp_servers）；一个工具都没有时是 None。"""
    tools = notebook_tools(workspace) if notebook else []
    return create_sdk_mcp_server(SERVER, tools=tools) if tools else None
