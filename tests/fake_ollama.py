"""测试用的假 Ollama（不是测试文件，被 test_ollama_service.py 当成"ollama serve"启动）。

像真的 Ollama 一样：在 OLLAMA_HOST 上回应 /api/version，并开一个子进程（真的 Ollama 加载模型时会开 llama-server.exe）。
子进程的 PID 写进 FAKE_OLLAMA_CHILD_PID 指定的文件，测试用它检查"关掉时子进程有没有一起结束"。
"""
import http.server
import json
import os
import subprocess
import sys

host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
with open(os.environ["FAKE_OLLAMA_CHILD_PID"], "w") as f:
    f.write(str(child.pid))


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"version": "fake"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass   # 不打印访问记录


http.server.HTTPServer((host, int(port)), Handler).serve_forever()
