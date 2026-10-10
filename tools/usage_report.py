"""开发用：看助手对话记录里的用量——每段对话用了哪些模型、调用几次、各种 token 多少、芸芸按价格算是多少钱。

**只读数字和模型名，不读、不打印对话内容。** 阶段 3 实测别家服务商时用（后台小任务用的哪个模型？读缓存报不报？）。

    .\\.venv\\Scripts\\python tools\\usage_report.py                 # 最近 3 段对话
    .\\.venv\\Scripts\\python tools\\usage_report.py 10 <projects 文件夹>

在 Claude 桌面版里运行时 %APPDATA% 会被重定向，看用户真实的数据要把文件夹写成
\\\\localhost\\C$\\Users\\<用户名>\\AppData\\Roaming\\Yunyun\\Yunyun\\assistant\\claude\\projects
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yunyun.assistant.providers import recorded_cost  # noqa: E402
from yunyun.paths import data_dir  # noqa: E402

USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def report(path):
    calls = {}                 # 每次模型调用（同一个 message.id 会分好几行写，取最后一行）：id → (模型, usage)
    cost_state = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("type") == "cost-state":
            cost_state = data
        message = data.get("message")
        if data.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
            calls[message.get("id") or len(calls)] = (message.get("model"), message["usage"])
    per_model = defaultdict(lambda: defaultdict(int))
    for model, usage in calls.values():
        per_model[model]["calls"] += 1
        for key in USAGE_KEYS:
            per_model[model][key] += int(usage.get(key) or 0)
    print(f"\n== {path.parent.name[-30:]}/{path.stem[:8]}…  （{path.stat().st_size // 1024} KB）")
    print("   主循环每次调用（对话记录里的 usage）：")
    for model, sums in per_model.items():
        print(f"     {model}: {sums['calls']} 次；输入 {sums['input_tokens']}、写缓存 {sums['cache_creation_input_tokens']}、"
              f"读缓存 {sums['cache_read_input_tokens']}、输出 {sums['output_tokens']}")
    if cost_state:
        usage = cost_state.get("modelUsage") or {}
        print("   Claude Code 累计（cost-state，含后台小任务）：")
        for model, used in usage.items():
            print(f"     {model}: 输入 {used.get('inputTokens')}、写缓存 {used.get('cacheCreationInputTokens')}、"
                  f"读缓存 {used.get('cacheReadInputTokens')}、输出 {used.get('outputTokens')}、"
                  f"网页搜索 {used.get('webSearchRequests')}")
        claude_total = cost_state.get("totalCostUSD")
        print(f"   花费：Claude Code 记的 ${claude_total}；芸芸算的 ${recorded_cost(claude_total, usage)}")


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    folder = Path(sys.argv[2]) if len(sys.argv) > 2 else data_dir() / "assistant" / "claude" / "projects"
    files = sorted(folder.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:count]
    if not files:
        print("没有找到对话记录：", folder)
    for path in files:
        report(path)


if __name__ == "__main__":
    main()
