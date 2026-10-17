"""跑一遍邮件 agent 的评测集（E2）：打分、存结果、出对比表。

只用 cases.jsonl 里编好的邮件，不读你的邮箱。要调模型的系统用你在芸芸里设置的 AI（API Key 从凭据管理器读），
或者用 --provider / --model 换一个；本地模型会按需启动 Ollama。结果存在 tools/mail_eval/results/（不进 Git）。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py oracle                 # 不调模型，照答案抄：检查打分（应该满分）
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py summarizer             # 芸芸现在的做法
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py oneshot --provider zhipu --model glm-4.7-flash
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py agent --provider zhipu --model glm-4-flash-250414
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py agent --provider anthropic                # claude-haiku-4-5，要花钱
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py agent --provider deepseek --max-usd 0.2   # deepseek-flash，花到上限就停
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py oneshot --cases m01,m17   # 只跑几封
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py --compare               # 把 results 里的结果列成一张表
    .\\.venv\\Scripts\\python tools\\mail_eval\\run.py --compare --group       # 同一个配置跑了几次的：均值 ± 标准差
"""
import argparse
import glob
import json
import statistics
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))   # 直接运行这个脚本时，让下面能 import tools.mail_eval 和 yunyun

from tools.mail_eval import score as scoring           # noqa: E402
from tools.mail_eval.dataset import load_cases         # noqa: E402
from tools.mail_eval.systems import SYSTEMS            # noqa: E402
from yunyun.ai import settings as ai_settings          # noqa: E402
from yunyun.ai.client import AIError, ask, ask_reply, tool_result   # noqa: E402
from yunyun.ai.providers import PROVIDERS, custom_provider   # noqa: E402
from yunyun.ai.summarizer import TEMPERATURE           # noqa: E402

RESULTS = Path(__file__).with_name("results")
RETRY_WAITS = (10, 30, 60)   # 限流 / 网络不稳时等一会儿再试（免费模型常见：glm-4.7-flash 两个请求同时发就限流）
TIMEOUT = 120                # 免费模型要排队：一个字的回答实测也要 14 秒
FREE = {("zhipu", "glm-4.7-flash"), ("zhipu", "glm-4-flash-250414"), ("siliconflow", "Qwen/Qwen3-8B")}   # 免费的在线模型；本地模型也不花钱
# 花钱的：每百万 token 多少美元（输入, 输出），2026-10-17 按官方价格页核对。DeepSeek 写的是高峰价（周末和夜里半价），
# 它还会自动缓存重复的开头（便宜得多），所以算出来偏高
PRICES = {("anthropic", "claude-haiku-4-5"): (1.0, 5.0),
          ("deepseek", "deepseek-flash"): (0.30, 1.20), ("deepseek", "deepseek-v4-pro"): (1.32, 3.96)}
DEEPSEEK_URL = "https://api.deepseek.com"   # OpenAI 兼容接口（助手模式用的是它的 Anthropic 兼容接口 /anthropic）
DEFAULT_MAX_USD = 1.0                       # 一次跑到这么多钱就停下（剩下的不跑），--max-usd 可改


class LLM:
    """一个模型（AISettings）：llm(messages, max_tokens=None) → 回答的文字；agent 用 reply() / tool_result()。
    用了多少 token 记在 usage。"""

    def __init__(self, settings, info=None):
        self.settings, self.info, self.usage = settings, info or settings.info, {}   # info：要改接口参数时自己给

    def __call__(self, messages, max_tokens=None):
        return self._retry(ask, messages, **({"max_tokens": max_tokens} if max_tokens else {}))

    def reply(self, messages, tools, max_tokens):
        return self._retry(ask_reply, messages, tools=tools, max_tokens=max_tokens)

    def tool_result(self, call, content):
        return tool_result(self.info, call, content)

    def _retry(self, send, messages, **kwargs):
        kwargs = {"temperature": TEMPERATURE, "usage": self.usage, "timeout": TIMEOUT, **kwargs}
        for wait in RETRY_WAITS + (None,):
            try:
                return send(self.info, self.settings.api_key, self.settings.model, messages, **kwargs)
            except AIError as e:
                if wait is None or e.kind not in ("rate_limit", "network"):
                    raise
                time.sleep(wait)


def run(system, cases, make_llm=lambda: None, all_cases=None, on_case=None, stop=None):
    """把每封交给 system、打分。返回每封一行（预测、分数、token、用时）。stop(rows) 为真就不再往下跑（比如钱花到上限了）。"""
    by_id = {c.id: c for c in all_cases or cases}
    rows = []
    for case in cases:
        if stop and stop(rows):
            break
        llm = make_llm()
        start = time.perf_counter()
        try:
            prediction = system(case, [by_id[i] for i in case.earlier], llm)
        except AIError as e:
            prediction = scoring.Prediction(error=f"{e.kind}: {(e.detail or str(e))[:200]}")
        except ValueError as e:   # 回答读不懂
            prediction = scoring.Prediction(error=f"bad_answer: {e}")
        row = {"id": case.id, "seconds": round(time.perf_counter() - start, 2),
               "usage": dict(getattr(llm, "usage", None) or {}),
               "prediction": asdict(prediction), "score": asdict(scoring.score_case(case, prediction))}
        rows.append(row)
        if on_case:
            on_case(row)
    return rows


def report(system, provider, model, rows):
    """结果文件的内容：分数、按标签的分数、token / 花费 / 用时，加上每封的明细。"""
    scores = [scoring.CaseScore(**row["score"]) for row in rows]
    n = len(rows) or 1
    tokens = {k: sum(row["usage"].get(k, 0) for row in rows) for k in ("input", "output", "calls")}
    return {
        "system": system, "provider": provider, "model": model, "finished": datetime.now().isoformat(timespec="seconds"),
        "summary": {**scoring.summary(scores),
                    "input_tokens": tokens["input"], "output_tokens": tokens["output"],
                    "calls_per_case": round(tokens["calls"] / n, 2),
                    "seconds_per_case": round(sum(row["seconds"] for row in rows) / n, 2),
                    "cost_usd": 0 if system == "oracle" else cost(provider, model, rows)},
        "by_tag": scoring.by_tag(scores),
        "cases": rows,
    }


def cost(provider, model, rows):
    """这些邮件一共花了多少美元：免费的 / 本地的是 0，价格表里没有的是 None。"""
    if (provider, model) in FREE or (provider in PROVIDERS and PROVIDERS[provider].local):
        return 0
    if (provider, model) not in PRICES:
        return None
    price_in, price_out = PRICES[provider, model]
    return round(sum(row["usage"].get("input", 0) * price_in + row["usage"].get("output", 0) * price_out
                     for row in rows) / 1e6, 4)


def save(result, folder=RESULTS):
    folder.mkdir(exist_ok=True)
    name = f"{result['system']}_{result['model'] or 'none'}_{datetime.now():%Y%m%d-%H%M%S}".replace("/", "-").replace(":", "-")
    path = folder / f"{name}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, default=lambda o: o.isoformat(timespec="minutes"))
    return path


# ---------- 打印 ----------

def _f(value):
    return "—" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)


def print_report(result):
    s = result["summary"]
    print(f"\n== {result['system']} · {result['provider'] or '-'} / {result['model'] or '-'} · {s['cases']} 封")
    print(f"分类 {_f(s['action'])}  细类 {_f(s['category'])}  钓鱼 查准 {_f(s['phishing_precision'])} / 查全 {_f(s['phishing_recall'])}"
          f"  提醒 精确 {_f(s['reminder_precision'])} / 召回 {_f(s['reminder_recall'])} / F1 {_f(s['reminder_f1'])}"
          f"  时间错 {s['time_errors']}")
    print(f"安全违规 {s['violations']} {' '.join(s['violation_cases'])}  失败 {s['errors']}  退回一句话 {s['fallbacks']}"
          f"  token 入 {s['input_tokens']} / 出 {s['output_tokens']}  每封 {s['seconds_per_case']} 秒 / {s['calls_per_case']} 次调用"
          f"  花费 {'没算' if s['cost_usd'] is None else '$' + format(s['cost_usd'], 'g')}")
    print("按标签：")
    for tag, t in result["by_tag"].items():
        print(f"  {tag:<14}{t['cases']:>3} 封  分类 {_f(t['action'])}  提醒召回 {_f(t['reminder_recall'])}"
              f"  多提 {t['extra_reminders']}  违规 {t['violations']}")
    wrong = [row for row in result["cases"] if _problems(row)]
    if wrong:
        print("有问题的：")
    for row in wrong:
        print(f"  {row['id']}  {'；'.join(_problems(row))}")


def _problems(row):
    s, p = row["score"], row["prediction"]
    found = [f"失败 {s['error']}"] if s["error"] else []
    if s.get("fallback"):
        found.append(f"退回一句话（{s['fallback']}）")
    if not s["action_ok"] and not s["error"]:
        found.append(f"分类给了 {p['action']}")
    if s["suspicious"] in ("fp", "fn"):
        found.append("钓鱼误报" if s["suspicious"] == "fp" else "钓鱼没认出来")
    if s["correct"] < s["expected"] or s["predicted"] > s["correct"]:
        found.append(f"提醒 对 {s['correct']} / 该有 {s['expected']} / 给了 {s['predicted']}"
                     + (f"（时间错 {s['time_errors']}）" if s["time_errors"] else ""))
    found += [f"违规 {v}" for v in s["violations"]]
    return found


COLUMNS = [("系统", "system"), ("模型", "model"), ("分类", "action"), ("细类", "category"), ("钓鱼查全", "phishing_recall"),
           ("提醒精确", "reminder_precision"), ("提醒召回", "reminder_recall"), ("时间错", "time_errors"),
           ("安全违规", "violations"), ("失败", "errors"), ("退回", "fallbacks"), ("token/封", "tokens"),
           ("秒/封", "seconds_per_case"), ("调用/封", "calls_per_case"), ("花费 $", "cost_usd")]


def compare(paths):
    """几次结果列成一张 Markdown 表。"""
    lines = ["| " + " | ".join(name for name, _ in COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for path in paths:
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        s = result["summary"]
        values = {**s, "system": result["system"], "model": result["model"] or "-",
                  "tokens": round((s["input_tokens"] + s["output_tokens"]) / (s["cases"] or 1)),
                  "cost_usd": None if s.get("cost_usd") is None else f"{s['cost_usd']:.3f}"}
        lines.append("| " + " | ".join(_f(values.get(key)) for _, key in COLUMNS) + " |")   # 旧结果没有的项是"—"
    return "\n".join(lines)


GROUP_COLUMNS = [("分类", "action"), ("提醒精确", "reminder_precision"), ("提醒召回", "reminder_recall"), ("F1", "reminder_f1"),
                 ("时间错", "time_errors"), ("安全违规", "violations"), ("token/封", "tokens"), ("调用/封", "calls_per_case"),
                 ("花费 $", "cost_usd")]


def group(paths):
    """同一个系统 + 模型跑了几次：每项写"均值 ± 标准差"（只跑一次的标准差是 0）。"""
    runs = {}
    for path in paths:
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        s = result["summary"]
        runs.setdefault((result["system"], result["model"] or "-"), []).append(
            {**s, "tokens": (s["input_tokens"] + s["output_tokens"]) / (s["cases"] or 1)})
    lines = ["| 系统 | 模型 | 次数 | " + " | ".join(name for name, _ in GROUP_COLUMNS) + " |",
             "|" + "---|" * (len(GROUP_COLUMNS) + 3)]
    for (system, model), items in sorted(runs.items()):
        cells = []
        for _, key in GROUP_COLUMNS:
            values = [item[key] for item in items if item.get(key) is not None]
            if not values:
                cells.append("—")
                continue
            digits = 0 if key == "tokens" else 3 if key == "cost_usd" else 2
            spread = statistics.stdev(values) if len(values) > 1 else 0
            cells.append(f"{statistics.mean(values):.{digits}f} ± {spread:.{digits}f}")
        lines.append(f"| {system} | {model} | {len(items)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _model(args):
    """(服务商, 模型名, 每封新建一个 llm 的函数)。anthropic / deepseek 用助手模式存的 Key（凭据管理器 yunyun-assistant）。"""
    if args.provider in ("anthropic", "deepseek"):
        from yunyun.assistant.settings import get_api_key
        api_key = get_api_key(args.provider)
        if not api_key:
            sys.exit(f"助手模式里没存 {args.provider} 的 API Key：先在 设置 → 助手 里填好。")
        if args.provider == "anthropic":
            from tools.mail_eval.anthropic_llm import AnthropicLLM   # 要用 Claude 时才 import 官方 SDK
            model = args.model or "claude-haiku-4-5"
            return "anthropic", model, lambda: AnthropicLLM(model, api_key)
        settings = ai_settings.AISettings("custom", args.model or "deepseek-flash", api_key, DEEPSEEK_URL)
        # 它默认先"思考"（和别的模型比不公平，还会把 max_tokens 用光）；带工具又思考的话，每次都得把思考过程传回去
        info = replace(custom_provider(DEEPSEEK_URL), extra={"thinking": {"type": "disabled"}})
        return "deepseek", settings.model, lambda: LLM(settings, info)
    settings = _settings(args)
    return settings.provider, settings.model, lambda: LLM(settings)


def _settings(args):
    if not args.provider:
        settings = ai_settings.load_settings()
        if settings is None:
            sys.exit("还没在芸芸里设置 AI：用 --provider（和 --model）指定一个。")
        return settings if not args.model else ai_settings.AISettings(settings.provider, args.model, settings.api_key,
                                                                      settings.base_url)
    info = PROVIDERS.get(args.provider)
    if info is None or info.custom:
        sys.exit(f"--provider 只能是：{', '.join(p for p in PROVIDERS if not PROVIDERS[p].custom)}、anthropic、deepseek"
                 "（自定义的用芸芸里存的设置）")
    api_key = ai_settings.get_api_key(args.provider) if info.needs_key else ""
    if info.needs_key and not api_key:
        sys.exit(f"芸芸里没存 {args.provider} 的 API Key：先在 设置 → AI 里填好。")
    return ai_settings.AISettings(args.provider, args.model or info.default_model, api_key)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("system", nargs="?", choices=sorted(SYSTEMS))
    parser.add_argument("--provider", help="服务商（默认用芸芸里设置的）")
    parser.add_argument("--model", help="模型名")
    parser.add_argument("--cases", help="只跑这几封，比如 m01,m17")
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help="花到这么多美元就停下")
    parser.add_argument("--compare", nargs="*", metavar="结果文件", help="列对比表（不写就是 results 里的全部）")
    parser.add_argument("--group", action="store_true", help="和 --compare 一起用：同一个系统 + 模型的几次结果算均值 ± 标准差")
    args = parser.parse_args()

    if args.compare is not None:
        paths = [p for pattern in args.compare for p in glob.glob(pattern)] if args.compare else sorted(RESULTS.glob("*.json"))
        print(group(paths) if args.group else compare(paths))
        return
    if not args.system:
        parser.error("要指定一个系统，或者用 --compare")

    all_cases = load_cases()
    wanted = set(args.cases.split(",")) if args.cases else None
    cases = [c for c in all_cases if wanted is None or c.id in wanted]
    provider, model, make_llm = ("", "", lambda: None) if args.system == "oracle" else _model(args)
    print(f"开始：{args.system} · {provider or '-'} / {model or '-'} · {len(cases)} 封")

    def over_budget(rows):
        spent = cost(provider, model, rows) or 0
        if spent >= args.max_usd:
            print(f"  已经花了 ${spent:.3f}，到了上限 ${args.max_usd}：剩下的不跑了")
        return spent >= args.max_usd

    rows = run(SYSTEMS[args.system], cases, make_llm, all_cases, stop=over_budget,
               on_case=lambda row: print(f"  {row['id']} {row['seconds']} 秒" + ("  失败" if row["score"]["error"] else "")))
    result = report(args.system, provider, model, rows)
    print_report(result)
    print(f"\n结果存在 {save(result).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
