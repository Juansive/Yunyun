"""助手的"大脑"：用 Claude Agent SDK 启动 Claude Code，把它的消息翻译成 protocol 里的事件。

**只在助手子进程里 import**（会 import SDK，芸芸进程不能碰）。这里的做法都是阶段 0 实测得出的（见 docs/助手模式计划.md）：
- 启动 claude.exe 时加 CREATE_NO_WINDOW：不加的话，打包后的芸芸（没有控制台）会弹出 Windows Terminal 窗口；
- 先清掉继承来的 ANTHROPIC_* / CLAUDE_* 变量：在 Claude 桌面版里开发时，它们会把请求导到桌面版的通道上；
- 会话数据放芸芸自己的数据目录（CLAUDE_CONFIG_DIR），不读用户自己的 Claude Code 设置和 MCP；
- 阶段 1 只开"网页搜索、读网页"：用 tools 决定有哪些工具（不在列表里的根本不存在），工作文件夹是个空文件夹。
"""
import logging
from collections import deque
from dataclasses import dataclass, field

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, CLINotFoundError, ResultMessage, ServerToolUseBlock,
    StreamEvent, ToolResultBlock, ToolUseBlock, UserMessage,
)

from yunyun.assistant import protocol
from yunyun.assistant.environment import (   # noqa: F401（clean_environment、hide_child_consoles 也从这里用）
    clean_environment, hide_child_consoles, prepare_process, system_proxy_env,
)
from yunyun.assistant.persona import persona
from yunyun.assistant.providers import launch_env, usable_tools
from yunyun.paths import data_dir

log = logging.getLogger(__name__)

WANTED_TOOLS = ["WebSearch", "WebFetch"]          # 阶段 1：只读，只有网页
CONFIG_DIR = data_dir() / "assistant" / "claude"   # Claude Code 的会话记录、设置放这里（里面有完整对话内容）
WORKSPACE = data_dir() / "assistant" / "workspace"  # 阶段 1 的工作文件夹：空的，阶段 2 改成用户选的文件夹


# ---------- 启动选项 ----------

def build_options(settings, api_key, language, stderr=None):
    """启动 Claude Code 的全部选项。"""
    provider = settings.info
    tools = usable_tools(provider, WANTED_TOOLS)
    env = {
        **launch_env(provider, api_key, settings.effective_model),
        "CLAUDE_CONFIG_DIR": str(CONFIG_DIR),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",   # 不自动更新、不发遥测和错误报告
        **system_proxy_env(),
    }
    return ClaudeAgentOptions(
        model=settings.effective_model,   # 注意：Haiku 4.5 不支持 effort 参数，所以不传
        tools=tools,                      # 只有这些工具存在
        allowed_tools=tools,              # 而且都不用问
        permission_mode="dontAsk",        # 别的一律拒绝（阶段 2 才有"问你"）
        setting_sources=[],               # 不读用户自己的 Claude Code 设置
        strict_mcp_config=True,           # 不加载别处配置的 MCP 服务器
        system_prompt={"type": "preset", "preset": "claude_code", "append": persona(language)},
        include_partial_messages=True,    # 逐字显示
        max_budget_usd=settings.budget_usd,
        cwd=str(WORKSPACE),
        env=env,
        stderr=stderr,
    )


# ---------- 把 SDK 的消息翻译成事件 ----------

@dataclass
class TurnState:
    """翻译时要记住的东西（跨消息、跨轮次）。"""
    cost_in_usd: bool = True
    session_cost: float = 0.0          # SDK 报的花费是整个会话的累计值，这一轮的 = 现在的 − 上一轮的
    interrupted: bool = False          # 这一轮用户点了"停止"
    errored: bool = False              # 这一轮已经报过错了（不要重复报）
    seen_tools: set = field(default_factory=set)

    def new_turn(self):
        self.interrupted = False
        self.errored = False


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


def _tool_detail(name, tool_input):
    """给人看的线索：搜索的关键词、读的网址……没有就是空的。"""
    for key in ("query", "url", "file_path", "path", "pattern", "command"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if isinstance(value, str) and value:
            return value[:120]
    return ""


def _message_text(message):
    return "".join(getattr(block, "text", "") for block in message.content if hasattr(block, "text"))


def translate(message, state):
    """一条 SDK 消息 → 零到多个 protocol 事件。"""
    events = []
    if isinstance(message, StreamEvent):
        event, delta = message.event, message.event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta" and delta.get("text"):
            events.append(protocol.TextDelta(delta["text"]))
    elif isinstance(message, AssistantMessage):
        if message.error and not state.errored:
            state.errored = True
            events.append(protocol.Error(error_kind(_message_text(message), message.error), _message_text(message)[:500]))
        for block in message.content:
            if isinstance(block, (ToolUseBlock, ServerToolUseBlock)) and block.id not in state.seen_tools:
                state.seen_tools.add(block.id)
                events.append(protocol.ToolStarted(block.id, block.name, _tool_detail(block.name, block.input)))
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        for block in message.content:
            if isinstance(block, ToolResultBlock):
                events.append(protocol.ToolFinished(block.tool_use_id, ok=not block.is_error))
    elif isinstance(message, ResultMessage):
        events.extend(_turn_finished(message, state))
    return events


def _turn_finished(message, state):
    events = []
    stopped = state.interrupted
    if message.subtype == "error_max_budget_usd":
        events.append(protocol.Error("budget"))
    elif message.subtype != "success" and not stopped and not state.errored:
        # 用户点了"停止"时结果是 error_during_execution：那是正常停下，不算出错
        events.append(protocol.Error("other", f"{message.subtype}: {message.errors or ''}"[:500]))
    elif message.subtype == "success" and message.is_error and not state.errored:
        events.append(protocol.Error(error_kind(message.result), (message.result or "")[:500]))

    total = message.total_cost_usd
    cost = None
    if total is not None:
        cost = max(total - state.session_cost, 0.0)
        state.session_cost = total
    usage = message.usage or {}
    events.append(protocol.TurnFinished(
        cost_usd=cost if state.cost_in_usd else None,
        session_cost_usd=state.session_cost if state.cost_in_usd else None,
        input_tokens=sum(int(usage.get(k) or 0) for k in
                         ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        output_tokens=int(usage.get("output_tokens") or 0),
        stopped=stopped,
    ))
    return events


# ---------- 大脑 ----------

class BrainStartError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class ClaudeBrain:
    """一次助手会话：start() 启动 Claude Code，ask() 问一轮，interrupt() 停下，close() 关掉。事件通过 emit 交出去。"""

    def __init__(self, settings, api_key, language, emit):
        self._settings = settings
        self._api_key = api_key
        self._language = language
        self._emit = emit
        self._client = None
        self._state = TurnState(cost_in_usd=settings.info.cost_in_usd)
        self._stderr = deque(maxlen=30)   # Claude Code 的错误输出：启动失败时看最后几行

    async def start(self):
        removed = prepare_process()
        log.info("准备启动 Claude Code：服务商 %s，模型 %s，清掉了 %d 个继承的环境变量",
                 self._settings.provider, self._settings.effective_model, len(removed))
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        options = build_options(self._settings, self._api_key, self._language, stderr=self._stderr.append)
        self._client = ClaudeSDKClient(options)
        try:
            await self._client.connect()
        except CLINotFoundError as e:
            raise BrainStartError("missing_cli", str(e)) from e
        except Exception as e:
            detail = f"{type(e).__name__}: {e} | {' / '.join(self._stderr)}"[:1000]
            raise BrainStartError(error_kind(detail), detail) from e
        self._emit(protocol.Ready(self._settings.provider, self._settings.effective_model))

    async def ask(self, text):
        self._state.new_turn()
        try:
            await self._client.query(text)
            async for message in self._client.receive_response():
                for event in translate(message, self._state):
                    if isinstance(event, protocol.Error):
                        log.warning("这一轮出错（%s）：%s", event.kind, event.detail)
                    elif isinstance(event, protocol.TurnFinished):
                        log.info("一轮结束：花费 %s 美元，token 输入 %d / 输出 %d%s", event.cost_usd,
                                 event.input_tokens, event.output_tokens, "（用户停止）" if event.stopped else "")
                    self._emit(event)
        except Exception as e:   # 子进程断了之类：告诉芸芸这一轮出错了，并且结束这一轮
            log.exception("这一轮意外出错")
            self._emit(protocol.Error(error_kind(str(e)), f"{type(e).__name__}: {e}"[:500]))
            self._emit(protocol.TurnFinished(stopped=self._state.interrupted))

    async def interrupt(self):
        if self._client is not None:
            self._state.interrupted = True
            await self._client.interrupt()

    async def close(self):
        if self._client is not None:
            client, self._client = self._client, None
            await client.disconnect()
