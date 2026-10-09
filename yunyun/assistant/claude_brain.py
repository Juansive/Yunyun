"""助手的"大脑"：用 Claude Agent SDK 启动 Claude Code，把它的消息翻译成 protocol 里的事件。

**只在助手子进程里 import**（会 import SDK，芸芸进程不能碰）。这里的做法都是阶段 0 实测得出的（见 docs/助手模式计划.md）：
- 启动 claude.exe 时加 CREATE_NO_WINDOW：不加的话，打包后的芸芸（没有控制台）会弹出 Windows Terminal 窗口；
- 先清掉继承来的 ANTHROPIC_* / CLAUDE_* 变量：在 Claude 桌面版里开发时，它们会把请求导到桌面版的通道上；
- 会话数据放芸芸自己的数据目录（CLAUDE_CONFIG_DIR），不读用户自己的 Claude Code 设置和 MCP（包括工作文件夹里的 .claude/）；
- 用 tools 决定有哪些工具（不在列表里的根本不存在）：没选工作文件夹时只有网页，选了才有读写文件、执行命令；
- 每次用工具之前由 PermissionGate 拍板（规则在 permissions.py）：PreToolUse hook 先跑，"要问"的再转给
  can_use_tool，它发 PermissionRequest 给芸芸，等 PermissionReply。hook 比 Claude Code 自己的"自动允许"更早，
  所以什么都逃不过这一关（SDK 的说明也这么建议）。
"""
import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, CLINotFoundError, HookMatcher, PermissionResultAllow,
    PermissionResultDeny, ResultMessage, ServerToolUseBlock, StreamEvent, SystemMessage, ToolResultBlock, ToolUseBlock,
    UserMessage,
)

from yunyun.assistant import apps, memory, permissions, proctree, protocol, transcripts
from yunyun.assistant.browser import Browser
from yunyun.assistant.environment import (   # noqa: F401（clean_environment、hide_child_consoles 也从这里用）
    clean_environment, hide_child_consoles, prepare_process, system_proxy_env,
)
from yunyun.assistant.persona import persona
from yunyun.assistant.providers import Provider, Rates, launch_env, usable_tools, usage_cost
from yunyun.assistant.settings import HISTORY_KEEP_DAYS, SESSION_DATA_DIRS
from yunyun.assistant.tools import SERVER as TOOL_SERVER
from yunyun.assistant.tools import Bridge, yunyun_server
from yunyun.paths import data_dir

log = logging.getLogger(__name__)

WEB_TOOLS = ["WebSearch", "WebFetch"]
# 选了工作文件夹才开。Windows 上 Claude Code 有 Git Bash 就用 Bash，没有就用 PowerShell，两个都列上
WORK_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write", "Bash", "PowerShell"]
PROJECT_NOTES_FILES = ("CLAUDE.md", os.path.join(".claude", "CLAUDE.md"))   # 项目规矩：读，但 .claude/ 里的设置不加载
PROJECT_NOTES_MAX_CHARS = 20_000   # 太长的只读开头（每一问都要带着它，太长费钱）
CONFIG_DIR = data_dir() / "assistant" / "claude"   # Claude Code 的会话记录、设置放这里（里面有完整对话内容）
WORKSPACE = data_dir() / "assistant" / "workspace"  # 用户没选工作文件夹时用的：一个空文件夹（只聊天）
# 整理对话（Claude Code 的 /compact）时告诉模型要留下什么（阶段 10）。总结用对话的语言写
COMPACT_COMMAND = ("/compact Keep: what the user asked for and their preferences, which files were changed and why, "
                   "work not finished yet, and key conclusions. Write the summary in the language the user uses.")


# ---------- 启动选项 ----------

def prune_old_sessions(config_dir, days, now=None):
    """删掉 days 天以前的对话记录和备份（按修改时间）；返回删了几个文件。删不掉的（正在用）跳过。"""
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for name in SESSION_DATA_DIRS:
        root = os.path.join(config_dir, name)
        for folder, subfolders, files in os.walk(root, topdown=False):
            for file in files:
                path = os.path.join(folder, file)
                try:
                    if os.path.getmtime(path) < cutoff:
                        os.remove(path)
                        removed += 1
                except OSError as e:
                    log.info("清理旧记录时跳过一个文件：%s", type(e).__name__)
            if folder != root and not os.listdir(folder):
                try:
                    os.rmdir(folder)
                except OSError as e:
                    log.info("清理旧记录时跳过一个文件夹：%s", type(e).__name__)
    return removed


def previous_cost(session_id, provider):
    """一段以前的对话一共花了多少（Claude Code 记录里的 cost-state）；找不到就是 0。
    自己算钱的服务商（provider.own_pricing）按它的价格算 modelUsage，和之后每一轮的算法一样。"""
    for path in (CONFIG_DIR / "projects").glob(f"*/{session_id}.jsonl"):
        transcript = transcripts.read_transcript(str(path))
        if transcript is None:
            continue
        if provider.own_pricing:
            return usage_cost(provider, transcript.model_usage) or 0.0
        return transcript.cost_usd or 0.0
    return 0.0


def wanted_tools(settings):
    return WEB_TOOLS + (WORK_TOOLS if settings.workspace else [])


def read_project_notes(workspace):
    """工作文件夹里的 CLAUDE.md（项目规矩）。没有就是空的；读不了记日志、当没有。"""
    if not workspace:
        return ""
    notes = []
    for name in PROJECT_NOTES_FILES:
        path = os.path.join(workspace, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                notes.append(f.read(PROJECT_NOTES_MAX_CHARS + 1))
        except OSError as e:
            log.warning("读不了项目说明 %s：%s", name, e)
    text = "\n\n".join(note.strip() for note in notes if note.strip())
    if len(text) > PROJECT_NOTES_MAX_CHARS:
        text = text[:PROJECT_NOTES_MAX_CHARS] + "\n…（后面太长，没有读）"
    return text


def build_options(settings, api_key, language, stderr=None, gate=None, resume="", bridge=None, browser=None):
    """启动 Claude Code 的全部选项。gate：PermissionGate（每次用工具前拍板）；测试里可以不传。
    resume：接着哪一段对话聊（Claude Code 的会话编号；空 = 新对话）；
    bridge：tools.Bridge（请芸芸进程截图、打开软件；有它才有这些工具，截图还要服务商能看图片）；
    browser：assistant.browser.Browser（有它才有浏览器的工具）。"""
    provider = settings.info
    screen = bridge is not None and provider.images
    env = {
        **launch_env(provider, api_key, settings.effective_model),
        "CLAUDE_CONFIG_DIR": str(CONFIG_DIR),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",   # 不自动更新、不发遥测和错误报告
        **system_proxy_env(),
    }
    notes = read_project_notes(settings.workspace)
    working = bool(settings.workspace)
    tools = usable_tools(provider, wanted_tools(settings))
    # 记事本（阶段 10）：目录接在人设后面（这段对话里不变，命中缓存），细节和写入用芸芸自己的工具（进程内 MCP）
    notebook = memory.index_lines(memory.load(), settings.workspace) if settings.notebook else None
    server = yunyun_server(settings.workspace, notebook=settings.notebook, bridge=bridge, screen=screen,
                           browser=browser)
    return ClaudeAgentOptions(
        resume=resume or None,            # 历史对话"接着聊"：Claude Code 读回那段对话的记录
        # 撤销（2.7）：Claude Code 改文件前先备份（放在 CLAUDE_CONFIG_DIR 里），每一问传回一个 uuid，rewind_files 到它
        enable_file_checkpointing=working,
        extra_args={"replay-user-messages": None} if working else {},
        model=settings.effective_model,   # 注意：Haiku 4.5 不支持 effort 参数，所以不传
        tools=tools,                      # 只有这些内置工具存在（芸芸自己的工具在 mcp_servers 里）
        mcp_servers={TOOL_SERVER: server} if server else {},
        allowed_tools=[],                 # 一个都不直接放行：全部交给 PermissionGate
        permission_mode="default",        # "要问"的交给 can_use_tool（dontAsk 会把"要问"直接变成拒绝）
        hooks={"PreToolUse": [HookMatcher(hooks=[gate.pre_tool_use])]} if gate else None,
        can_use_tool=gate.can_use_tool if gate else None,
        setting_sources=[],               # 不读用户自己的 Claude Code 设置，也不读工作文件夹里 .claude/ 的设置
        strict_mcp_config=True,           # 不加载别处配置的 MCP 服务器
        system_prompt={"type": "preset", "preset": "claude_code",
                       "append": persona(language, working, notes, web_search="WebSearch" in tools, notebook=notebook,
                                         screen=screen, apps=bridge is not None, browser=browser is not None)},
        include_partial_messages=True,    # 逐字显示
        # 每段对话的上限：Claude Code 按整段对话累计算；但它只认 Claude 的价格，自己算钱的服务商由 TurnState 管。
        # 每次回答的上限都由 TurnState 估算
        max_budget_usd=None if provider.own_pricing else settings.conversation_budget_usd,
        cwd=settings.workspace or str(WORKSPACE),
        env=env,
        stderr=stderr,
    )


# ---------- 权限：每次用工具之前 ----------

class PermissionGate:
    """每次用工具前拍板（permissions.decide），要问的就发 PermissionRequest 给芸芸，等它的 PermissionReply。

    grants：这次对话里你选过"都允许"的（新对话 = 新的子进程 = 新的 PermissionGate，自然就清空了）。
    """

    DENIED = "The user denied this action."
    STOPPED = "The user stopped the current task."

    def __init__(self, workspace, emit, ask_screenshot=False, browser=None):
        """browser：assistant.browser.Browser（网页上点击、输入前，问它这个元素是什么）。"""
        self.workspace = str(workspace)
        self.ask_screenshot = ask_screenshot   # 设置里选了"截图前先问我"
        self.browser = browser
        self.grants = set()
        self.stopped = False  # 这一轮被停下了：之后迟到的确认请求直接拒绝（下一轮开始时 ClaudeBrain.ask 清掉）
        self._emit = emit
        self._pending = {}    # request_id → (Future, Decision)
        self._next_id = 1

    def decide(self, tool, tool_input, element=None):
        known = apps.names() if tool == protocol.OPEN_APP_TOOL else None   # 打开软件：只认开始菜单里有的名字
        return permissions.decide(tool, tool_input, self.workspace, self.grants, self.ask_screenshot, known, element)

    async def _look(self, tool, tool_input):
        """拍板前要先看清的东西：软件列表（第一次要读 1 秒多，在别的线程读好，decide 里就不卡了）；
        网页上要点 / 输入的元素是什么（permissions.ElementFacts，芸芸自己看的，不信模型的说法）。"""
        if tool == protocol.OPEN_APP_TOOL:
            await asyncio.to_thread(apps.list_apps)
        elif tool in protocol.BROWSER_ACTION_TOOLS:
            if self.browser is None:
                return permissions.ElementFacts(problem="Yunyun has no browser here.")
            ref = tool_input.get("ref") if isinstance(tool_input, dict) else None
            return await self.browser.element_facts(ref)
        return None

    def _record(self, tool_id, tool, tool_input, decision, element=None):
        """操作记录：这次工具调用最后怎么定的（protocol.DECISIONS），给芸芸发一条 ToolDecision。"""
        if tool in permissions.SHELL_TOOLS and isinstance(tool_input, dict):
            detail = str(tool_input.get("command") or "")[:500]   # 命令记全文（太长的截一下）
        elif element is not None and not element.problem:
            detail = " · ".join(x for x in (element.site, element.element) if x)   # 网页：哪个网站、哪个元素
            if tool == protocol.BROWSER_TYPE_TOOL and isinstance(tool_input, dict):
                detail += f" ← {str(tool_input.get('text') or '')[:100]}"
        else:
            detail = _tool_detail(tool, tool_input, self.workspace)
        self._emit(protocol.ToolDecision(tool_id or "", tool, decision, detail))

    async def pre_tool_use(self, hook_input, tool_use_id, context):
        """PreToolUse hook：所有工具调用都先经过这里。"""
        tool, tool_input = hook_input.get("tool_name", ""), hook_input.get("tool_input")
        element = await self._look(tool, tool_input)
        decision = self.decide(tool, tool_input, element)
        log.info("工具 %s：%s%s", tool, decision.action, f"（{decision.kind}）" if decision.kind else "")
        output = {"hookEventName": "PreToolUse", "permissionDecision": decision.action}
        if decision.action == permissions.DENY:
            output["permissionDecisionReason"] = decision.reason
            self._record(tool_use_id, tool, tool_input, "blocked", element)
        elif decision.action == permissions.ASK:
            output["permissionDecisionReason"] = f"Yunyun asks the user ({decision.kind})"   # 记录等问完再发
        else:
            output["permissionDecisionReason"] = "Allowed by Yunyun's rules"
            self._record(tool_use_id, tool, tool_input, "granted" if decision.by_grant else "auto", element)
        return {"hookSpecificOutput": output}

    async def can_use_tool(self, tool, tool_input, context):
        """hook 说"要问"的工具：问芸芸（界面上的确认卡片），等回答。"""
        tool_id = getattr(context, "tool_use_id", None) or ""
        element = await self._look(tool, tool_input)
        decision = self.decide(tool, tool_input, element)   # 再看一次：等着的时候你可能刚选了"都允许"
        if decision.action == permissions.ALLOW:
            self._record(tool_id, tool, tool_input, "granted" if decision.by_grant else "auto", element)
            return PermissionResultAllow()
        if decision.action == permissions.DENY:
            self._record(tool_id, tool, tool_input, "blocked", element)
            return PermissionResultDeny(message=decision.reason)
        if self.stopped:
            self._record(tool_id, tool, tool_input, "stopped", element)
            return PermissionResultDeny(message=self.STOPPED)
        request_id, self._next_id = self._next_id, self._next_id + 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, decision)
        self._emit(protocol.PermissionRequest(
            request_id, tool, tool_input if isinstance(tool_input, dict) else {}, decision.kind,
            bool(decision.grant), decision.scope, decision.danger, tool_id, element.element if element else "",
            element.link if element and tool == protocol.BROWSER_DOWNLOAD_TOOL else ""))
        try:
            reply = await future
        finally:
            self._pending.pop(request_id, None)
        remember = reply.allow and reply.remember and bool(decision.grant)
        log.info("确认 %d（%s）：%s%s", request_id, decision.kind, "允许" if reply.allow else "拒绝",
                 "，这次对话都允许" if remember else "")
        if not reply.allow:
            self._record(tool_id, tool, tool_input, "stopped" if self.stopped else "denied", element)
            message = self.DENIED + (f" Reason: {reply.reason}" if reply.reason else "")
            return PermissionResultDeny(message=message)
        if remember:
            self.grants.update(decision.grant)
        self._record(tool_id, tool, tool_input, "remembered" if remember else "allowed", element)
        return PermissionResultAllow()

    def reply(self, reply):
        """芸芸发来的 PermissionReply。"""
        pending = self._pending.get(reply.request_id)
        if pending is None:
            log.info("确认 %d 已经不在等了（停下了？），不理", reply.request_id)
        elif not pending[0].done():
            pending[0].set_result(reply)

    def deny_all(self, reason=""):
        """停下 / 关掉时：等着的确认一律按"拒绝"处理，免得卡住；这一轮之后再来的也直接拒绝。"""
        self.stopped = True
        for request_id, (future, _) in list(self._pending.items()):
            if not future.done():
                future.set_result(protocol.PermissionReply(request_id, False, reason))

    @property
    def waiting(self):
        return len(self._pending)


# ---------- 把 SDK 的消息翻译成事件 ----------

@dataclass
class TurnState:
    """翻译时要记住的东西（跨消息、跨轮次）。"""
    session_cost: float = 0.0          # SDK 报的花费是整个会话的累计值，这一轮的 = 现在的 − 上一轮的
    interrupted: bool = False          # 这一轮用户点了"停止"
    errored: bool = False              # 这一轮已经报过错了（不要重复报）
    seen_tools: set = field(default_factory=set)
    rates: Rates | None = None         # 主模型的价格（providers.Rates）；None = 不知道价格，不估算
    # 自己算钱的服务商（Provider.own_pricing）：整段对话的花费按它的价格算 model_usage，不用 Claude Code 报的
    own_pricing: Provider | None = None
    reply_budget: float | None = None  # 每次回答的上限（美元）
    conversation_budget: float | None = None   # 每段对话的上限：只在自己算钱时由这里管（Claude 官方交给 Claude Code）
    estimate: float = 0.0              # 这一轮估算已经花了多少
    over_budget: bool = False          # 估算超过了每次回答的上限：大脑会停下这一轮
    over_conversation: bool = False    # 这段对话加上这一轮超过了每段对话的上限：也停下
    _message_out: int = 0              # 当前这条模型消息已经算过的输出 token / 网页搜索次数（SDK 报的是累计值）
    _message_searches: int = 0
    unreported: float = 0.0            # 中途停下的几轮里，Claude Code 没算进总花费、芸芸按估算补上的钱
    workspace: str = ""                # 工作文件夹：进度行里的文件路径显示成相对它的
    checkpoint: str = ""               # 这一问（Claude Code 传回来的那条用户消息）的 uuid：撤销时 rewind 到它
    files: list = field(default_factory=list)          # 这一轮用 Edit / Write 改成功的文件
    _editing: dict = field(default_factory=dict)       # 正在改的：工具调用 id → 文件
    remembered: int = 0                                # 这一轮往记事本里记了几条（remember 成功的次数）
    _remembering: set = field(default_factory=set)     # 正在记的：工具调用 id
    # 对话长度：最近一次模型请求读的前文（输入 + 写缓存 + 读缓存）加上它写的输出 = 下一次请求至少要带着的
    context_tokens: int = 0
    context_limit: int = 0             # 超过多少自动整理（0 = 不整理；AssistantSettings.compact_tokens）
    _context_input: int = 0
    compact_meta: dict | None = None   # 整理做完了：Claude Code 报的 compact_metadata（pre_tokens、post_tokens……）
    compact_error: str = ""            # 整理没做成：Claude Code 报的原因

    def new_turn(self):
        self.interrupted = False
        self.errored = False
        self.estimate = 0.0
        self.over_budget = False
        self.over_conversation = False
        self.checkpoint = ""
        self.files = []
        self._editing = {}
        self.remembered = 0
        self._remembering = set()

    @property
    def conversation_cost(self):
        """这段对话到上一轮为止一共花了多少（含中途停下、按估算补上的）。"""
        return self.session_cost + self.unreported

    @property
    def conversation_full(self):
        """自己管每段对话上限时：已经花到上限了（新的一问不用再发出去）。"""
        return self.conversation_budget is not None and self.conversation_cost >= self.conversation_budget

    def track_context(self, usage, new_message):
        """对话长度跟着每次模型调用的用量走（不管知不知道价格）。"""
        if not isinstance(usage, dict):
            return
        if new_message:
            self._context_input = sum(int(usage.get(k) or 0) for k in
                                      ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.context_tokens = self._context_input + int(usage.get("output_tokens") or 0)

    @property
    def needs_compact(self):
        """这一轮好好答完了、对话也超过门槛了：该整理了。"""
        return (bool(self.context_limit) and self.context_tokens >= self.context_limit and not self.interrupted
                and not self.errored and not self.over_budget and not self.over_conversation)

    def add_usage(self, usage, new_message):
        """把一次模型调用的用量（流式事件里的 usage）算进这一轮的估算。"""
        self.track_context(usage, new_message)
        if self.rates is None or not isinstance(usage, dict):
            return
        if new_message:   # message_start：输入（含读写缓存）只在开头报一次
            self._message_out = self._message_searches = 0
            self.estimate += self.rates.cost(usage.get("input_tokens"), 0, usage.get("cache_creation_input_tokens"),
                                             usage.get("cache_read_input_tokens"))
        out = int(usage.get("output_tokens") or 0)
        if out > self._message_out:
            self.estimate += self.rates.cost(output_tokens=out - self._message_out)
            self._message_out = out
        searches = int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
        if searches > self._message_searches:
            self.estimate += self.rates.cost(searches=searches - self._message_searches)
            self._message_searches = searches
        if self.reply_budget is not None and self.estimate >= self.reply_budget:
            self.over_budget = True
        if self.conversation_budget is not None and self.conversation_cost + self.estimate >= self.conversation_budget:
            self.over_conversation = True


_NETWORK_HINTS = ("connection error", "econnrefused", "econnreset", "enotfound", "etimedout", "fetch failed",
                  "getaddrinfo", "network", "timed out", "socket")
_ASSISTANT_ERROR_KINDS = {"authentication_failed": "auth", "billing_error": "billing", "rate_limit": "rate_limit"}


def error_kind(text, sdk_error=None):
    """从 SDK 的出错代号和出错文字判断属于哪一种（protocol.ERROR_KINDS）。"""
    if sdk_error in _ASSISTANT_ERROR_KINDS:
        return _ASSISTANT_ERROR_KINDS[sdk_error]
    lowered = (text or "").lower()
    if any(hint in lowered for hint in _NETWORK_HINTS):
        return "network"
    if "401" in lowered or "invalid x-api-key" in lowered or "authentication" in lowered:
        return "auth"
    if "credit balance" in lowered or "billing" in lowered:
        return "billing"
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limit"
    return "other"


def _tool_detail(name, tool_input, workspace=""):
    """给人看的线索：搜索的关键词、读的网址、文件（工作文件夹里的显示相对路径）……没有就是空的。"""
    for key in ("query", "url", "file_path", "notebook_path", "path", "pattern", "command", "title", "id", "target",
                "name"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if isinstance(value, str) and value:
            if key in ("file_path", "notebook_path", "path") and workspace and os.path.isabs(value):
                relative = os.path.relpath(value, workspace) if _same_drive(value, workspace) else value
                value = value if relative.startswith("..") else relative
            return value[:120]
    return ""


def _same_drive(a, b):
    return os.path.splitdrive(a)[0].lower() == os.path.splitdrive(b)[0].lower()


def _message_text(message):
    return "".join(getattr(block, "text", "") for block in message.content if hasattr(block, "text"))


def translate(message, state):
    """一条 SDK 消息 → 零到多个 protocol 事件。"""
    events = []
    if isinstance(message, StreamEvent):
        event, delta = message.event, message.event.get("delta") or {}
        kind = event.get("type")
        if kind == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
            events.append(protocol.TextDelta(delta["text"]))
        elif kind == "message_start":
            state.add_usage((event.get("message") or {}).get("usage"), new_message=True)
        elif kind == "message_delta":
            state.add_usage(event.get("usage"), new_message=False)
    elif isinstance(message, AssistantMessage):
        if message.error and not state.errored:
            state.errored = True
            events.append(protocol.Error(error_kind(_message_text(message), message.error), _message_text(message)[:500]))
        for block in message.content:
            if isinstance(block, (ToolUseBlock, ServerToolUseBlock)) and block.id not in state.seen_tools:
                state.seen_tools.add(block.id)
                events.append(protocol.ToolStarted(block.id, block.name,
                                                   _tool_detail(block.name, block.input, state.workspace)))
                path = (block.input or {}).get("file_path") or (block.input or {}).get("notebook_path")
                if block.name in permissions.EDIT_TOOLS and isinstance(path, str) and path:
                    state._editing[block.id] = path
                elif block.name == memory.REMEMBER_TOOL:
                    state._remembering.add(block.id)
    elif isinstance(message, UserMessage):
        results = [b for b in message.content if isinstance(b, ToolResultBlock)] if isinstance(message.content, list) else []
        if not results and message.uuid and not state.checkpoint:
            state.checkpoint = message.uuid   # 这一问本身（replay-user-messages 传回来的）：撤销就回到它之前
        for block in results:
            events.append(protocol.ToolFinished(block.tool_use_id, ok=not block.is_error))
            path = state._editing.pop(block.tool_use_id, None)
            if path and not block.is_error and path not in state.files:
                state.files.append(path)
            if block.tool_use_id in state._remembering:
                state._remembering.discard(block.tool_use_id)
                state.remembered += 0 if block.is_error else 1
    elif isinstance(message, SystemMessage):
        data = message.data if isinstance(message.data, dict) else {}
        if message.subtype == "compact_boundary":     # 整理做完了（try_compact.py 实测的消息）
            state.compact_meta = data.get("compact_metadata") or {}
        elif data.get("compact_result") == "failed":
            state.compact_error = str(data.get("compact_error") or "failed")
    elif isinstance(message, ResultMessage):
        events.extend(_turn_finished(message, state))
    return events


def _turn_finished(message, state):
    events = []
    stopped = state.interrupted and not state.over_budget and not state.over_conversation
    if message.subtype == "error_max_budget_usd" or state.over_conversation:
        events.append(protocol.Error("budget"))           # 这段对话累计花到了上限
    elif state.over_budget:
        events.append(protocol.Error("reply_budget", f"~${state.estimate:.3f}"))   # 这次回答快超了，芸芸自己停的
    elif message.subtype != "success" and not stopped and not state.errored:
        # 用户点了"停止"时结果是 error_during_execution：那是正常停下，不算出错
        events.append(protocol.Error("other", f"{message.subtype}: {message.errors or ''}"[:500]))
    elif message.subtype == "success" and message.is_error and not state.errored:
        events.append(protocol.Error(error_kind(message.result), (message.result or "")[:500]))

    # 整段对话一共花了多少：Claude 官方用 Claude Code 报的；自己算钱的服务商按它的价格算各模型的累计用量
    # （model_usage 也是整段对话累计的，连后台小任务用的模型一起，2026-10-08 实测）
    total = (usage_cost(state.own_pricing, message.model_usage) if state.own_pricing is not None
             else message.total_cost_usd)
    cost = None
    if total is not None:
        cost = max(total - state.session_cost, 0.0)
        state.session_cost = total
    if not cost and state.estimate > 0 and (state.interrupted or state.over_budget or state.over_conversation
                                            or total is None):
        # 中途停下时 Claude Code 报的这一轮花费是 0（2026-10-07 实测），可停下前处理过的 token 一样计费：用自己的估算
        cost = state.estimate
        state.unreported += state.estimate
    usage = message.usage or {}
    events.append(protocol.TurnFinished(
        cost_usd=cost,
        session_cost_usd=state.conversation_cost,
        input_tokens=sum(int(usage.get(k) or 0) for k in
                         ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        output_tokens=int(usage.get("output_tokens") or 0),
        stopped=stopped,
        checkpoint=state.checkpoint if state.files else "",   # 没改文件就没什么可撤销的
        files=tuple(state.files),
        session_id=message.session_id or "",
        context_tokens=state.context_tokens,
        context_limit=state.context_limit,
        remembered=state.remembered,
    ))
    return events


# ---------- 大脑 ----------

async def _with_images(text, images):
    """带图片的提问：一条用户消息，内容是几张图片 + 文字（SDK 的 query 也收这种消息流）。"""
    content = [{"type": "image", "source": {"type": "base64", "media_type": image.get("mime") or "image/jpeg",
                                            "data": image.get("data", "")}} for image in images]
    content.append({"type": "text", "text": text})
    yield {"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}


class BrainStartError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class ClaudeBrain:
    """一次助手会话：start() 启动 Claude Code，ask() 问一轮，interrupt() 停下，close() 关掉。事件通过 emit 交出去。"""

    def __init__(self, settings, api_key, language, emit, resume=""):
        """resume：接着哪一段对话聊（Claude Code 的会话编号；空 = 新对话）。"""
        self._settings = settings
        self._api_key = api_key
        self._language = language
        self._emit = emit
        self._resume = resume
        self._client = None
        own = settings.info.own_pricing
        self._state = TurnState(
            rates=settings.info.rates(settings.effective_model),   # Claude 官方自己填的模型名不知道价格：不估算
            own_pricing=settings.info if own else None,
            reply_budget=settings.budget_usd,
            conversation_budget=settings.conversation_budget_usd if own else None,
            workspace=settings.workspace,
            context_limit=settings.compact_tokens,
        )
        self.compacting = False   # 正在整理前面的对话（worker 据此把这时来的提问排到整理之后）
        # 芸芸的浏览器（阶段 6.2）：第一次用时才打开；开 / 关告诉芸芸（开着时不闲置释放）
        self.browser = Browser(on_change=lambda is_open: emit(protocol.BrowserChanged(is_open)),
                               on_download=lambda name, path, by_user: emit(protocol.Downloaded(name, path, by_user)))
        self.gate = PermissionGate(settings.workspace or WORKSPACE, emit, ask_screenshot=settings.screenshot_ask,
                                   browser=self.browser)
        self.bridge = Bridge(emit)   # 请芸芸进程截图、打开软件（服务商看不了图片就没有截图工具，见 build_options）
        self._stderr = deque(maxlen=30)   # Claude Code 的错误输出：启动失败时看最后几行

    async def start(self):
        removed = prepare_process()
        # 放进作业对象：之后开的 claude.exe、命令都在里面，停止时能找全；这个进程没了，它们也跟着结束
        guarded = proctree.guard_this_process()
        log.info("作业对象：%s", "好了" if guarded else "没放进去（停止时只能尽量结束命令）")
        log.info("准备启动 Claude Code：服务商 %s，模型 %s，%s，清掉了 %d 个继承的环境变量",
                 self._settings.provider, self._settings.effective_model,
                 "用了工作文件夹" if self._settings.workspace else "没有工作文件夹", len(removed))
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        removed = prune_old_sessions(CONFIG_DIR, HISTORY_KEEP_DAYS)   # 历史只留 30 天（卡片那边芸芸自己清）
        if removed:
            log.info("清理了 %d 个 %d 天以前的对话记录 / 备份文件", removed, HISTORY_KEEP_DAYS)
        options = build_options(self._settings, self._api_key, self._language, stderr=self._stderr.append,
                                gate=self.gate, resume=self._resume, bridge=self.bridge, browser=self.browser)
        if self._resume:
            # 接着聊：Claude Code 报的花费会接着那段对话的累计往上加（try_resume.py 实测），先把以前花的当起点，
            # 不然第一问会把以前的钱再算一遍
            self._state.session_cost = previous_cost(self._resume, self._settings.info)
            log.info("接着以前的一段对话聊（之前花了 %.4f 美元）", self._state.session_cost)
        self._client = ClaudeSDKClient(options)
        try:
            await self._client.connect()
        except CLINotFoundError as e:
            raise BrainStartError("missing_cli", str(e)) from e
        except Exception as e:
            detail = f"{type(e).__name__}: {e} | {' / '.join(self._stderr)}"[:1000]
            raise BrainStartError(error_kind(detail), detail) from e
        self._emit(protocol.Ready(self._settings.provider, self._settings.effective_model))

    async def ask(self, text, images=()):
        """问一轮。images：一起发的图片（{"data": base64, "mime": …}，阶段 5 输入框里粘贴的）。"""
        if images and not self._settings.info.images:   # 界面上本来就不让粘（5.6），这里兜底
            log.info("这家服务商看不了图片，%d 张图片没发", len(images))
            images = ()
        self._state.new_turn()
        self.gate.stopped = False
        if self._state.conversation_full:
            # 自己管每段对话上限时，花到了就不再发出去（Claude Code 的 max_budget_usd 也是这样报 budget）
            log.info("这段对话已经花了约 %.4f 美元，到了上限 %s，不再提问", self._state.conversation_cost,
                     self._state.conversation_budget)
            self._emit(protocol.Error("budget"))
            self._emit(protocol.TurnFinished(session_cost_usd=self._state.conversation_cost))
            return
        started, first_text = time.monotonic(), None   # 首字要等多久（只记时间，不记内容）
        if images:
            log.info("这一问带了 %d 张图片", len(images))
        try:
            await self._client.query(_with_images(text, images) if images else text)
            async for message in self._client.receive_response():
                for event in translate(message, self._state):
                    if isinstance(event, protocol.TextDelta) and first_text is None:
                        first_text = time.monotonic() - started
                    elif isinstance(event, protocol.Error):
                        log.warning("这一轮出错（%s）：%s", event.kind, event.detail)
                    elif isinstance(event, protocol.TurnFinished):
                        log.info("一轮结束：花费 %s 美元，token 输入 %d / 输出 %d，首字 %s 秒，共 %.1f 秒%s", event.cost_usd,
                                 event.input_tokens, event.output_tokens,
                                 "—" if first_text is None else f"{first_text:.1f}", time.monotonic() - started,
                                 "（用户停止）" if event.stopped else "")
                    self._emit(event)
                state = self._state
                if (state.over_budget or state.over_conversation) and not state.interrupted:
                    log.info("这次回答估算花了约 %.4f 美元，到了%s的上限，停下", state.estimate,
                             "每段对话" if state.over_conversation else "每次回答")
                    # 另开一个任务去停：在这里等它的话，SDK 读消息的那一头可能正等着我们把消息取走
                    asyncio.get_running_loop().create_task(self.interrupt())
        except Exception as e:   # 子进程断了之类：告诉芸芸这一轮出错了，并且结束这一轮
            log.exception("这一轮意外出错")
            self._emit(protocol.Error(error_kind(str(e)), f"{type(e).__name__}: {e}"[:500]))
            self._emit(protocol.TurnFinished(stopped=self._state.interrupted))
            return
        if self._state.needs_compact:
            await self.compact()

    async def compact(self):
        """把前面的对话整理成摘要（Claude Code 的 /compact，阶段 10）：之后每一问只带着摘要，不再带着全部前文。
        整理的花费算进这段对话；没做成只记日志（芸芸那边不提示出错）。"""
        state = self._state
        before = state.context_tokens
        state.new_turn()
        state.compact_meta, state.compact_error = None, ""
        self.compacting = True
        self._emit(protocol.Compacting())
        log.info("对话长度约 %d token，超过了 %d，开始整理", before, state.context_limit)
        started, finished = time.monotonic(), None
        try:
            await self._client.query(COMPACT_COMMAND)
            async for message in self._client.receive_response():
                for event in translate(message, state):   # 整理时模型写的总结、进度都不转给芸芸，只要花费
                    if isinstance(event, protocol.TurnFinished):
                        finished = event
                    elif isinstance(event, protocol.Error):
                        state.compact_error = state.compact_error or event.kind
        except Exception as e:   # 子进程断了之类：这次不整理了，下一问照常
            log.warning("整理对话时出错：%s: %s", type(e).__name__, e)
            state.compact_error = state.compact_error or type(e).__name__
        finally:
            self.compacting = False
        meta = state.compact_meta
        ok = meta is not None and not state.compact_error and not state.interrupted
        # post_tokens 只算整理后的对话本身（不含说明和工具列表，偏小）；下一问答完会按实际用量重新算
        state.context_tokens = int(meta.get("post_tokens") or 0) if ok else before
        log.info("整理%s：%d → %d token，%.1f 秒，花费 %s 美元%s", "好了" if ok else "没做成", before,
                 state.context_tokens, time.monotonic() - started, finished.cost_usd if finished else None,
                 f"（{state.compact_error}）" if state.compact_error else "")
        self._emit(protocol.Compacted(
            ok, before, state.context_tokens,
            cost_usd=finished.cost_usd if finished else None,
            session_cost_usd=finished.session_cost_usd if finished else state.conversation_cost,
            detail=state.compact_error[:300]))

    def reply_permission(self, reply):
        self.gate.reply(reply)

    def reply_bridge(self, reply):
        """芸芸进程做完了请它做的事（ScreenshotReply / OpenAppReply）。"""
        self.bridge.reply(reply)

    async def rewind(self, checkpoint):
        """撤销：用 Edit / Write 改过的文件恢复到 checkpoint 那一问之前（之后几问的改动也一起撤销）。"""
        try:
            await self._client.rewind_files(checkpoint)
        except Exception as e:   # 没有这个检查点、子进程断了……：告诉芸芸没做成
            log.warning("撤销没做成：%s: %s", type(e).__name__, e)
            self._emit(protocol.Rewound(checkpoint, False, f"{type(e).__name__}: {e}"[:300]))
            return
        log.info("撤销做完了")
        self._emit(protocol.Rewound(checkpoint, True))

    async def interrupt(self):
        self.gate.deny_all(PermissionGate.STOPPED)   # 等着确认的先拒绝掉，不然停不下来
        self.bridge.cancel_all()                      # 等着截图、打开软件的也一样
        if self._client is not None:
            self._state.interrupted = True
            running = proctree.before_stop()
            await self._client.interrupt()
            self._kill_leftovers(running)

    async def close(self):
        self.gate.deny_all(PermissionGate.STOPPED)
        self.bridge.cancel_all()
        await self.browser.close()   # 好好关浏览器（不然作业对象一结束它，下次打开 Edge 会说没有正常关闭）
        if self._client is not None:
            client, self._client = self._client, None
            running = proctree.before_stop()
            await client.disconnect()
            self._kill_leftovers(running)

    @staticmethod
    def _kill_leftovers(running):
        """Claude Code 停下后，它执行命令开的进程可能还在后台跑（Windows 不会顺着进程树结束）：结束掉（见 proctree）。"""
        try:
            killed = proctree.stop_commands(running)
        except OSError as e:
            log.warning("结束命令进程时出错：%s", e)
            return
        if killed:
            log.info("停下后还有 %d 个命令进程在跑，结束掉了", killed)
