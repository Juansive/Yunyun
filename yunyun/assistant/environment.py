"""子进程启动前的环境准备：清掉继承来的变量、找系统代理、不弹控制台窗口。

助手子进程（claude_brain）和"测试连接"的小进程（check）都要用，所以单独放在这里：不 import Qt，也不 import SDK。
这些做法都是阶段 0 实测得出的（见 docs/助手模式计划.md）。
"""
import os
import subprocess
import urllib.request

_INHERITED_PREFIXES = ("ANTHROPIC_", "CLAUDE_", "MCP_")


def clean_environment(environ=None):
    """去掉从父进程继承来的 Claude 相关环境变量，返回删掉的名字（不记值）。

    在 Claude 桌面版里开发时，环境里有 30 个这样的变量，其中 ANTHROPIC_BASE_URL 会把请求导到桌面版的通道上；
    官方 SDK 也会读 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 这些变量。所以启动前一律清掉，只用芸芸自己给的。
    """
    environ = os.environ if environ is None else environ
    names = [k for k in environ if k.upper().startswith(_INHERITED_PREFIXES) or k.upper() == "CLAUDECODE"]
    for name in names:
        del environ[name]
    return names


def system_proxy_env(environ=None, getproxies=urllib.request.getproxies):
    """用户只开了 Windows 的系统代理时，把它变成 HTTPS_PROXY（claude.exe 和官方 SDK 都不会自己去读系统代理）。

    代理软件用 TUN 模式时（虚拟网卡，所有程序自动走代理），系统代理开关是关的，这里返回空的。
    """
    environ = os.environ if environ is None else environ
    if any(environ.get(name) for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")):
        return {}   # 已经设了，子进程会继承
    proxies = getproxies()   # Windows 上会读注册表里的系统代理（开关打开时才有）
    url = proxies.get("https") or proxies.get("http")
    if not url:
        return {}
    if "://" not in url:
        url = "http://" + url
    return {"HTTPS_PROXY": url, "NO_PROXY": "localhost,127.0.0.1,::1"}


def hide_child_consoles():
    """让 SDK 启动的子进程（claude.exe）不建控制台窗口；claude.exe 再启动的 git.exe 会继承这个看不见的控制台。

    不加的话，打包后的芸芸（没有控制台）一打开助手模式就会弹出 Windows Terminal 窗口（0.7 实测）。
    SDK 没给改启动参数的地方，所以把它用的 anyio.open_process 包一层（只影响助手子进程自己）。重复调用没关系。
    """
    import anyio
    if getattr(anyio.open_process, "yunyun_hidden", False):
        return
    original = anyio.open_process
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    async def open_process_hidden(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | no_window
        return await original(*args, **kwargs)

    open_process_hidden.yunyun_hidden = True
    anyio.open_process = open_process_hidden


def prepare_process(environ=None):
    """助手子进程启动 Claude Code 之前调用一次。"""
    environ = os.environ if environ is None else environ
    removed = clean_environment(environ)
    environ["CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK"] = "1"   # SDK 读的是本进程的环境变量；检查版本有时要多花 2～3 秒
    hide_child_consoles()
    return removed
