"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证旁观模式（阶段 8 + 8.6a）的 hook 在这台电脑上真的能跑。不花钱。

    .\\.venv\\Scripts\\python tools\\try_watch.py

  - 用 watch/hooks.connect() 往一个**临时的**设置文件里加芸芸的 hook（就是真正会写进 ~/.claude/settings.json 的那几条）：
    放在这次 Claude Code 的配置文件夹（CLAUDE_CONFIG_DIR，临时的）里当"用户设置"，只让它读这一份（不碰你真的设置）。
    10-16：以前用 --settings 交给它，配合芸芸的 setting_sources=[]，新版 Claude Code 不跑里面的 hook；
    而且 Windows 上 hook 命令现在交给 PowerShell 解释，老写法 `"路径" "脚本"` 是语法错误——hooks.py 改成了"程序 + 参数"；
  - 芸芸这边开一个临时名字的通道（WatchServer），hook 脚本按 YUNYUN_WATCH_PIPE 发到这里；
  - 问一句 → 收到"在干活"（UserPromptSubmit，后台跑）→ 它答完 → Stop hook → relay.py → 通道：收到"做完了 + 项目名 + 第一句"了吗？
  - 8.6a 的回话：芸芸把第一次"做完了"先留着，回一句"接着干"（Stop 回 block）→ Claude Code 有没有把这句交给模型、接着答？
  - 8.6b 的批准：它要跑一条命令、要批准 → 芸芸收到（带着命令）、过一会儿回"允许" → 命令真的跑了吗？做完的指纹对得上吗？
    （芸芸带的 Claude Code 里，hook 和它自己的确认（芸芸助手的权限卡片）是同时进行的，谁先回答算谁——和桌面版的确认框一样）
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

from fake_anthropic_api import SHELL, FakeApi, make_brain, serve
from yunyun.assistant import claude_brain
from yunyun.watch import hooks
from yunyun.watch.server import WatchServer

FOLLOW_UP = "（芸芸转告）再跑一遍测试"


def main():
    app = QCoreApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-watch-"))
    proj = tmp / "my-project"
    proj.mkdir()
    settings = tmp / "claude" / "settings.json"   # make_brain 把 CLAUDE_CONFIG_DIR 设成 tmp/claude
    hooks.connect(settings)
    print("hook 命令：", hooks.relay_command())
    pipe = f"yunyun-watch-try-{uuid.uuid4().hex[:8]}"
    server = WatchServer(pipe)
    received = []

    def on_event(event):
        received.append((time.monotonic(), event))
        if event.event == "approval":
            server.hold(event)   # 要批准：芸芸过一会儿替你点"允许"（像你在确认卡片上点了）
            threading.Timer(0.3, lambda: replies.append((event, {"behavior": "allow"}))).start()
        if event.event == "stop" and sum(e.event == "stop" for _, e in received) == 1:
            server.hold(event)   # 第一次做完：芸芸过一会儿让它接着干（像你在绿条倒数时说了一句）
            threading.Timer(0.3, lambda: replies.append((event, {"decision": "block", "reason": FOLLOW_UP}))).start()
    replies = []
    server.event_received.connect(on_event)

    original = claude_brain.build_options

    def with_hooks(*args, **kwargs):
        options = original(*args, **kwargs)
        options.setting_sources = ["user"]               # 只读 CLAUDE_CONFIG_DIR 里那份（临时的）
        options.env["YUNYUN_WATCH_PIPE"] = pipe          # hook 脚本发到临时通道
        return options
    claude_brain.build_options = with_hooks

    api = FakeApi([("tool", SHELL, {"command": "echo yunyun-approved", "description": "跑一下测试"}),
                   ("text", "**改好了**：测试都过了。\n\n细节……"), ("text", "又跑了一遍，还是都过了。")])
    http = serve(api)
    done = {}

    def brain_thread():
        async def run():
            brain = make_brain(http, tmp, proj, lambda event: None)
            try:
                await asyncio.wait_for(brain.start(), 60)
                done["asked"] = time.monotonic()
                await asyncio.wait_for(brain.ask("帮我改一下"), 90)
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
        while replies:   # 在 Qt 的线程里回话
            server.answer(*replies.pop())
        time.sleep(0.01)
    thread.join()
    app.processEvents()
    server.close()
    http.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

    if done.get("error"):
        print("出错：", done["error"])
    start = done.get("asked", 0)
    for at, event in received:
        print(f"收到：{event.event} · 项目 {event.project} · 「{event.text}」 · 对话 {event.session[:8]} · "
              f"祖先 {len(event.ancestors)} 个，问了之后 {at - start:.2f} 秒")
    kinds = [e.event for _, e in received]
    stopped = any(e.event == "stop" and e.project == "my-project" and e.text == "改好了：测试都过了。" for _, e in received)
    followed = any(FOLLOW_UP in prompt for prompts in api.prompts_seen for prompt in prompts) and kinds.count("stop") == 2
    asked = [e for _, e in received if e.event == "approval"]
    ran = any("yunyun-approved" in text and not error for error, text in api.by_id.values())
    matched = bool(asked) and any(e.event == "busy" and e.fp == asked[0].fp for _, e in received)
    checks = [(stopped, "做完的消息到了芸芸这边"), ("busy" in kinds, "开始干活的消息也到了"),
              (followed, "芸芸回的\"接着干\"交给了模型，它接着答完又停了一次"),
              (bool(asked) and asked[0].input.get("command") == "echo yunyun-approved" and ran,
               "要批准的命令带到了芸芸这边，芸芸回\"允许\"以后命令真的跑了"),
              (matched, "命令跑完的消息带着同一个指纹（芸芸据此收起卡片）")]
    for ok, what in checks:
        print(" ", "✓" if ok else "✗", what)
    print("结论：", "✓ 都成了" if all(ok for ok, _ in checks) else "✗ 有没成的，看上面")


if __name__ == "__main__":
    main()
