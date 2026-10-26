"""芸芸的"我是谁"（阶段 12.2）：她对自己的认识，跟着经历慢慢变。设计见 docs/助手模式计划.md 阶段 12。

- 一开始是空的（只有预设性格）；每天回顾时（reflect.py）她可以提议加一句、改一句、删一句；
- **改得像人一样慢**（2026-10-26 定）：提议先进"观察中"，在 DAYS_TO_APPLY 个不同的日子里都有依据才真的改；
  一天最多改 MAX_PER_DAY 处；OBSERVE_DAYS 天没再出现的提议扔掉；
- 每次改之前存一份旧版（最多 MAX_VERSIONS 份，设置里能退回）；你在设置里手动改的马上生效（12.5）；
- 底线规则（persona._RULES）不在这里，改不到；
- 存在 %APPDATA%\\Yunyun\\Yunyun\\assistant\\whoami.json，只在本机；日志不写内容。

芸芸进程（设置页）和助手子进程（回顾、人设）都用：不 import Qt，也不 import SDK。
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from yunyun import jsonfile
from yunyun.paths import data_dir

DAYS_TO_APPLY = 3      # 在几个不同的日子里出现过才真的改（2026-10-26 定）
MAX_PER_DAY = 2        # 一次回顾最多改几处
MAX_PROPOSALS = 3      # 一次回顾最多新提几条
OBSERVE_DAYS = 30      # 观察中的多少天没再出现就扔掉
MAX_OBSERVING = 20
MAX_LINES = 20         # "我是谁"最多几句（每一问都带着）
MAX_LINE = 80          # 一句最多多少字
MAX_VERSIONS = 50
VERSION = 1


@dataclass
class Line:
    id: str      # "s3"
    text: str


@dataclass
class Change:
    id: str                 # "k2"
    text: str               # 新的那句；空 = 删掉 replaces 那句
    replaces: str = ""      # 改 / 删哪一句（Line.id）；空 = 加一句
    days: list = field(default_factory=list)   # 在哪几天的回顾里有依据（"YYYY-MM-DD"）
    created: str = ""


@dataclass
class WhoAmI:
    lines: list = field(default_factory=list)      # Line
    observing: list = field(default_factory=list)  # Change
    versions: list = field(default_factory=list)   # {"at": ISO, "text": 改之前的全文}，旧的在前


def whoami_path():
    return data_dir() / "assistant" / "whoami.json"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _clean(text):
    return " ".join(str(text or "").split())[:MAX_LINE]


def text_of(who):
    """全文：一句一行。"""
    return "\n".join(line.text for line in who.lines)


# ---------- 读写 ----------

def load(path=None):
    """读"我是谁"；没有文件、读不了、格式不对都当没有（读不了的记日志）。"""
    data = jsonfile.read(path or whoami_path(), "我是谁") or {}

    def items(key):
        value = data.get(key)
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    lines = [Line(str(i["id"]), _clean(i.get("text"))) for i in items("lines") if i.get("id") and _clean(i.get("text"))]
    observing = [Change(str(i["id"]), _clean(i.get("text")), str(i.get("replaces") or ""),
                        [str(d) for d in i.get("days") or [] if d], str(i.get("created") or ""))
                 for i in items("observing") if i.get("id") and isinstance(i.get("days") or [], list)]
    versions = [{"at": str(i.get("at") or ""), "text": str(i.get("text") or "")} for i in items("versions")]
    return WhoAmI(lines, observing, versions)


def save(who, path=None):
    jsonfile.write(path or whoami_path(), {"version": VERSION, "lines": [asdict(x) for x in who.lines],
                                           "observing": [asdict(x) for x in who.observing], "versions": who.versions})


def _next_id(items, prefix):
    numbers = [int(x.id[1:]) for x in items if x.id[1:].isdigit()]
    return f"{prefix}{max(numbers, default=0) + 1}"


def _backup(who, now):
    who.versions.append({"at": now, "text": text_of(who)})
    del who.versions[:-MAX_VERSIONS]


# ---------- 回顾以后 ----------

def apply(who, changes, day, now=None):
    """把一天回顾的结果用上 → 这次真的改了几处。看不懂的部分跳过。

    changes：{"seen": [观察中的编号], "propose": [{"text", "replaces"}]}；day：回顾的是哪天（"YYYY-MM-DD"）。"""
    now = now or _now()
    changes = changes if isinstance(changes, dict) else {}
    by_id = {c.id: c for c in who.observing}
    line_ids = {line.id for line in who.lines}
    for change_id in changes.get("seen") if isinstance(changes.get("seen"), list) else []:
        change = by_id.get(str(change_id))
        if change is not None and day not in change.days:
            change.days.append(day)
    proposals = changes.get("propose") if isinstance(changes.get("propose"), list) else []
    for item in [p for p in proposals if isinstance(p, dict)][:MAX_PROPOSALS]:
        text, replaces = _clean(item.get("text")), str(item.get("replaces") or "")
        if replaces not in line_ids:
            replaces = ""
        if not text and not replaces:
            continue
        same = next((c for c in who.observing if c.replaces == replaces and c.text.casefold() == text.casefold()), None)
        if same is not None:   # 已经在观察中：算又出现了一次
            if day not in same.days:
                same.days.append(day)
            continue
        who.observing.append(Change(_next_id(who.observing, "k"), text, replaces, [day], now))
    applied = _grow(who, now)
    _trim(who, day)
    return applied


def _grow(who, now):
    """有依据的日子够了的提议真的改进去（一次最多 MAX_PER_DAY 处，依据多的、早提的先）。"""
    ready = sorted((c for c in who.observing if len(c.days) >= DAYS_TO_APPLY), key=lambda c: (-len(c.days), c.created))
    done = []
    for change in ready:
        if len(done) >= MAX_PER_DAY:
            break
        line = next((x for x in who.lines if x.id == change.replaces), None)
        if change.replaces and line is None:
            continue   # 要改的那句已经没了：_trim 会扔掉它
        if not change.replaces and len(who.lines) >= MAX_LINES:
            continue   # 满了：等她提议删掉或改掉别的
        if not done:
            _backup(who, now)
        if line is None:
            who.lines.append(Line(_next_id(who.lines, "s"), change.text))
        elif change.text:
            line.text = change.text
        else:
            who.lines.remove(line)
        done.append(change)
    who.observing = [c for c in who.observing if c not in done]
    return len(done)


def _trim(who, day):
    """要改的那句没了的、太久没再出现的扔掉；太多了留最近出现过的。"""
    cutoff = (date.fromisoformat(day) - timedelta(days=OBSERVE_DAYS)).isoformat()
    line_ids = {line.id for line in who.lines}
    who.observing = [c for c in who.observing
                     if (not c.replaces or c.replaces in line_ids) and max(c.days, default="") >= cutoff]
    who.observing = sorted(who.observing, key=lambda c: max(c.days, default=""), reverse=True)[:MAX_OBSERVING]


def lines_for_review(who):
    """交给回顾的模型看的样子：现在的每一句（带编号）+ 观察中的提议（带编号和出现过几天）。"""
    now = "\n".join(f"[{line.id}] {line.text}" for line in who.lines) or "(empty: only the preset personality so far)"
    pending = "\n".join(
        f"[{c.id}] {'add' if not c.replaces else ('remove ' + c.replaces if not c.text else 'rewrite ' + c.replaces)}"
        f"{': ' + c.text if c.text else ''} (evidence on {len(c.days)} of {DAYS_TO_APPLY} days)"
        for c in who.observing) or "(none)"
    return now, pending


# ---------- 设置页 ----------

def set_text(text, path=None):
    """你在设置里改了全文：马上生效（一行一句，先存旧版）。"""
    who = load(path)
    new = [_clean(t) for t in str(text or "").splitlines() if _clean(t)][:MAX_LINES]
    if new == [line.text for line in who.lines]:
        return
    _backup(who, _now())
    kept = {line.text: line for line in who.lines}   # 没改的句子留着原来的编号（观察中的提议还对得上）
    lines = []
    for sentence in new:
        line = kept.pop(sentence, None) or Line(_next_id(who.lines + lines, "s"), sentence)
        lines.append(line)
    who.lines = lines
    ids = {line.id for line in lines}
    who.observing = [c for c in who.observing if not c.replaces or c.replaces in ids]
    save(who, path)


def restore(index, path=None):
    """退回到第 index 份旧版（versions 里的位置）。"""
    who = load(path)
    if not 0 <= index < len(who.versions):
        return False
    set_text(who.versions[index]["text"], path)
    return True


def clear(path=None):
    """全部清空（现在的、观察中的、旧版都删）。"""
    save(WhoAmI(), path)
