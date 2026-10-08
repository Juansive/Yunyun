"""开发用：真的 Claude Code + 假的 Anthropic 接口，验证接别家服务商（DeepSeek）时 Claude Code 怎么发请求。不花钱。

    .\\.venv\\Scripts\\python tools\\try_provider.py

  1. 照 Claude 官方的样子问一句：看 Claude Code 报的花费（作对照）
  2. 照 DeepSeek 的样子（地址带 /anthropic、Bearer 传 Key、模型别名），分别选 flash / pro 问一句：
     请求发到哪里、Key 怎么传、用什么模型、有没有网页搜索工具、Claude Code 报的花费是多少

2026-10-08 实测（Claude Code 2.1.292）：地址、Bearer、模型名都对，WebSearch 不在工具里；
**不认识的模型名，Claude Code 一律按 $5 / $25 每百万 token 算花费**（DeepSeek flash 实际约是它的 1/17）→ 别家的花费芸芸自己算。
后台小任务（haiku 别名 → deepseek-flash）这里验证不了：读网页时 Claude Code 把 http 升成 https，连不上假接口，
小模型那一步不会发生——3.6 用真 Key 时确认。
"""
import asyncio
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from fake_anthropic_api import FakeApi, make_brain, serve
from yunyun.assistant import protocol
from yunyun.assistant.providers import PROVIDERS

USAGE = {"input_tokens": 100_000, "output_tokens": 1_000}   # 每次回答报这么多，花费的差别才看得出来


async def ask_once(tmp, provider, model):
    api = FakeApi([("text", "你好呀")], usage=USAGE)
    server = serve(api)
    events = []
    brain = make_brain(server, tmp, "", events.append, provider=provider, model=model)
    if provider is not None:   # 地址和真的一样带一段路径
        PROVIDERS["fake"] = replace(PROVIDERS["fake"], base_url=PROVIDERS["fake"].base_url + "/anthropic")
    try:
        await asyncio.wait_for(brain.start(), 60)
        await asyncio.wait_for(brain.ask("你好"), 60)
    except Exception as e:
        events.append(protocol.Error("other", f"{type(e).__name__}: {e}"))
    finally:
        await brain.close()
        server.shutdown()
    finished = next((e for e in events if isinstance(e, protocol.TurnFinished)), None)
    errors = [e for e in events if isinstance(e, protocol.Error)]
    return api, finished, errors


def show(title, api, finished, errors):
    print(f"\n== {title} ==")
    for method, path, model, auth in api.requests:
        print(f"   {method} {path}  模型 {model}  Key：{auth or '没带'}")
    print("   给模型的工具：", api.offered_tools)
    print("   Claude Code 报的这一轮花费：", finished.cost_usd if finished else None, "  出错：", errors)


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="yunyun-provider-"))
    ok = True
    try:
        api, finished, errors = await ask_once(tmp / "claude", None, "")
        show("Claude 官方的样子（claude-haiku-4-5，对照）", api, finished, errors)
        print(f"   按 Haiku 的价格应该约 ${USAGE['input_tokens'] / 1e6 * 1 + USAGE['output_tokens'] / 1e6 * 5:.3f}")

        deepseek = replace(PROVIDERS["deepseek"], cost_in_usd=True)   # 打开才看得到 Claude Code 报的原始花费
        for model in ("deepseek-flash", "deepseek-v4-pro"):
            api, finished, errors = await ask_once(tmp / model, deepseek, model)
            show(f"DeepSeek 的样子（{model}）", api, finished, errors)
            main_models = {m for _, path, m, _ in api.requests if "/v1/messages" in path and m}
            ok &= bool(api.requests) and all(auth == "bearer" for _, _, _, auth in api.requests)
            ok &= all(path.startswith("/anthropic/") for _, path, _, _ in api.requests)
            ok &= model in main_models and main_models <= {model, "deepseek-flash"}
            ok &= all(m == "deepseek-flash" for _, m in api.others if m)
            ok &= api.offered_tools is not None and "WebSearch" not in api.offered_tools and not errors
        print("\n结论：", "✓ 地址、Bearer、模型都对，没有网页搜索" if ok else "✗ 有不对的地方，看上面")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
