"""开发用：真的 Claude Code + 假的 Anthropic 接口 + 真的 Edge（不显示窗口、临时配置文件夹），验证芸芸的浏览器（阶段 6.2a）。
不花钱、不联网（网页是本机临时起的小网站）、不碰你的 Edge。

    .\\.venv\\Scripts\\python tools\\try_browser.py

  剧本：browser_open 一个表单页（里面预先填好了名字、密码、卡号）→ browser_read → browser_open file:// （权限规则拒绝）→
  browser_open 第二页 → browser_back。看：都不用问；交给模型的快照里有名字、**没有密码和卡号**；回到了表单页。
  然后像点了"停止"那样结束作业里的命令进程（proctree.stop_commands）：浏览器还活着、还能读。
"""
import asyncio
import shutil
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import proctree, protocol

FORM = """<html><head><meta charset="utf-8"><title>表单页</title></head><body>
<h1>报名</h1>
<label>名字 <input name="name" value="张三"></label>
<label>密码 <input type="password" name="pw" value="hunter2-secret"></label>
<input name="card" autocomplete="cc-number" aria-label="卡号" value="4111111111111111">
<a href="page2.html">下一页</a></body></html>"""
PAGE2 = """<html><head><meta charset="utf-8"><title>第二页</title></head><body><p>这里是第二页</p></body></html>"""


class ResultApi(FakeApi):
    """按工具调用的编号记下交给模型的每个工具结果：{tool_use_id: (出错没有, 文字)}。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.by_id = {}

    def handle(self, path, body):
        for message in body.get("messages") or []:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    text = str(content).split("<system-reminder>")[0].strip()
                    self.by_id.setdefault(block["tool_use_id"], (bool(block.get("is_error")), text))
        return super().handle(path, body)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def web_server(folder):
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-browser-"))
    site = tmp / "site"
    site.mkdir()
    (site / "form.html").write_text(FORM, encoding="utf-8")
    (site / "page2.html").write_text(PAGE2, encoding="utf-8")
    web = web_server(site)
    base = f"http://127.0.0.1:{web.server_address[1]}"
    api = ResultApi([("tool", protocol.BROWSER_OPEN_TOOL, {"url": f"{base}/form.html"}),
                     ("tool", protocol.BROWSER_READ_TOOL, {}),
                     ("tool", protocol.BROWSER_OPEN_TOOL, {"url": "file:///C:/Windows/win.ini"}),
                     ("tool", protocol.BROWSER_OPEN_TOOL, {"url": f"{base}/page2.html"}),
                     ("tool", protocol.BROWSER_BACK_TOOL, {}),
                     ("text", "看完了")])
    server = serve(api)
    events, brain, survived = [], None, None
    try:
        brain = make_brain(server, tmp, "", events.append)
        brain.browser.profile_dir = tmp / "browser"   # 临时配置文件夹，不显示窗口
        brain.browser.headless = True
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("打开那个表单看看"), 120)
        killed = proctree.stop_commands([])           # 像点了"停止"：作业里的命令进程都结束，浏览器要留着
        try:
            _, title, _ = await asyncio.wait_for(brain.browser.read(), 20)
            survived = (killed, title)
        except Exception as e:
            survived = (killed, f"没了：{e}")
    finally:
        if brain is not None:
            await brain.close()
        server.shutdown()
        web.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    offered = sorted(t for t in api.offered_tools or [] if "browser" in t)
    decisions = [(e.tool.rsplit("__", 1)[-1], e.decision) for e in events if isinstance(e, protocol.ToolDecision)]
    asked = [e for e in events if isinstance(e, protocol.PermissionRequest)]
    changes = [e.open for e in events if isinstance(e, protocol.BrowserChanged)]
    results = [api.by_id.get(tool_id, (None, "（没交给模型）")) for tool_id, _, _ in api.calls]
    print("工具", offered)
    print("操作记录", decisions, "；问了", len(asked), "次；浏览器开关", changes)
    for error, text in results:
        print("   结果", "✗" if error else "✓", text[:300].replace("\n", " | "))
    print("停止之后：结束了", survived[0] if survived else "?", "个进程；浏览器还能读：", survived and survived[1])

    first = results[0][1] if results else ""
    checks = {
        "三个工具都有": offered == sorted([protocol.BROWSER_OPEN_TOOL, protocol.BROWSER_READ_TOOL, protocol.BROWSER_BACK_TOOL]),
        "都不用问、file:// 被拒": decisions == [("browser_open", "auto"), ("browser_read", "auto"),
                                            ("browser_open", "blocked"), ("browser_open", "auto"),
                                            ("browser_back", "auto")] and not asked,
        "读到了表单": "Title: 表单页" in first and "张三" in first,
        "密码和卡号遮掉了": all("hunter2" not in t and "4111" not in t for _, t in results) and "(hidden)" in first,
        "后退回到表单": len(results) == 5 and "Title: 表单页" in results[4][1],
        "停止后浏览器还在": bool(survived) and survived[1] == "表单页",
        "关助手时关浏览器": changes == [True, False],
    }
    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 芸芸的浏览器能用（看网页不用问、只开 http(s)、密码卡号不给模型看、停止时浏览器留着）"
          if all(checks.values()) else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
