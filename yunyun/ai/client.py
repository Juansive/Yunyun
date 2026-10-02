"""最小的"OpenAI 兼容"聊天接口客户端：只用标准库 urllib 发一个 HTTP 请求。

没有用 openai 之类的 SDK，是为了让你看清楚调用大模型其实就是：
    POST {base_url}/chat/completions
    请求头带 Authorization: Bearer <API Key>
    请求体是 JSON：{"model": ..., "messages": [...]}
    返回的 JSON 里，回答在 choices[0].message.content
"""
import json
import socket
import urllib.error
import urllib.request


class AIError(Exception):
    """调用大模型失败。

    e.kind 给界面做多语言："auth" Key 不对 / "rate_limit" 调用太频繁或额度用完 /
    "network" 网络问题 / "bad_response" 返回内容看不懂 / "other" 其他；
    e.detail 是服务器的原话，方便排查。
    """

    def __init__(self, message, kind="other", detail=""):
        super().__init__(message)
        self.kind = kind
        self.detail = detail


def chat(base_url, api_key, model, messages, extra=None, max_tokens=200, temperature=0.7, timeout=30):
    """发送一次对话，返回模型回答的文字。"""
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,   # 越高越有创意、越低越稳定；提醒语气活泼一点，0.7 正好
        **(extra or {}),              # ** 把字典展开合并进来
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 服务器回了，但状态码不是 2xx
        detail = _error_detail(e)
        if e.code in (401, 403):
            kind = "auth"
        elif e.code == 429:
            kind = "rate_limit"
        else:
            kind = "other"
        raise AIError(f"HTTP {e.code}: {detail}", kind=kind, detail=detail) from e
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as e:
        raise AIError(f"网络错误：{e}", kind="network", detail=str(e)) from e
    except ValueError as e:  # 返回的不是合法 JSON
        raise AIError("返回内容不是 JSON", kind="bad_response", detail=str(e)) from e

    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise AIError("返回格式不对", kind="bad_response", detail=str(data)[:300]) from e


def _error_detail(error):
    """尽量从错误响应里读出服务器的说明（通常是 {"error": {"message": ...}}）。"""
    try:
        raw = error.read().decode("utf-8", errors="replace")
    except Exception:
        return str(error)
    try:
        data = json.loads(raw)
        err = data.get("error", data)
        return str(err.get("message") or err) if isinstance(err, dict) else str(err)
    except ValueError:
        return raw[:300]
