"""测试连接、查余额（设置窗口用）：**都不花钱**，不问模型任何问题。

一次确认三件事：Key 对不对、模型名有没有、网络通不通。怎么测按服务商（providers.Provider.models_url）：
- Claude 官方：用 Anthropic 官方的 Python SDK 查一下这个模型（Models API）；
- DeepSeek 这类：查它的模型列表（OpenAI 格式，Bearer 传 Key）。DeepSeek 认不出的模型名会悄悄换成 flash，所以要看名字在不在列表里。
查余额（Provider.balance_url，DeepSeek 有、Anthropic 没有）：测试成功时顺便查；设置页每次显示时也单独查一次。

在单独的小进程里运行：芸芸进程不 import anthropic SDK（理由和助手子进程一样：import 了，内存就还不回来）。
    开发版：python -m yunyun.assistant.check       打包后：芸芸.exe --assistant-check
从标准输入读一行 JSON：{"provider": ..., "model": ..., "key": ..., "what": "check" 或 "balance"}
（Key 不放在命令行参数里：别的程序看得到命令行）；往标准输出写一行 JSON：
{"ok": true, "balance": {...}（查得到余额时才有）} 或 {"ok": false, "kind": "auth", "detail": "..."}（kind 是 protocol.ERROR_KINDS 里的）。
balance 的样子：{"available": 够不够用, "items": [{"currency": "CNY", "total": "110.00"}, ...]}。
"""
import json
import logging
import os
import sys
import urllib.error
import urllib.request

from yunyun.assistant import protocol
from yunyun.assistant.environment import clean_environment, system_proxy_env
from yunyun.assistant.providers import PROVIDERS

log = logging.getLogger(__name__)

TIMEOUT_S = 20
_HTTP_KINDS = {401: "auth", 403: "auth", 402: "billing", 429: "rate_limit"}


class CheckError(Exception):
    def __init__(self, kind, detail=""):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


def _message(error):
    """出错信息里给人看的那部分（不会含 Key）。

    服务器回的是 {"type": "error", "error": {"type": ..., "message": "API key is invalid."}}：只取里面那句话。
    """
    body = getattr(error, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("message"):
        return str(body["error"]["message"])[:300]
    return str(getattr(error, "message", None) or error)[:300]


def _prepare():
    clean_environment()                      # 官方 SDK 也会读 ANTHROPIC_BASE_URL 等变量：只用芸芸给的
    os.environ.update(system_proxy_env())    # 只开了系统代理时，让它走代理（urllib 也读 HTTPS_PROXY）


def check(provider_code, model, api_key):
    """返回 (出错种类, 详细信息)；没问题时返回 (None, "")。"""
    provider = PROVIDERS.get(provider_code)
    if provider is None:
        return "other", f"不认识的服务商：{provider_code}"
    _prepare()
    if provider.models_url:
        return _check_model_list(provider, model, api_key)
    return _check_anthropic(provider, model, api_key)


def _check_anthropic(provider, model, api_key):
    import anthropic   # 到这里才 import：只有这个小进程会载入它

    client = anthropic.Anthropic(api_key=api_key, base_url=provider.base_url or None, max_retries=1, timeout=TIMEOUT_S)
    try:
        client.models.retrieve(model)
    except anthropic.NotFoundError as e:
        return "model", _message(e)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        return "auth", _message(e)
    except anthropic.RateLimitError as e:
        return "rate_limit", _message(e)
    except anthropic.APIConnectionError as e:   # 连不上、超时
        return "network", _message(e)
    except anthropic.APIStatusError as e:
        return "other", f"{e.status_code}: {_message(e)}"
    return None, ""


def _check_model_list(provider, model, api_key):
    try:
        data = get_json(provider.models_url, api_key)
    except CheckError as e:
        return e.kind, e.detail
    names = [str(m.get("id")) for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
    if model not in names:
        return "model", f"{model}（{', '.join(names) or '—'}）"
    return None, ""


def balance(provider_code, api_key):
    """这家服务商的账户余额（见文件开头 balance 的样子）；查不了（没有接口）是 None；出错抛 CheckError。"""
    provider = PROVIDERS.get(provider_code)
    if provider is None or not provider.balance_url:
        return None
    data = get_json(provider.balance_url, api_key)
    items = [{"currency": str(b.get("currency", "")), "total": str(b.get("total_balance", ""))}
             for b in (data.get("balance_infos") or []) if isinstance(b, dict)]
    return {"available": bool(data.get("is_available")), "items": items}


def get_json(url, api_key):
    """GET 一个要 Bearer Key 的 JSON 接口；出错变成 CheckError（种类按 HTTP 状态码）。测试里换成假的。"""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise CheckError(_HTTP_KINDS.get(e.code, "other"), f"{e.code}: {_http_message(e)}") from e
    except (urllib.error.URLError, OSError) as e:   # 连不上、超时（TimeoutError 也是 OSError）
        raise CheckError("network", str(getattr(e, "reason", e))[:300]) from e
    except ValueError as e:   # 回的不是 JSON
        raise CheckError("other", f"{type(e).__name__}: {e}"[:300]) from e
    if not isinstance(data, dict):
        raise CheckError("other", "回答的格式不对")
    return data


def _http_message(error):
    """OpenAI 格式的出错：{"error": {"message": "Authentication Fails ..."}}，只取那句话。"""
    try:
        body = json.loads(error.read().decode("utf-8", errors="replace"))
        return str(body["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError, OSError):
        return str(error.reason)[:300]


def run(request):
    """一个请求 → 一个结果（见文件开头）。"""
    provider, key = request["provider"], request["key"]
    if request.get("what") == "balance":
        _prepare()
        try:
            return {"ok": True, "balance": balance(provider, key)}
        except CheckError as e:
            return {"ok": False, "kind": e.kind, "detail": e.detail}
    kind, detail = check(provider, request["model"], key)
    if kind is not None:
        return {"ok": False, "kind": kind, "detail": detail}
    result = {"ok": True}
    try:
        result["balance"] = balance(provider, key)   # 顺便查余额：查不到也不影响"测试通过"
    except CheckError as e:
        log.info("测试通过了，但查不到余额（%s）", e.kind)
    return result


def main():
    out = sys.stdout.buffer if sys.stdout else os.fdopen(1, "wb", closefd=False)
    sys.stdout = sys.stderr   # 别的代码 print 的东西去 stderr，标准输出只放结果
    try:
        line = next(protocol.read_lines(0), b"")
        result = run(json.loads(line))
    except Exception as e:   # 兜底：总要给芸芸一个回答，界面不能一直转圈
        result = {"ok": False, "kind": "other", "detail": f"{type(e).__name__}: {e}"[:300]}
    if result.get("balance") is None:
        result.pop("balance", None)
    out.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    out.flush()


if __name__ == "__main__":
    main()
