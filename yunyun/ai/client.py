"""最小的大模型客户端：只用标准库 urllib 发一个 HTTP 请求。

没有用 openai 之类的 SDK，是为了让你看清楚调用大模型其实就是：
    POST {base_url}/chat/completions
    请求头带 Authorization: Bearer <API Key>
    请求体是 JSON：{"model": ..., "messages": [...]}
    返回的 JSON 里，回答在 choices[0].message.content

本机的 Ollama 用它自己的接口 POST {base_url}/api/chat（见 chat_ollama），格式很像，回答在 message.content。
外部只需要调用 ask()：它按服务商的接口格式选用 chat() 或 chat_ollama()。

工具调用（邮件 agent 用，E3）：ask_reply(..., tools=[...]) 返回整个 Reply——模型可能不直接回答，而是说"我要调用某个工具"
（Reply.tool_calls）。调用方去执行，把 Reply.message 和每个工具的结果（tool_result()）接在 messages 后面再问一次，
直到模型给出回答。工具的说明两家都用 OpenAI 的格式：{"type": "function", "function": {"name", "description", "parameters"}}；
不一样的地方（参数是 JSON 字符串还是对象、结果消息怎么写）都在这里处理掉。
"""
import contextlib
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from yunyun.ai import ollama_service
from yunyun.ai.errors import AIError  # noqa: F401  别的模块习惯从这里 import AIError


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None   # 解析好的参数；模型写的不是合法的 JSON 对象时是 None（把错误当成工具结果告诉它）
    raw: str = ""            # 参数原文


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict = field(default_factory=dict)   # 这条回答本身：要接着问的话，原样放回 messages
    truncated: bool = False                       # 写到 max_tokens 被截断了


def ask(provider, api_key, model, messages, **kwargs):
    """按服务商的接口格式发一次对话，返回回答的文字（summarizer 和设置页的"测试"都用它）。"""
    return ask_reply(provider, api_key, model, messages, **kwargs).text


def ask_reply(provider, api_key, model, messages, tools=None, **kwargs):
    """和 ask() 一样，但返回整个 Reply。tools：工具的说明；模型不支持工具时抛 AIError(kind="no_tools")。"""
    if provider.api == "ollama":
        with ollama_service.session(provider.base_url):   # 需要时才启动 Ollama，用完空闲一会儿再关
            return chat_ollama_reply(provider.base_url, model, messages, extra=provider.extra, tools=tools, **kwargs)
    kwargs.setdefault("max_tokens", provider.max_tokens)
    kwargs.setdefault("timeout", 120 if provider.local else 30)   # 本地模型第一次要先读进内存，多等一会儿
    return chat_reply(provider.base_url, api_key, model, messages, extra=provider.extra,
                      use_proxy=not provider.local, tools=tools, **kwargs)   # 本机 / 局域网的模型不走系统代理


def tool_result(provider, call, content):
    """把一个工具的结果（文字）交回给模型的那条消息：OpenAI 兼容接口按 id 对上，Ollama 按工具名。"""
    if provider.api == "ollama":
        return {"role": "tool", "content": content, "tool_name": call.name}
    return {"role": "tool", "tool_call_id": call.id, "content": content}


def chat(base_url, api_key, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=30, use_proxy=True,
         usage=None):
    """OpenAI 兼容格式：发送一次对话，返回模型回答的文字。api_key 为空时不发 Key（本地模型一般不需要）。
    usage：给一个 dict 就把这次用了多少 token 加进去（评测用，见 count_usage）。"""
    return chat_reply(base_url, api_key, model, messages, extra, max_tokens, temperature, timeout, use_proxy, usage).text


def chat_reply(base_url, api_key, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=30,
               use_proxy=True, usage=None, tools=None):
    """OpenAI 兼容格式，返回 Reply。工具调用在 choices[0].message.tool_calls，参数是 JSON 字符串。"""
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,   # 越高越有创意、越低越稳定
        **(extra or {}),              # ** 把字典展开合并进来
        **({"tools": tools} if tools else {}),
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with _tool_errors(tools):
        data = _post_json(base_url.rstrip("/") + "/chat/completions", body, timeout, headers=headers, use_proxy=use_proxy)
    tokens = (data.get("usage") or {}) if isinstance(data, dict) else {}
    count_usage(usage, tokens.get("prompt_tokens"), tokens.get("completion_tokens"))
    try:
        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError) as e:
        raise AIError("返回格式不对", kind="bad_response", detail=str(data)[:300]) from e
    calls = _tool_calls(message)
    echo = {"role": "assistant", "content": text}
    if calls:
        echo["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.raw}}
                              for c in calls]
    return Reply(text, calls, echo, choice.get("finish_reason") == "length")


def _tool_calls(message):
    """回答里的工具调用。参数：OpenAI 给的是 JSON 字符串，Ollama（和个别兼容接口）给的是对象；没给 id 的按顺序编一个。"""
    calls = []
    for index, item in enumerate(message.get("tool_calls") or []):
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            raw = arguments
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                arguments = None
        else:
            raw = json.dumps(arguments or {}, ensure_ascii=False)
            arguments = arguments or {}
        calls.append(ToolCall(str(item.get("id") or f"call_{index}"), str(function.get("name") or ""),
                              arguments if isinstance(arguments, dict) else None, raw))
    return calls


@contextlib.contextmanager
def _tool_errors(tools):
    """发了工具、服务商回 400 说不支持工具（tools / function calling）：换成 kind="no_tools"，提示换个模型。"""
    try:
        yield
    except AIError as e:
        if tools and e.status == 400 and re.search(r"tool|function", e.detail or "", re.IGNORECASE):
            e.kind = "no_tools"
        raise


def count_usage(usage, prompt_tokens, completion_tokens):
    """把一次调用的 token 数加进 usage（{"input", "output", "calls"}）；usage 是 None 就不记。服务商没报 token 数时按 0 算。"""
    if usage is None:
        return
    usage["input"] = usage.get("input", 0) + (prompt_tokens or 0)
    usage["output"] = usage.get("output", 0) + (completion_tokens or 0)
    usage["calls"] = usage.get("calls", 0) + 1


# 本地模型最多用四分之一的 CPU 线程（至少 2 个）。实测（20 线程的笔记本，只用 CPU）：
# Ollama 默认线程数时每封约 0.6 秒、整台电脑 CPU 约 47%；限制到 4 线程每封约 0.8 秒、CPU 只有约 20%。
# 慢一点点，换来用户正在做的事不被拖卡。有独立显卡时推理在显卡上，这个设置几乎没影响。
OLLAMA_THREADS = max(2, (os.cpu_count() or 4) // 4)
# 每次请求都要带上同样的运行参数：参数一变，Ollama 会把模型卸载再重新加载
OLLAMA_OPTIONS = {"num_thread": OLLAMA_THREADS}


def load_ollama_model(provider, model, timeout=120):
    """只把模型读进内存、不生成任何内容（POST /api/generate 不带 prompt）。用来提前准备，见 ai.prepare()。"""
    body = {"model": model, "keep_alive": provider.extra.get("keep_alive"), "options": dict(OLLAMA_OPTIONS)}
    _post_ollama(provider.base_url.rstrip("/") + "/api/generate", body, timeout)


def chat_ollama(base_url, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=120, usage=None):
    """本机 Ollama 的原生接口：发送一次对话，返回模型回答的文字。

    没用 Ollama 的"OpenAI 兼容接口"，因为只有原生接口能传 think（关掉思考模式）和 keep_alive（多久卸载模型）。
    timeout 比在线服务长：第一次要先把模型从硬盘读进内存，没有显卡的电脑上生成也慢一些。
    """
    return chat_ollama_reply(base_url, model, messages, extra, max_tokens, temperature, timeout, usage).text


def chat_ollama_reply(base_url, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=120, usage=None,
                      tools=None):
    """Ollama 原生接口，返回 Reply。工具调用在 message.tool_calls，参数是对象、没有 id。"""
    body = {
        "model": model,
        "messages": messages,
        "stream": False,              # 一次性返回整个回答（不要一个字一个字地流式返回）
        "options": {"num_predict": max_tokens, "temperature": temperature, **OLLAMA_OPTIONS},
        **(extra or {}),
        **({"tools": tools} if tools else {}),
    }
    url = base_url.rstrip("/") + "/api/chat"
    with _tool_errors(tools):
        try:
            data = _post_ollama(url, body, timeout)
        except AIError as e:
            # 有的模型本来就不会"思考"，旧版本的 Ollama 收到 think 参数会报错：去掉它再试一次
            if "think" not in body or "think" not in e.detail.lower():
                raise
            body.pop("think")
            data = _post_ollama(url, body, timeout)
    if isinstance(data, dict):
        count_usage(usage, data.get("prompt_eval_count"), data.get("eval_count"))
    try:
        message = data["message"]
        text = message.get("content") or ""
    except (KeyError, TypeError, AttributeError) as e:
        raise AIError("返回格式不对", kind="bad_response", detail=str(data)[:300]) from e
    calls = _tool_calls(message)
    echo = {"role": "assistant", "content": text}
    if calls:
        echo["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments or {}}} for c in calls]
    return Reply(text, calls, echo, data.get("done_reason") == "length")


def _post_ollama(url, body, timeout):
    with _ollama_errors():
        return _post_json(url, body, timeout, use_proxy=False)


@contextlib.contextmanager
def _ollama_errors():
    """发给本机 Ollama 的请求：连不上、没有这个模型时，换成更好懂的错误类型。"""
    try:
        yield
    except AIError as e:
        if e.kind == "network":
            e.kind = "not_running"     # 本机的端口连不上：多半是 Ollama 没装或者没打开
        elif e.status == 404:
            e.kind = "model_missing"   # 例如 {"error": "model \"qwen3:1.7b\" not found, try pulling it first"}
        raise


def pull_ollama(base_url, model, on_progress=None, timeout=120):
    """让本机的 Ollama 下载一个模型（POST /api/pull）。下载完成才返回，失败时抛 AIError。

    Ollama 会一边下载一边返回进度：每行一个 JSON（这种格式叫 NDJSON），例如
        {"status": "pulling 3d0b790534fe", "digest": "sha256:3d0b...", "total": 1359296448, "completed": 52428800}
        {"status": "success"}
    一个模型由好几个文件（层）组成，每层有自己的 digest，所以把各层的进度加起来算总进度。
    on_progress(已下载字节, 总字节)：每收到一条进度就调用一次（在调用者的线程里）。
    timeout 是"多久收不到任何数据就算失败"，不是整个下载的时长。
    """
    request = _json_request(base_url.rstrip("/") + "/api/pull", {"model": model, "stream": True})
    layers = {}   # {digest: (已下载, 总大小)}
    with _ollama_errors(), _translate_errors(), _open(request, timeout, use_proxy=False) as response:
        for line in response:              # 一行一行地读，读到一行就处理一行，不用等整个下载结束
            if not line.strip():
                continue
            data = json.loads(line)
            if "error" in data:            # 比如模型名写错了、Ollama 自己连不上网
                raise AIError(f"下载失败：{data['error']}", kind="other", detail=str(data["error"]))
            if data.get("total") and data.get("digest"):
                layers[data["digest"]] = (data.get("completed", 0), data["total"])
                if on_progress:
                    on_progress(sum(c for c, _ in layers.values()), sum(t for _, t in layers.values()))
            if data.get("status") == "success":
                return
    raise AIError("下载没有完成", kind="other", detail="Ollama 没有返回 success")


def _post_json(url, body, timeout, headers=None, use_proxy=True):
    """POST 一个 JSON，返回解析好的 JSON。出错时统一抛 AIError。"""
    request = _json_request(url, body, headers)
    with _translate_errors(), _open(request, timeout, use_proxy) as response:
        return json.loads(response.read().decode("utf-8"))


def _json_request(url, body, headers=None):
    return urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )


@contextlib.contextmanager
def _translate_errors():
    """把 urllib 的各种异常统一换成 AIError（with 块里出的错都会经过这里）。"""
    try:
        yield
    except urllib.error.HTTPError as e:  # 服务器回了，但状态码不是 2xx（HTTPError 是 URLError 的子类，要先捕获）
        detail = _error_detail(e)
        kind = {401: "auth", 403: "auth", 404: "not_found", 429: "rate_limit"}.get(e.code, "other")
        raise AIError(f"HTTP {e.code}: {detail}", kind=kind, detail=detail, status=e.code) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise AIError(f"网络错误：{e}", kind="network", detail=str(e)) from e
    except ValueError as e:  # 返回的不是合法 JSON
        raise AIError("返回内容不是 JSON", kind="bad_response", detail=str(e)) from e


# 不走代理的"开门器"：urllib 默认会用系统代理（比如开了加速器 / VPN），
# 连本机的 Ollama 时如果也绕到代理上，就会连不上
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _open(request, timeout, use_proxy=True):
    if use_proxy:
        return urllib.request.urlopen(request, timeout=timeout)
    return _NO_PROXY.open(request, timeout=timeout)


def _error_detail(error):
    """尽量从错误响应里读出服务器的说明（通常是 {"error": {"message": ...}}，Ollama 是 {"error": "..."}）。"""
    try:
        raw = error.read().decode("utf-8", errors="replace")
    except Exception:
        return str(error)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return raw[:300]
        err = data.get("error", data)
        return str(err.get("message") or err) if isinstance(err, dict) else str(err)
    except ValueError:
        return raw[:300]
