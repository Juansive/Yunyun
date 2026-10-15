"""电源和程序的三个工具（阶段 11.6e）：power 锁屏 / 睡眠 / 关机 / 重启 / 取消关机（关机、重启要问）、list_processes 列出
正在运行的程序（不用问）、end_process 结束一个程序（每次问）。做事在 assistant/processes.py；规则在 permissions.decide。
"""
import asyncio

from claude_agent_sdk import tool

from yunyun.assistant import processes, protocol
from yunyun.assistant.tools.bridge import text_result as _text

POWER = ("Lock the screen, put the computer to sleep, shut down or restart, or cancel a pending shutdown / restart. "
         "lock, sleep and cancel need no confirmation; shutdown and restart are confirmed by the user every time and "
         f"then wait {processes.GRACE_S} seconds (Windows shows a notice), so they can still be cancelled.")
POWER_SCHEMA = {"type": "object", "properties": {"action": {"type": "string", "enum": list(protocol.POWER_ACTIONS)}},
                "required": ["action"]}
LIST = ("List the programs running on the user's computer, grouped by program, with the memory each uses (biggest "
        "first). Use it for \"what's slowing my computer down\" or before end_process.")
END = ("End (force-quit) a running program: every process with that program name, or one process by pid. The user "
       "confirms every time; unsaved work in it is lost. Yunyun herself and Windows system processes can't be ended. "
       "Use the exact name from list_processes, e.g. notepad.exe.")
END_SCHEMA = {"type": "object",
              "properties": {"name": {"type": "string", "description": "Program name, e.g. notepad.exe"},
                             "pid": {"type": "integer", "description": "Optional: one process id instead of a name"}}}


def system_tools(language="zh"):
    @tool("power", POWER, POWER_SCHEMA)
    async def power(args):
        ok, detail = await asyncio.to_thread(processes.power, str(args.get("action") or ""), language)
        return _text(detail, not ok)

    @tool("list_processes", LIST, {"type": "object", "properties": {}})
    async def list_processes(args):
        return _text(await asyncio.to_thread(processes.list_text))

    @tool("end_process", END, END_SCHEMA)
    async def end_process(args):
        ok, detail = await asyncio.to_thread(processes.end, args)
        return _text(detail, not ok)

    return [power, list_processes, end_process]
