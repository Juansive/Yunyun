"""助手模式的记账本：芸芸用 API Key 花了多少钱（按天记）。

Anthropic 没有能查账户余额的接口（普通 Key 查不到；用量接口要组织的管理员 Key，个人账户用不了，也不给余额），
所以芸芸自己记：每次回答结束，Claude Code 报的这一轮花费（protocol.TurnFinished.cost_usd）加到当天。
设置页显示"今天 / 本月 / 一共"，余额请用户到官网看。

- 文件：数据目录下的 assistant_spending.json，内容只有 {"days": {"2026-10-07": 0.0234, ...}}，**不记任何对话内容**；
- 只在芸芸进程里用（会话 session.py 收到 TurnFinished 时记），不 import Qt；
- 按 token 计费的服务商（cost_usd 是 None）不记。
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import date

from yunyun.paths import data_dir

log = logging.getLogger(__name__)

LEDGER_PATH = data_dir() / "assistant_spending.json"   # 测试时换到临时目录（conftest）


@dataclass(frozen=True)
class Spending:
    today: float = 0.0
    month: float = 0.0
    total: float = 0.0


def _load():
    try:
        days = json.loads(LEDGER_PATH.read_text(encoding="utf-8")).get("days") or {}
        return {str(day): float(cost) for day, cost in days.items()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, AttributeError, TypeError) as e:
        log.warning("记账本读不了，当成空的：%s", e)
        return {}


def record(cost_usd, day=None):
    """把一笔花费（美元）记到 day（默认今天）。0、负数、None 不记。"""
    if not cost_usd or cost_usd <= 0:
        return
    days = _load()
    key = (day or date.today()).isoformat()
    days[key] = round(days.get(key, 0.0) + float(cost_usd), 6)
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = LEDGER_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps({"days": days}, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, LEDGER_PATH)   # 先写临时文件再换过去：写到一半断电也不会把记账本写坏
    except OSError as e:
        log.warning("记账本写不进去：%s", e)


def summary(today=None):
    """今天、本月、一共花了多少。"""
    today = today or date.today()
    days = _load()
    month_prefix = today.isoformat()[:7]   # "2026-10"
    return Spending(
        today=days.get(today.isoformat(), 0.0),
        month=sum(cost for day, cost in days.items() if day.startswith(month_prefix)),
        total=sum(days.values()),
    )


def money(value):
    """$0.012 / $1.25：不到 1 美元多留一位小数，不然几次提问全显示成 $0.01。"""
    return f"${value:.3f}" if value < 1 else f"${value:.2f}"
