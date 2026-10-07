"""测试连接：用 Anthropic 官方的 Python SDK 查一下这个模型（Models API，不问 Claude 任何问题，**不花钱**）。

一次确认三件事：Key 对不对、模型名有没有、网络通不通。设置窗口的"测试并保存"用它。
在单独的小进程里运行：芸芸进程不 import anthropic SDK（理由和助手子进程一样：import 了，内存就还不回来）。
    开发版：python -m yunyun.assistant.check       打包后：芸芸.exe --assistant-check
从标准输入读一行 JSON：{"provider": ..., "model": ..., "key": ...}（Key 不放在命令行参数里：别的程序看得到命令行）；
往标准输出写一行 JSON：{"ok": true} 或 {"ok": false, "kind": "auth", "detail": "..."}（kind 是 protocol.ERROR_KINDS 里的）。

阶段 3 接别家的"Anthropic 兼容接口"时，它们不一定支持 Models API，到时按服务商换一种测法（比如问一句很短的话）。
"""
import json
import os
import sys

from yunyun.assistant import protocol
from yunyun.assistant.environment import clean_environment, system_proxy_env
from yunyun.assistant.providers import PROVIDERS

TIMEOUT_S = 20


def _message(error):
    """出错信息里给人看的那部分（不会含 Key）。

    服务器回的是 {"type": "error", "error": {"type": ..., "message": "API key is invalid."}}：只取里面那句话。
    """
    body = getattr(error, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("message"):
        return str(body["error"]["message"])[:300]
    return str(getattr(error, "message", None) or error)[:300]


def check(provider_code, model, api_key):
    """返回 (出错种类, 详细信息)；没问题时返回 (None, "")。"""
    import anthropic   # 到这里才 import：只有这个小进程会载入它

    provider = PROVIDERS.get(provider_code)
    if provider is None:
        return "other", f"不认识的服务商：{provider_code}"
    clean_environment()                      # 官方 SDK 也会读 ANTHROPIC_BASE_URL 等变量：只用芸芸给的
    os.environ.update(system_proxy_env())    # 只开了系统代理时，让它走代理
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


def main():
    out = sys.stdout.buffer if sys.stdout else os.fdopen(1, "wb", closefd=False)
    sys.stdout = sys.stderr   # 别的代码 print 的东西去 stderr，标准输出只放结果
    try:
        line = next(protocol.read_lines(0), b"")
        request = json.loads(line)
        kind, detail = check(request["provider"], request["model"], request["key"])
        result = {"ok": True} if kind is None else {"ok": False, "kind": kind, "detail": detail}
    except Exception as e:   # 兜底：总要给芸芸一个回答，界面不能一直转圈
        result = {"ok": False, "kind": "other", "detail": f"{type(e).__name__}: {e}"[:300]}
    out.write((json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8"))
    out.flush()


if __name__ == "__main__":
    main()
