"""开发用：真的 Claude Code + 假的 Anthropic 接口 + 真的 Edge（不显示窗口、临时配置文件夹），验证芸芸的浏览器（阶段 6.2）。
不花钱、不联网（网页是本机临时起的小网站）、不碰你的 Edge。

    .\\.venv\\Scripts\\python tools\\try_browser.py

  6.2a 看网页：browser_open 表单页（预先填好了名字、密码、卡号）→ browser_read → browser_open file://（拒绝）→
  browser_open 第二页 → browser_back。都不用问；快照里有名字、**没有密码和卡号**。
  6.2b 点击、输入：输入名字（问你，假装选了"这个网站都允许"）→ 往密码框输入（直接拒绝）→ 选城市（不用再问）→
  在搜索框输入并回车（搜索不算提交，不用问）→ 后退 → 点"提交订单"（标红、不给"都允许"，假装允许一次）→ 后退 →
  点"新窗口打开"（跟到新标签页）。剧本里的编号写成 "?名字"，由假接口从上一份快照里现找。
  6.2c 下载（存到临时的"下载"文件夹）：用普通点击点下载链接（拒绝，叫它用下载工具）→ 点"导出"按钮（脚本触发的下载，
  看不出来：点了，下载被取消，告诉模型）→ browser_download 导出、下载说明（每次问、不给都允许，存好了）→
  最后模拟你自己在窗口里点下载（照常存、芸芸说一声）。
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
from yunyun import paths
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
<a href="page2.html">下一页</a> <a href="page2.html" target="_blank">新窗口打开</a>
<a href="files/说明.txt" download>下载说明</a>
<button onclick="const a = document.createElement('a'); a.href = 'files/report.txt'; a.download = 'report.txt';
  document.body.appendChild(a); a.click();">导出</button></body></html>"""
PAGES = {
    "form.html": FORM,
    "page2.html": '<html><head><meta charset="utf-8"><title>第二页</title></head><body><p>这里是第二页</p></body></html>',
    "search.html": '<html><head><meta charset="utf-8"><title>搜索结果</title></head><body><p>搜到了</p></body></html>',
    "done.html": '<html><head><meta charset="utf-8"><title>订单提交了</title></head><body><p>谢谢</p></body></html>',
}


class ScriptApi(FakeApi):
    """剧本里的 "?名字" 编号从最近一份快照里现找（导航以后编号会变）。"""

    def prepare_step(self):
        if self.steps and self.steps[0][0] == "tool" and str(self.steps[0][2].get("ref", "")).startswith("?"):
            kind, tool, tool_input = self.steps[0]
            snapshot = next((text for _, text in reversed(self.by_id.values()) if "[ref=" in text), "")
            found = re.search(rf'"{re.escape(tool_input["ref"][1:])}"[^\n]*?\[ref=(\w+)\]', snapshot)
            self.steps[0] = (kind, tool, {**tool_input, "ref": found.group(1) if found else "e999"})


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
    (site / "files").mkdir()
    (site / "files" / "说明.txt").write_text("说明的内容", encoding="utf-8")
    (site / "files" / "report.txt").write_text("report", encoding="utf-8")
    downloads = tmp / "Downloads"
    paths.downloads_dir = lambda: downloads   # 不往你真的"下载"文件夹里放东西
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
        # 6.2c 下载
        ("tool", B.BROWSER_CLICK_TOOL, {"ref": "?下载说明"}),
        ("tool", B.BROWSER_CLICK_TOOL, {"ref": "?导出"}),
        ("tool", B.BROWSER_DOWNLOAD_TOOL, {"ref": "?导出"}),
        ("tool", B.BROWSER_DOWNLOAD_TOOL, {"ref": "?下载说明"}),
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
        # 你自己在窗口里点下载（不是芸芸的操作）：照常存到"下载"文件夹
        brain.browser._acted_at = 0
        form = next(p for p in brain.browser._context.pages if "form.html" in p.url)
        await form.get_by_text("下载说明").click()
        for _ in range(100):
            if any(isinstance(e, protocol.Downloaded) and e.by_user for e in events):
                break
            await asyncio.sleep(0.1)
        killed = proctree.stop_commands([])           # 像点了"停止"：作业里的命令进程都结束，浏览器要留着
        try:
            view = await asyncio.wait_for(brain.browser.read(), 20)
            survived = (killed, view.title)
        except Exception as e:
            survived = (killed, f"没了：{e}")
    finally:
        if brain is not None:
            await brain.close()
        saved = sorted(p.name for p in downloads.iterdir()) if downloads.is_dir() else []
        server.shutdown()
        web.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    offered = sorted(t for t in api.offered_tools or [] if "browser" in t)
    decisions = [(e.tool.rsplit("__", 1)[-1], e.decision) for e in events if isinstance(e, protocol.ToolDecision)]
    asked = [(e.kind, e.element, e.scope, e.can_remember, e.danger) for e in events
             if isinstance(e, protocol.PermissionRequest)]
    links = [e.link for e in events if isinstance(e, protocol.PermissionRequest)]
    changes = [e.open for e in events if isinstance(e, protocol.BrowserChanged)]
    downloaded = [(e.name, e.by_user) for e in events if isinstance(e, protocol.Downloaded)]
    results = [api.result_of(i) for i in range(len(api.calls))]
    print("工具", [t.rsplit("__", 1)[-1] for t in offered])
    print("操作记录", decisions)
    print("问了", asked, "；浏览器开关", changes)
    for (_, tool, tool_input), (error, text) in zip(api.calls, results):
        print("  ", tool.rsplit("__", 1)[-1], tool_input, "✗" if error else "✓", text[:160].replace("\n", " | "))
    print("停止之后：结束了", survived[0] if survived else "?", "个进程；浏览器还能读：", survived and survived[1])
    print("下载好了", downloaded, "；\"下载\"文件夹里", saved, "；下载卡片上的链接", links[2:])

    texts = [t for _, t in results]
    title = [re.search(r"Title: (.*)", t).group(1) if "Title: " in t else "" for t in texts]
    checks = {
        "七个工具都有": len(offered) == 7,
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
        "普通点击点下载链接被拒": decisions[12] == ("browser_click", "blocked") and "browser_download" in texts[12],
        "脚本触发的下载先取消、告诉模型": decisions[13] == ("browser_click", "granted") and "cancelled" in texts[13]
                                       and "report.txt" in texts[13],
        "下载工具每次问、存好了": decisions[14:16] == [("browser_download", "allowed")] * 2
                                and 'Downloaded "report.txt"' in texts[14] and 'Downloaded "说明.txt"' in texts[15],
        "新标签页跟过去": decisions[16] == ("browser_click", "granted") and title[16] == "第二页" and "new tab" in texts[16],
        "确认卡片看到的是芸芸自己看的元素": asked == [("web_type", 'textbox "名字"', "127.0.0.1", True, False),
                                         ("web_click", 'button "提交订单"', "127.0.0.1", False, True),
                                         ("web_download", 'button "导出"', "127.0.0.1", False, True),
                                         ("web_download", 'link "下载说明"', "127.0.0.1", False, True)],
        "下载卡片写着链接": links[2] == "" and links[3].endswith(".txt"),
        "你自己点的下载照常存": downloaded == [("report.txt", False), ("说明.txt", False), ("说明 (1).txt", True)]
                               and saved == ["report.txt", "说明 (1).txt", "说明.txt"],
        "停止后浏览器还在": bool(survived) and survived[1] == "第二页",
        "关助手时关浏览器": changes == [True, False],
    }
    print("\n" + "；".join(f"{k} {'✓' if v else '✗'}" for k, v in checks.items()))
    print("结论：", "✓ 芸芸的浏览器能用（看网页不用问；点击输入要问、能按网站都允许；提交标红每次问；"
                    "密码卡号不看也不填；新标签页跟过去；下载每次问、存到下载文件夹；停止时浏览器留着）"
          if all(checks.values()) else "✗ 有问题，看上面")


if __name__ == "__main__":
    asyncio.run(main())
