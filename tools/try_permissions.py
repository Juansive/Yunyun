"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证权限关卡（claude_brain.PermissionGate）真的管住了每一次工具调用。

    .\\.venv\\Scripts\\python tools\\try_permissions.py [--keep]     # --keep：留下临时文件夹看看

**不花钱、不联网**：本机起一个假的 /v1/messages，按剧本让"模型"一步步调用工具；ClaudeBrain 的服务商地址指到它，Key 是假的。
所有东西都在一个新的临时文件夹里：工作文件夹 proj\\（有 hello.py）、外面的 other\\（有 secret.txt）、Claude Code 的数据目录。
不碰芸芸项目、不碰你的文件、不读你的 Key。

确认请求由脚本按剧本自动回答（模拟用户点按钮），最后打印每一步：有没有问、怎么答的、Claude Code 实际做了什么。
"""
import asyncio
import json
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant import claude_brain, protocol  # noqa: E402
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


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-permissions-"))
    proj, other = tmp / "proj", tmp / "other"
    proj.mkdir()
    other.mkdir()
    (proj / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    (other / "secret.txt").write_text("不该被读到的内容\n", encoding="utf-8")
    claude_brain.CONFIG_DIR = tmp / "claude"
    claude_brain.WORKSPACE = tmp / "empty"

    # 剧本：(步骤说明, 剧本里的调用, 期望：问不问, 问的话怎么答)
    plan = [
        ("读工作文件夹里的文件", ("tool", "Read", {"file_path": str(proj / "hello.py")}), None),
        ("读外面的文件", ("tool", "Read", {"file_path": str(other / "secret.txt")}), "deny"),
        ("改工作文件夹里的文件", ("tool", "Edit", {"file_path": str(proj / "hello.py"), "old_string": "print('hi')",
                                         "new_string": "print('hello')"}), "allow"),
        ("执行命令（选这类都允许）", ("tool", SHELL, {"command": "echo first", "description": "say"}), "remember"),
        ("同一类命令", ("tool", SHELL, {"command": "echo second", "description": "say"}), None),
        ("危险命令", ("tool", SHELL, {"command": "rm hello.py", "description": "delete"}), "deny"),
        ("写外面的文件", ("tool", "Write", {"file_path": str(other / "new.txt"), "content": "x"}), "deny"),
        ("搜索工作文件夹", ("tool", "Glob", {"pattern": "**/*.py"}), None),
        ("任务工具（没开）", ("tool", "Task", {"description": "x", "prompt": "y", "subagent_type": "general-purpose"}),
         None),
    ]
    api = FakeApi([step for _, step, _ in plan] + [("text", "做完了")])
    server = serve(api)
    PROVIDERS["fake"] = Provider(code="fake", models=("claude-haiku-4-5",), prices={"claude-haiku-4-5": (1, 5)},
                                 base_url=f"http://127.0.0.1:{server.server_address[1]}")
    settings = AssistantSettings(provider="fake", workspace=str(proj), budget_usd=10, conversation_budget_usd=50)

    loop = asyncio.get_running_loop()
    requests = []   # (第几次请求, 事件, 回答)
    answers = iter(answer for _, _, answer in plan if answer)
    brain = None

    def emit(event):
        if isinstance(event, protocol.PermissionRequest):
            answer = next(answers, "deny")
            reply = protocol.PermissionReply(event.request_id, answer != "deny", "剧本：拒绝" if answer == "deny" else "",
                                             answer == "remember")
            requests.append((event, answer))
            loop.call_soon(brain.reply_permission, reply)
        elif isinstance(event, protocol.Error):
            print("出错：", event)

    brain = claude_brain.ClaudeBrain(settings, "sk-fake-not-a-real-key", "zh", emit)
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("按剧本做"), 120)
    finally:
        await brain.close()
        server.shutdown()

    expected_asks = [answer for _, _, answer in plan if answer]
    print("结论：", "✓ 和规则一致" if [answer for _, answer in requests] == expected_asks
          and (proj / "hello.py").read_text(encoding="utf-8").strip() == "print('hello')"
          and not (other / "new.txt").exists() else "✗ 和规则不一致，看下面的明细")
    print("Claude Code 给模型的工具：", api.offered_tools)
    print("别的请求：", sorted(set(api.others)))
    print("确认请求：")
    for event, answer in requests:
        print(f"  {event.tool:10} kind={event.kind:14} 都允许={'可' if event.can_remember else '否'} "
              f"危险={'是' if event.danger else '否'} scope={event.scope!r} → 剧本答：{answer}")
    print("每一步的结果（Claude Code 发回模型的 tool_result）：")
    results = {tool_id: (err, text) for tool_id, err, text in api.results}
    for (label, _, _), (tool_id, tool, _) in zip(plan, api.calls):
        err, text = results.get(tool_id, (None, "（没有结果）"))
        print(f"  {label:14} {tool:10} {'出错/拒绝' if err else '成功'}：{text[:90]!r}")
    print("磁盘上：hello.py =", (proj / "hello.py").read_text(encoding="utf-8").strip() if (proj / "hello.py").exists()
          else "（被删了！）", "；other/new.txt 存在：", (other / "new.txt").exists())
    stderr = list(brain._stderr)
    if stderr:
        print("Claude Code 的错误输出（最后几行）：", " / ".join(stderr[-5:]))
    if "--keep" in sys.argv:
        print("临时文件夹（留着看）：", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
