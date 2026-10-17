"""测试用的假大模型服务器（不是测试文件，被 test_ai_tools.py 用）。

在本机随便一个空端口上真的开一个 HTTP 服务，像 OpenAI 兼容接口（POST /chat/completions）和 Ollama（POST /api/chat、
GET /api/version）一样回应：回答按剧本一条一条给，收到的请求都记在 requests 里。客户端走的是真的 urllib 和 JSON。

    with FakeLLMServer([回答1, (400, 错误内容), ...]) as server:
        server.url        # http://127.0.0.1:端口
        server.requests   # [{"path", "headers", "body"}]
"""
import http.server
import json
import threading


class FakeLLMServer:
    def __init__(self, replies):
        self.replies = list(replies)   # 每条是回答的 JSON，或者 (HTTP 状态码, JSON)
        self.requests = []

    def __enter__(self):
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):          # Ollama 的 /api/version：芸芸用它看 Ollama 开着没有
                self._send(200, {"version": "fake"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                reply = fake.replies.pop(0) if fake.replies else (500, {"error": "剧本里没有更多回答了"})
                self._send(*(reply if isinstance(reply, tuple) else (200, reply)))

            def _send(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass   # 不打印访问记录

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()   # 关得快
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
