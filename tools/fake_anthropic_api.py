"""开发用：本机的假 Anthropic 接口（/v1/messages，流式 SSE），让真的 Claude Code 按剧本调用工具。不花钱、不联网。

tools/try_permissions.py、tools/try_rewind.py 用它验证"只有真的 Claude Code 才知道"的行为（权限关卡、撤销……）：

    api = FakeApi([("tool", "Read", {...}), ("text", "做完了")])   # 剧本：主循环每来一次请求，就按顺序回一步
    server = serve(api)
    brain = make_brain(server, tmp, workspace, emit)                # ClaudeBrain，服务商地址指到假接口，Key 是假的
"""
import json
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant import archive, claude_brain, memory, rapport, recollections  # noqa: E402
from yunyun.assistant.providers import PROVIDERS, Provider  # noqa: E402
from yunyun.assistant.settings import AssistantSettings  # noqa: E402

SHELL = "<shell>"   # 剧本里的"执行命令"：Claude Code 给的是 Bash 就用 Bash，给的是 PowerShell 就用 PowerShell


class FakeApi:
    """假的 Anthropic Messages 接口。主循环（系统提示词里有芸芸的人设）按剧本回答；别的请求（起标题之类）随便答一句。"""

    def __init__(self, steps, usage=None):
        self.steps = list(steps)
        self.usage = usage or {"input_tokens": 10, "output_tokens": 5}   # 主循环每次回答报的用量（看 Claude Code 怎么算花费）
        self.requests = []          # 每个请求：(方法, 路径, 模型, Key 怎么传的："bearer" / "x-api-key" / "")
        self.offered_tools = None   # Claude Code 第一次请求时带的工具列表
        self.by_id = {}             # 交给模型的每个工具结果：tool_use_id → (出错没有, 文字)（去掉了 Claude Code 附的提醒）
        self.calls = []             # 每一步实际发出的工具调用：(id, 工具, 参数)
        self.others = []            # 别的请求：(路径, 模型)
        self.prompts_seen = []      # 每次主循环请求里带着的提问（看"接着聊"时前面的对话有没有一起交给模型）
        self.lock = threading.Lock()

    def handle(self, path, body):
        if path.endswith("/count_tokens"):
            return {"input_tokens": 100}, False
        system = json.dumps(body.get("system", ""), ensure_ascii=False)
        model = body.get("model") or "claude-haiku-4-5"
        if "/v1/messages" not in path or "芸芸" not in system:
            self.others.append((path, body.get("model")))
            return (self._message([{"type": "text", "text": "ok"}], "end_turn", model, {"input_tokens": 10, "output_tokens": 5}),
                    bool(body.get("stream")))
        with self.lock:
            if self.offered_tools is None:
                self.offered_tools = [t.get("name") for t in body.get("tools", [])]
            prompts = []
            for message in body["messages"]:
                content = message.get("content")
                if message.get("role") != "user":
                    continue
                texts = [content] if isinstance(content, str) else [
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                prompts.extend(t for t in texts if t and not t.startswith("<system-reminder>"))
            self.prompts_seen.append(prompts)
            for message in body["messages"]:
                for block in message.get("content") if isinstance(message.get("content"), list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        content = block.get("content")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        text = str(content).split("<system-reminder>")[0].strip()
                        self.by_id.setdefault(block["tool_use_id"], (bool(block.get("is_error")), text))
            self.prepare_step()
            step = self.steps.pop(0) if self.steps else ("text", "（剧本演完了）")
            if step[0] == "text":
                return self._message([{"type": "text", "text": step[1]}], "end_turn", model), bool(body.get("stream"))
            _, tool, tool_input = step
            if tool == SHELL:
                tool = "Bash" if "Bash" in self.offered_tools else "PowerShell"
            tool_id = f"toolu_{len(self.calls) + 1:02d}"
            self.calls.append((tool_id, tool, tool_input))
            block = {"type": "tool_use", "id": tool_id, "name": tool, "input": tool_input}
            return self._message([block], "tool_use", model), bool(body.get("stream"))

    def prepare_step(self):
        """出下一步之前（刚收好这次带回来的工具结果）：剧本可以按结果改下一步（见 try_browser.py）。"""

    @property
    def results(self):
        """[(tool_use_id, 出错没有, 文字)]，按交给模型的先后。"""
        return [(tool_id, error, text) for tool_id, (error, text) in self.by_id.items()]

    def result_of(self, call_index):
        """第几次工具调用的结果 (出错没有, 文字)；没交给模型就是 (None, "（没交给模型）")。"""
        return self.by_id.get(self.calls[call_index][0], (None, "（没交给模型）"))

    def _message(self, content, stop_reason, model, usage=None):
        return {"id": "msg_fake", "type": "message", "role": "assistant", "model": model,
                "content": content, "stop_reason": stop_reason, "stop_sequence": None, "usage": dict(usage or self.usage)}

    def note_request(self, method, path, headers, body):
        auth = ("bearer" if (headers.get("Authorization") or "").startswith("Bearer ")
                else "x-api-key" if headers.get("x-api-key") else "")
        with self.lock:
            self.requests.append((method, path, (body or {}).get("model"), auth))


def sse(message):
    """一条完整的消息 → 流式的 SSE 事件（Claude Code 用 stream=true 请求）。"""
    def event(name, data):
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    head = {**message, "content": [], "stop_reason": None}
    out = [event("message_start", {"type": "message_start", "message": head})]
    for i, block in enumerate(message["content"]):
        if block["type"] == "text":
            out.append(event("content_block_start", {"type": "content_block_start", "index": i,
                                                     "content_block": {"type": "text", "text": ""}}))
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "text_delta", "text": block["text"]}}))
        else:
            out.append(event("content_block_start", {"type": "content_block_start", "index": i,
                                                     "content_block": {**block, "input": {}}}))
            out.append(event("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(block["input"], ensure_ascii=False)}}))
        out.append(event("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(event("message_delta", {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"],
                                                                          "stop_sequence": None},
                                       "usage": {"output_tokens": message["usage"].get("output_tokens", 5)}}))
    out.append(event("message_stop", {"type": "message_stop"}))
    return "".join(out).encode("utf-8")


def serve(api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            api.note_request("POST", self.path, self.headers, body)
            reply, stream = api.handle(self.path, body)
            data = sse(reply) if stream else json.dumps(reply).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            api.note_request("GET", self.path, self.headers, None)
            data = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            if not isinstance(sys.exc_info()[1], ConnectionResetError):   # Claude Code 退出时断开连接：正常
                super().handle_error(request, client_address)

    server = Server(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def make_brain(server, tmp, workspace, emit, resume="", provider=None, model="", compact_tokens=0, notebook=False,
               withdraw=""):
    """一个连到假接口的 ClaudeBrain：数据目录都在 tmp 里，工作文件夹是 workspace（"" = 只聊天）；resume：接着哪段对话
    （withdraw：并且扔掉它的最后一问——那一问的指纹）。
    provider：照哪家服务商的样子（地址换成假接口）；不给就是 Claude 官方那样。model：空 = 那家的默认模型。
    compact_tokens：对话超过多少自动整理（0 = 不整理，免得别的剧本被整理打乱）；notebook：用不用记事本（同理默认不用）。"""
    claude_brain.CONFIG_DIR = Path(tmp) / "claude"
    claude_brain.WORKSPACE = Path(tmp) / "empty"
    memory.memory_path = lambda: Path(tmp) / "memory.json"   # 记事本也在临时文件夹里（不碰你真的记事本）
    rapport.rapport_path = lambda: Path(tmp) / "rapport.json"                 # 相处笔记、回忆也一样（11.3、11.4）
    recollections.recollections_path = lambda: Path(tmp) / "recollections.json"
    archive.archive_path = lambda: Path(tmp) / "archive.sqlite"               # 原文存档（12.1）
    base = provider or Provider(code="fake", models=("claude-haiku-4-5",), prices={"claude-haiku-4-5": (1, 5)})
    PROVIDERS["fake"] = replace(base, code="fake", base_url=f"http://127.0.0.1:{server.server_address[1]}")
    settings = AssistantSettings(provider="fake", model=model, workspace=str(workspace) if workspace else "",
                                 budget_usd=10, conversation_budget_usd=50, compact_tokens=compact_tokens,
                                 notebook=notebook)
    return claude_brain.ClaudeBrain(settings, "sk-fake-not-a-real-key", "zh", emit, resume=resume, withdraw=withdraw)
