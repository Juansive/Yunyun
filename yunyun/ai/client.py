"""最小的大模型客户端：只用标准库 urllib 发一个 HTTP 请求。

没有用 openai 之类的 SDK，是为了让你看清楚调用大模型其实就是：
    POST {base_url}/chat/completions
    请求头带 Authorization: Bearer <API Key>
    请求体是 JSON：{"model": ..., "messages": [...]}
    返回的 JSON 里，回答在 choices[0].message.content

本机的 Ollama 用它自己的接口 POST {base_url}/api/chat（见 chat_ollama），格式很像，回答在 message.content。
外部只需要调用 ask()：它按服务商的接口格式选用 chat() 或 chat_ollama()。
"""
import contextlib
import json
import socket
import urllib.error
import urllib.request


class AIError(Exception):
    """调用大模型失败。

    e.kind 给界面做多语言："auth" Key 不对 / "rate_limit" 调用太频繁或额度用完 /
    "network" 网络问题 / "bad_response" 返回内容看不懂 / "other" 其他；
    本地模型还有："not_running" 连不上 Ollama / "model_missing" Ollama 里还没有这个模型。
    e.detail 是服务器的原话，方便排查；e.status 是 HTTP 状态码（没有就是 None）。
    """

    def __init__(self, message, kind="other", detail="", status=None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.status = status


def ask(provider, api_key, model, messages, **kwargs):
    """按服务商的接口格式发一次对话，返回回答的文字（summarizer 和设置页的"测试"都用它）。"""
    if provider.api == "ollama":
        return chat_ollama(provider.base_url, model, messages, extra=provider.extra, **kwargs)
    return chat(provider.base_url, api_key, model, messages, extra=provider.extra, **kwargs)


def chat(base_url, api_key, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=30):
    """OpenAI 兼容格式：发送一次对话，返回模型回答的文字。"""
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,   # 越高越有创意、越低越稳定；提醒语气活泼一点，0.7 正好
        **(extra or {}),              # ** 把字典展开合并进来
    }
    data = _post_json(base_url.rstrip("/") + "/chat/completions", body, timeout,
                      headers={"Authorization": f"Bearer {api_key}"})
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise AIError("返回格式不对", kind="bad_response", detail=str(data)[:300]) from e


def chat_ollama(base_url, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=120):
    """本机 Ollama 的原生接口：发送一次对话，返回模型回答的文字。

    没用 Ollama 的"OpenAI 兼容接口"，因为只有原生接口能传 think（关掉思考模式）和 keep_alive（多久卸载模型）。
    timeout 比在线服务长：第一次要先把模型从硬盘读进内存，没有显卡的电脑上生成也慢一些。
    """
    body = {
        "model": model,
        "messages": messages,
        "stream": False,              # 一次性返回整个回答（不要一个字一个字地流式返回）
        "options": {"num_predict": max_tokens, "temperature": temperature},
        **(extra or {}),
    }
    url = base_url.rstrip("/") + "/api/chat"
    try:
        data = _post_ollama(url, body, timeout)
    except AIError as e:
        # 有的模型本来就不会"思考"，旧版本的 Ollama 收到 think 参数会报错：去掉它再试一次
        if "think" not in body or "think" not in e.detail.lower():
            raise
        body.pop("think")
        data = _post_ollama(url, body, timeout)
    try:
        return data["message"]["content"] or ""
    except (KeyError, TypeError) as e:
        raise AIError("返回格式不对", kind="bad_response", detail=str(data)[:300]) from e


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
        kind = {401: "auth", 403: "auth", 429: "rate_limit"}.get(e.code, "other")
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
