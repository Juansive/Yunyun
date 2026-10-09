"""开发用：真的 Claude Code + 假的 Anthropic 接口 + 真的 Edge（不显示窗口、临时配置文件夹），验证芸芸的浏览器（阶段 6.2）。
不花钱、不联网（网页是本机临时起的小网站）、不碰你的 Edge。

    .\\.venv\\Scripts\\python tools\\try_browser.py

  6.2a 看网页：browser_open 表单页（预先填好了名字、密码、卡号）→ browser_read → browser_open file://（拒绝）→
  browser_open 第二页 → browser_back。都不用问；快照里有名字、**没有密码和卡号**。
  6.2b 点击、输入：输入名字（问你，假装选了"这个网站都允许"）→ 往密码框输入（直接拒绝）→ 选城市（不用再问）→
  在搜索框输入并回车（搜索不算提交，不用问）→ 后退 → 点"提交订单"（标红、不给"都允许"，假装允许一次）→ 后退 →
  点"新窗口打开"（跟到新标签页）。剧本里的编号写成 "?名字"，由假接口从上一份快照里现找。
  最后像点了"停止"那样结束作业里的命令进程（proctree.stop_commands）：浏览器还活着、还能读。
"""
import asyncio
import re
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
<form role="search" action="search.html"><input name="q" aria-label="搜索"><button>搜索</button></form>
<form action="done.html">
<label>名字 <input name="name" value="张三"></label>
<label>密码 <input type="password" name="pw" value="hunter2-secret"></label>
<input name="card" autocomplete="cc-number" aria-label="卡号" value="4111111111111111">
<select name="city" aria-label="城市"><option>北京</option><option>上海</option></select>
<button type="submit">提交订单</button>
</form>
<a href="page2.html">下一页</a> <a href="page2.html" target="_blank">新窗口打开</a></body></html>"""
PAGES = {
    "form.html": FORM,
    "page2.html": '<html><head><meta charset="utf-8"><title>第二页</title></head><body><p>这里是第二页</p></body></html>',
    "search.html": '<html><head><meta charset="utf-8"><title>搜索结果</title></head><body><p>搜到了</p></body></html>',
    "done.html": '<html><head><meta charset="utf-8"><title>订单提交了</title></head><body><p>谢谢</p></body></html>',
}


class ScriptApi(FakeApi):
    """记下交给模型的每个工具结果（{tool_use_id: (出错没有, 文字)}）；剧本里的 "?名字" 编号从最近一份快照里现找。"""

    def __init__(self, steps):
        super().__init__(steps)
        self.by_id = {}
        self.last_snapshot = ""

    def handle(self, path, body):
        for message in body.get("messages") or []:
            for block in message.get("content") if isinstance(message.get("content"), list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    text = str(content).split("<system-reminder>")[0].strip()
                    if block["tool_use_id"] not in self.by_id:
                        self.by_id[block["tool_use_id"]] = (bool(block.get("is_error")), text)
                        if "[ref=" in text:
                            self.last_snapshot = text
        with self.lock:
            if self.steps and self.steps[0][0] == "tool" and str(self.steps[0][2].get("ref", "")).startswith("?"):
                kind, tool, tool_input = self.steps[0]
                name = tool_input["ref"][1:]
                found = re.search(rf'"{re.escape(name)}"[^\n]*?\[ref=(\w+)\]', self.last_snapshot)
                self.steps[0] = (kind, tool, {**tool_input, "ref": found.group(1) if found else "e999"})
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
    for name, html in PAGES.items():
        (site / name).write_text(html, encoding="utf-8")
    web = web_server(site)
    base = f"http://127.0.0.1:{web.server_address[1]}"
    B = protocol
    api = ScriptApi([
        # 6.2a 看网页
        ("tool", B.BROWSER_OPEN_TOOL, {"url": f"{base}/form.html"}),
        ("tool", B.BROWSER_READ_TOOL, {}),
        ("tool", B.BROWSER_OPEN_TOOL, {"url": "file:///C:/Windows/win.ini"}),
        ("tool", B.BROWSER_OPEN_TOOL, {"url": f"{base}/page2.html"}),
        ("tool", B.BROWSER_BACK_TOOL, {}),
        # 6.2b 点击、输入
        ("tool", B.BROWSER_TYPE_TOOL, {"ref": "?名字", "text": "李四"}),
        ("tool", B.BROWSER_TYPE_TOOL, {"ref": "?密码", "text": "hunter2"}),
        ("tool", B.BROWSER_SELECT_TOOL, {"ref": "?城市", "values": ["上海"]}),
        ("tool", B.BROWSER_TYPE_TOOL, {"ref": "?搜索", "text": "猫", "submit": True}),
        ("tool", B.BROWSER_BACK_TOOL, {}),
        ("tool", B.BROWSER_CLICK_TOOL, {"ref": "?提交订单"}),
        ("tool", B.BROWSER_BACK_TOOL, {}),
        ("tool", B.BROWSER_CLICK_TOOL, {"ref": "?新窗口打开"}),
        ("text", "做完了"),
    ])
    server = serve(api)
    loop = asyncio.get_running_loop()
    events, brain, survived = [], None, None

    def emit(event):
        events.append(event)
        if isinstance(event, protocol.PermissionRequest):   # 能"都允许"就选"都允许"，不能就允许一次
            loop.call_soon(brain.reply_permission,
                           protocol.PermissionReply(event.request_id, True, "", event.can_remember))

    try:
        brain = make_brain(server, tmp, "", emit)
        brain.browser.profile_dir = tmp / "browser"   # 临时配置文件夹，不显示窗口
        brain.browser.headless = True
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("打开那个表单，帮我填好提交"), 180)
        killed = proctree.stop_commands([])           # 像点了"停止"：作业里的命令进程都结束，浏览器要留着
        try:
            view = await asyncio.wait_for(brain.browser.read(), 20)
            survived = (killed, view.title)
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
    asked = [(e.kind, e.element, e.scope, e.can_remember, e.danger) for e in events
             if isinstance(e, protocol.PermissionRequest)]
    changes = [e.open for e in events if isinstance(e, protocol.BrowserChanged)]
    results = [api.by_id.get(tool_id, (None, "（没交给模型）")) for tool_id, _, _ in api.calls]
    print("工具", [t.rsplit("__", 1)[-1] for t in offered])
    print("操作记录", decisions)
    print("问了", asked, "；浏览器开关", changes)
    for (_, tool, tool_input), (error, text) in zip(api.calls, results):
        print("  ", tool.rsplit("__", 1)[-1], tool_input, "✗" if error else "✓", text[:160].replace("\n", " | "))
    print("停止之后：结束了", survived[0] if survived else "?", "个进程；浏览器还能读：", survived and survived[1])

    texts = [t for _, t in results]
    title = [re.search(r"Title: (.*)", t).group(1) if "Title: " in t else "" for t in texts]
    checks = {
        "六个工具都有": len(offered) == 6,
        "6.2a 看网页都不用问、file:// 被拒": decisions[:5] == [("browser_open", "auto"), ("browser_read", "auto"),
                                                           ("browser_open", "blocked"), ("browser_open", "auto"),
                                                           ("browser_back", "auto")],
        "读到表单、名字": title[0] == "表单页" and "张三" in texts[0],
        "密码和卡号从没给模型看": all("hunter2-secret" not in t and "4111" not in t for t in texts),
        "输入名字问了、选了都允许": decisions[5] == ("browser_type", "remembered") and "李四" in texts[5],
        "密码框不填": decisions[6] == ("browser_type", "blocked") and results[6][0] and "ask the user" in texts[6],
        "同一网站不再问": decisions[7] == ("browser_select", "granted"),
        "搜索不算提交": decisions[8] == ("browser_type", "granted") and title[8] == "搜索结果",
        "提交订单标红、不给都允许": decisions[10] == ("browser_click", "allowed") and title[10] == "订单提交了",
        "新标签页跟过去": decisions[12] == ("browser_click", "granted") and title[12] == "第二页" and "new tab" in texts[12],
        "确认卡片看到的是芸芸自己看的元素": asked == [("web_type", 'textbox "名字"', "127.0.0.1", True, False),
                                         ("web_click", 'button "提交订单"', "127.0.0.1", False, True)],
        "停止后浏览器还在": bool(survived) and survived[1] == "第二页",
        "关助手时关浏览器": changes == [True, False],
    }
    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 芸芸的浏览器能用（看网页不用问；点击输入要问、能按网站都允许；提交标红每次问；"
                    "密码卡号不看也不填；新标签页跟过去；停止时浏览器留着）"
          if all(checks.values()) else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
