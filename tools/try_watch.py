"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证旁观模式（阶段 8）的 hook 在这台电脑上真的能跑。不花钱。

    .\\.venv\\Scripts\\python tools\\try_watch.py

  - 用 watch/hooks.connect() 往一个**临时的**设置文件里加芸芸的 hook（命令就是真正会写进 ~/.claude/settings.json 的那条），
    用 --settings 交给 Claude Code（不碰你真的设置）；
  - 芸芸这边开一个临时名字的通道（WatchServer），hook 脚本按 YUNYUN_WATCH_PIPE 发到这里；
  - 问一句 → Claude Code 答完 → Stop hook → relay.py → 通道：收到"做完了 + 项目名 + 第一句"了吗？用了多久？
"""
import asyncio
import shutil
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import claude_brain
from yunyun.watch import hooks
from yunyun.watch.server import WatchServer


def main():
    app = QCoreApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-watch-"))
    proj = tmp / "my-project"
    proj.mkdir()
    settings = tmp / "settings.json"
    hooks.connect(settings)
    print("hook 命令：", hooks.relay_command())
    pipe = f"yunyun-watch-try-{uuid.uuid4().hex[:8]}"
    server = WatchServer(pipe)
    received = []
    server.event_received.connect(lambda event: received.append((time.monotonic(), event)))

    original = claude_brain.build_options

    def with_hooks(*args, **kwargs):
        options = original(*args, **kwargs)
        options.settings = str(settings)                 # 只给这次的 Claude Code 用
        options.env["YUNYUN_WATCH_PIPE"] = pipe          # hook 脚本发到临时通道
        return options
    claude_brain.build_options = with_hooks

    api = FakeApi([("text", "**改好了**：测试都过了。\n\n细节……")])
    http = serve(api)
    done = {}

    def brain_thread():
        async def run():
            brain = make_brain(http, tmp, proj, lambda event: None)
            try:
                await asyncio.wait_for(brain.start(), 60)
                done["asked"] = time.monotonic()
                await asyncio.wait_for(brain.ask("帮我改一下"), 60)
                done["answered"] = time.monotonic()
                await asyncio.sleep(3)   # Stop hook 在回答之后跑：等一会儿再关
            finally:
                await brain.close()
        try:
            asyncio.run(run())
        except Exception as e:
            done["error"] = f"{type(e).__name__}: {e}"
        done["finished"] = True

    thread = threading.Thread(target=brain_thread)
    thread.start()
    while not done.get("finished"):
        app.processEvents()
        time.sleep(0.01)
    thread.join()
    app.processEvents()
    server.close()
    http.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    if done.get("error"):
        print("出错：", done["error"])
    for at, event in received:
        print(f"收到：{event.event} · 项目 {event.project} · 「{event.text}」 · 祖先 {len(event.ancestors)} 个，"
              f"答完后 {at - done.get('answered', at):.2f} 秒")
    ok = any(e.event == "stop" and e.project == "my-project" and e.text == "改好了：测试都过了。" for _, e in received)
    print("结论：", "✓ Claude Code 跑了芸芸的 hook，做完的消息到了芸芸这边" if ok else "✗ 没收到，看上面")


if __name__ == "__main__":
    main()
