"""开发用：本机的假 Anthropic 接口（/v1/messages，流式 SSE），让真的 Claude Code 按剧本调用工具。不花钱、不联网。

tools/try_permissions.py、tools/try_rewind.py 用它验证"只有真的 Claude Code 才知道"的行为（权限关卡、撤销……）：

    api = FakeApi([("tool", "Read", {...}), ("text", "做完了")])   # 剧本：主循环每来一次请求，就按顺序回一步
    server = serve(api)
    brain = make_brain(server, tmp, workspace, emit)                # ClaudeBrain，服务商地址指到假接口，Key 是假的
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant import claude_brain  # noqa: E402
from yunyun.assistant.providers import PROVIDERS, Provider  # noqa: E402
from yunyun.assistant.settings import AssistantSettings  # noqa: E402

SHELL = "<shell>"   # 剧本里的"执行命令"：Claude Code 给的是 Bash 就用 Bash，给的是 PowerShell 就用 PowerShell


class FakeApi:
    """假的 Anthropic Messages 接口。主循环（系统提示词里有芸芸的人设）按剧本回答；别的请求（起标题之类）随便答一句。"""

    def __init__(self, steps):
        self.steps = list(steps)
        self.offered_tools = None   # Claude Code 第一次请求时带的工具列表
        self.results = []           # 每次主循环请求带回来的 tool_result：(tool_use_id, 出错没有, 内容开头)
        self.calls = []             # 每一步实际发出的工具调用：(id, 工具, 参数)
        self.others = []            # 别的请求：(路径, 模型)
        self.lock = threading.Lock()

    def handle(self, path, body):
        if path.endswith("/count_tokens"):
            return {"input_tokens": 100}, False
        system = json.dumps(body.get("system", ""), ensure_ascii=False)
        if "/v1/messages" not in path or "芸芸" not in system:
            self.others.append((path, body.get("model")))
            return self._message([{"type": "text", "text": "ok"}], "end_turn"), bool(body.get("stream"))
        with self.lock:
            if self.offered_tools is None:
                self.offered_tools = [t.get("name") for t in body.get("tools", [])]
            last = body["messages"][-1]
            for block in last.get("content", []) if isinstance(last.get("content"), list) else []:
                if block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    self.results.append((block["tool_use_id"], bool(block.get("is_error")), str(content)[:160]))
            step = self.steps.pop(0) if self.steps else ("text", "（剧本演完了）")
            if step[0] == "text":
                return self._message([{"type": "text", "text": step[1]}], "end_turn"), bool(body.get("stream"))
            _, tool, tool_input = step
            if tool == SHELL:
                tool = "Bash" if "Bash" in self.offered_tools else "PowerShell"
            tool_id = f"toolu_{len(self.calls) + 1:02d}"
            self.calls.append((tool_id, tool, tool_input))
            block = {"type": "tool_use", "id": tool_id, "name": tool, "input": tool_input}
            return self._message([block], "tool_use"), bool(body.get("stream"))

    @staticmethod
    def _message(content, stop_reason):
        return {"id": "msg_fake", "type": "message", "role": "assistant", "model": "claude-haiku-4-5",
                "content": content, "stop_reason": stop_reason, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5}}


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
                                       "usage": {"output_tokens": 5}}))
    out.append(event("message_stop", {"type": "message_stop"}))
    return "".join(out).encode("utf-8")


def serve(api):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            reply, stream = api.handle(self.path, body)
            data = sse(reply) if stream else json.dumps(reply).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
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


def make_brain(server, tmp, workspace, emit):
    """一个连到假接口的 ClaudeBrain：数据目录都在 tmp 里，工作文件夹是 workspace。"""
    claude_brain.CONFIG_DIR = Path(tmp) / "claude"
    claude_brain.WORKSPACE = Path(tmp) / "empty"
    PROVIDERS["fake"] = Provider(code="fake", models=("claude-haiku-4-5",), prices={"claude-haiku-4-5": (1, 5)},
                                 base_url=f"http://127.0.0.1:{server.server_address[1]}")
    settings = AssistantSettings(provider="fake", workspace=str(workspace), budget_usd=10, conversation_budget_usd=50)
    return claude_brain.ClaudeBrain(settings, "sk-fake-not-a-real-key", "zh", emit)
