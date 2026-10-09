"""打开软件（阶段 6）：find_app 按名字找（不用问）、open_app 打开（每次问你，可以选"这次对话都允许打开它"）。

不把开始菜单里的全部软件（这台电脑上 275 个）交给模型：模型按名字找，拿到准确的名字再打开。
真正打开在芸芸进程里做（assistant/apps.py 的说明里写了为什么），这里发 OpenAppRequest、等 OpenAppReply（bridge.py）。
只打开，不操作软件里面。
"""
import asyncio
import logging

from claude_agent_sdk import tool

from yunyun.assistant import apps, protocol
from yunyun.assistant.tools.bridge import text_result as _text

log = logging.getLogger(__name__)

FIND_APP = (
    "Find apps in the user's Windows Start menu by name (part of the name is fine, any language). Returns up to "
    f"{apps.MAX_RESULTS} exact names to use with open_app. If nothing matches, try other words: the English or "
    "Chinese name (e.g. Calculator / 计算器), the product name without the company, or a shorter word."
)
FIND_APP_SCHEMA = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "Words in the app's name"}},
    "required": ["query"],
}
OPEN_APP = (
    "Open an app from the user's Start menu. name must be exactly one of the names find_app returned. "
    "Yunyun asks the user before opening it. This only starts the app: you can't see, click or type inside it."
)
OPEN_APP_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string", "description": "The exact app name from find_app"}},
    "required": ["name"],
}


def app_tools(bridge):
    """bridge：tools.bridge.Bridge。"""
    @tool("find_app", FIND_APP, FIND_APP_SCHEMA)
    async def find_app(args):
        query = str(args.get("query") or "").strip()
        if not query:
            return _text("Give some words of the app's name.", error=True)
        everything = await asyncio.to_thread(apps.list_apps)   # 第一次要读 1 秒多：别卡住别的
        if not everything:
            return _text("Couldn't read the Start menu on this computer.", error=True)
        found = list(dict.fromkeys(app.name for app in apps.search(query, everything)))   # 重名的只说一次
        log.info("找软件：%d 个结果", len(found))   # 不记找的是什么
        if not found:
            return _text(f"No app in the Start menu matches \"{query}\". Try other words.")
        return _text("Apps in the Start menu:\n" + "\n".join(found))

    @tool("open_app", OPEN_APP, OPEN_APP_SCHEMA)
    async def open_app(args):
        app = await asyncio.to_thread(apps.find_exact, str(args.get("name") or ""))
        if app is None:   # 权限规则已经挡过一次（permissions.decide），这里兜底
            return _text("There is no app with exactly this name in the Start menu. Use find_app first.", error=True)
        reply = await bridge.request(lambda request_id: protocol.OpenAppRequest(request_id, app.name, app.app_id),
                                     protocol.OpenAppReply)
        if not reply.ok:
            log.info("软件没打开：%s", reply.detail)
            return _text(f"Not opened: {reply.detail}", error=True)
        return _text(f"Opened \"{app.name}\" (it may take a few seconds to appear).")

    return [find_app, open_app]
