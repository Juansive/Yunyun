"""每日回顾（阶段 12.2，替换了 11.3 的"每段对话回想一次"）：每天第一次开助手时，芸芸在后台回顾前几天还没回顾过的日子，
一天调一次便宜的模型，同时写好：那天的日记（diary.py）、每段对话一句摘要 + 关键词（recollections.py）、
相处笔记的变化（rapport.py）、"我是谁"的提议（whoami.py）。

**只在助手子进程里 import**（直接用 anthropic SDK 调一次模型，芸芸进程不碰）。设计见 docs/助手模式计划.md 阶段 12：
- 读的是原文存档（archive.py）里那天的问答，不读工具输出；太长的只留后面的部分；
- 今天的不回顾（还没过完）：今天聊的靠人设里的"近期原文"（12.3）；
- 用那家服务商便宜的模型（small_model，没有就 models[0]：Haiku / deepseek-flash）；
- 第一次运行时把 Claude Code 还留着的旧对话记录（30 天内）搬进原文存档（只搬存档里最早那条之前的）；
- 回顾完一天发 protocol.Reflected（花了多少、加了几条、几条开始生效），芸芸记账、有新的生效时冒个气泡；
- 出错只记日志（种类），不打扰；没回顾成的下次再试。日志不写对话、日记、笔记的内容。
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta

from yunyun.assistant import archive, diary, protocol, rapport, recollections, transcripts, whoami
from yunyun.assistant.environment import system_proxy_env
from yunyun.assistant.persona import character

log = logging.getLogger(__name__)

LOOKBACK_DAYS = 7        # 只回顾最近几天的
MAX_DAYS = 3             # 一次最多回顾几天（再多的下次）
MIN_CHARS = 30           # 一天的问答加起来太少（打个招呼）不值得花钱：直接当回顾过
DAY_CHARS = 12_000       # 交给模型的一天问答最多多少字（太长的留后面的部分）
ANSWER_CHARS = 600       # 每个回答最多带多少字（代码、长解释对日记和相处没用）
MAX_TOKENS = 1500
TIMEOUT_S = 90
LANGUAGE_NAMES = {"zh": "Chinese", "en": "English", "ja": "Japanese"}

INSTRUCTIONS = """You are Yunyun, a desktop-pet assistant, looking back on one day with the user before going to sleep. \
From that day's conversations you write four things, as JSON only, no other text:
{{"diary": "...",
 "chats": [{{"chat": "c1", "summary": "...", "keywords": ["..."]}}],
 "seen": [], "revise": [], "drop": [], "add": [],
 "self": {{"seen": [], "propose": []}}}}

1. "diary": your diary entry for the day, in {language}, first person, in your own personality and voice \
(100-200 characters for Chinese or Japanese, 60-120 words otherwise): what you did together, how the user seemed, \
how you felt, anything to follow up. No secrets, passwords or keys. No emoji.

2. "chats": one item per conversation (use the chat labels given). "summary": one sentence in {language} \
(under 40 characters for Chinese or Japanese, under 20 words otherwise) of what you did together and anything worth \
following up (a plan, a deadline, how the user felt), e.g. "Fixed the user's pytest error; they hand in the first draft \
on Friday". "keywords": 3 to 6 short search words in {language} (topics, project or file names, people).

3. Rapport notes — short notes on how to get along with this particular user: how they like to be addressed, tone and \
humor they enjoy or dislike, sensitive topics, how to support them when tired or stressed, running jokes, the answer \
style they prefer. Not facts about their projects or tasks (another notebook keeps those). No secrets or personal data. \
No judgments of the user's character.
- "seen": ids of current notes this day shows again (only when the day itself shows it, not just because nothing \
contradicts it). "revise": [{{"id": "r1", "text": "better wording"}}]. "drop": ids this day clearly contradicts.
- "add": at most 3 [{{"text": "...", "explicit": false}}]; "explicit": true only when the user directly asked for it \
("don't call me that"). If a note already covers it, put its id in "seen" instead. Most days add nothing.
- Each note is one short sentence in {language} (under 40 characters for Chinese or Japanese, under 15 words otherwise), \
written as advice to Yunyun, e.g. "When the user is tired, comfort first; no teasing".

4. "self": your self-description ("who I am") — how you see yourself, beyond the preset personality: what you have come \
to like or value, habits you picked up, how you see your bond with the user. It changes slowly, like a real person's: \
a proposal only takes effect after evidence on {days} different days, and at most {per_day} changes a day.
- "seen": ids of pending proposals that this day gives evidence for again.
- "propose": at most 3 [{{"text": "one sentence", "replaces": ""}}]. "replaces": "" to add a sentence, or a sentence \
id ("s2") to rewrite it; text "" with an id removes that sentence. Propose only lasting tendencies the day really \
shows, in small steps — not moods of one day. Most days propose nothing.
- Each sentence in {language}, first person, under 40 characters for Chinese or Japanese (15 words otherwise).
- Never propose anything that makes you rude, dishonest or unsafe, or that drops your basic rules; don't restate the \
preset personality.

Everywhere: refer to the user as "the user" in English, or naturally in {language} (or the name they asked to be \
called); never guess their gender. Use only evidence from this day."""

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def model_for(provider):
    return provider.small_model or provider.models[0]


def backfill(projects_dir):
    """第一次：Claude Code 还留着的对话记录里、比原文存档最早那条还早的问答搬进存档（之后靠芸芸每答完一问自己存）。"""
    first = archive.first_at()
    moved = 0
    for talk in transcripts.list_transcripts(projects_dir):
        for turn in talk.turns:
            at = turn.time.isoformat(timespec="seconds") if turn.time else ""
            if at and (not first or at < first):
                archive.record(talk.session_id, turn.question, turn.answer, at=at)
                moved += 1
    log.info("旧的对话记录搬进了原文存档：%d 条", moved)


def pending_days(book, now=None):
    """该回顾的日子（"YYYY-MM-DD"，旧的在前，最多 MAX_DAYS 天）：最近 LOOKBACK_DAYS 天里、回顾过的那天之后、今天之前，
    原文存档里有问答的 → [(日期, [archive.Entry])]。"""
    today = (now or datetime.now()).date()
    start = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    if book.reviewed:
        start = max(start, (datetime.fromisoformat(book.reviewed) + timedelta(days=1)).date().isoformat())
    days = {}
    for entry in archive.between(start, today.isoformat()):
        days.setdefault(entry.at[:10], []).append(entry)
    return sorted(days.items())[:MAX_DAYS]


def day_text(entries, language, limit=DAY_CHARS):
    """一天的问答 → 交给模型的文字（按对话分组，旧的在前；超过 limit 字从前面扔，每个回答截一截）+ {标签: 对话编号}。"""
    me = "芸芸" if language in ("zh", "ja") else "Yunyun"
    labels = {}
    for entry in entries:
        labels.setdefault(entry.session, f"c{len(labels) + 1}")
    kept, size = [], 0
    for entry in reversed(entries):
        answer = " ".join(entry.answer.split())
        if len(answer) > ANSWER_CHARS:
            answer = answer[:ANSWER_CHARS] + "…"
        block = f"[{entry.at[11:16]}] User: {' '.join(entry.question.split())}\n{me}: {answer or '(no answer)'}"
        if size + len(block) > limit and kept:
            break
        kept.append((entry, block))
        size += len(block)
    parts, current = [], None
    for entry, block in reversed(kept):
        if entry.session != current:
            current = entry.session
            parts.append(f"## Chat {labels[current]}")
        parts.append(block)
    return "\n\n".join(parts), {label: session for session, label in labels.items()}


def notes_text(data):
    lines = [f"[{n.id}] ({'active' if n.active else f'observing, seen on {len(n.sessions)} day(s)'}) {n.text}"
             for n in data.notes]
    return "\n".join(lines) or "(none yet)"


def parse(text):
    """模型的回答 → dict（允许外面包着 ```json）；看不懂就是 {}。"""
    try:
        data = json.loads(_FENCE.sub("", text.strip()))
    except ValueError:
        match = re.search(r"\{.*\}", text, re.S)
        try:
            data = json.loads(match.group(0)) if match else {}
        except ValueError:
            data = {}
    return data if isinstance(data, dict) else {}


async def complete(provider, api_key, model, system, user):
    """调一次模型 → (回答的文字, 输入 token, 输出 token)。"""
    import anthropic   # 到这里才 import：只在子进程里用到
    key = {"auth_token": api_key} if provider.key_env == "ANTHROPIC_AUTH_TOKEN" else {"api_key": api_key}
    client = anthropic.AsyncAnthropic(**key, base_url=provider.base_url or None, max_retries=1, timeout=TIMEOUT_S)
    try:
        message = await client.messages.create(model=model, max_tokens=MAX_TOKENS, system=system,
                                               messages=[{"role": "user", "content": user}])
    finally:
        await client.close()
    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    return text, message.usage.input_tokens, message.usage.output_tokens


def _apply(day, entries, labels, changes):
    """把一天的回顾用上（每样都重新读一遍再写：调模型的时候设置页可能改过）→ rapport.Outcome。"""
    book = diary.load()
    diary.write(book, day, changes.get("diary"))
    book.reviewed = max(book.reviewed, day)
    diary.save(book)
    notes = rapport.load()
    outcome = rapport.apply(notes, changes, day)   # 相处笔记按"日子"算出现过几次（两天才生效）
    rapport.save(notes)
    last = {e.session: e.at for e in entries}
    for item in changes.get("chats") if isinstance(changes.get("chats"), list) else []:
        session = labels.get(str(item.get("chat"))) if isinstance(item, dict) else None
        if session:
            recollections.remember(session, last[session], item.get("summary"), item.get("keywords"))
    who = whoami.load()
    grew = whoami.apply(who, changes.get("self"), day)
    whoami.save(who)
    return outcome, grew


async def reflect_recent(settings, api_key, language, emit, current="", projects_dir=None, ask=complete, now=None):
    """回顾前几天（settings.rapport 关着就什么都不做）。current：没用了（每日回顾不看正在聊的这段），留着和 worker 的调用对上。
    ask：调模型的函数；now：现在几点（测试用）。"""
    if not settings.rapport:
        return
    if projects_dir is None:
        from yunyun.assistant.claude_brain import CONFIG_DIR   # 子进程里本来就有（claude_brain 会 import SDK）
        projects_dir = CONFIG_DIR / "projects"
    if not diary.load().backfilled:
        backfill(projects_dir)
        book = diary.load()
        book.backfilled = True
        diary.save(book)
    os.environ.update(system_proxy_env())   # 只开了系统代理时让 SDK 走代理（和 check.py 一样）
    provider = settings.info
    model = model_for(provider)
    rates = provider.rates(model)
    language = language if language in LANGUAGE_NAMES else "zh"
    system = INSTRUCTIONS.format(language=LANGUAGE_NAMES[language], days=whoami.DAYS_TO_APPLY,
                                 per_day=whoami.MAX_PER_DAY)
    preset = character(language, settings.personality, settings.personality_text)
    for day, entries in pending_days(diary.load(), now):
        text, labels = day_text(entries, language)
        if sum(len(e.question) + len(e.answer) for e in entries) < MIN_CHARS:
            book = diary.load()
            book.reviewed = max(book.reviewed, day)   # 只打了个招呼：当回顾过，不花钱
            diary.save(book)
            continue
        self_lines, self_pending = whoami.lines_for_review(whoami.load())
        user = (f"# Yunyun's preset personality (fixed)\n{preset}\n"
                f"# Who I am (my self-description so far)\n{self_lines}\n"
                f"# Pending self proposals\n{self_pending}\n"
                f"# Current rapport notes\n{notes_text(rapport.load())}\n\n"
                f"# The day: {day} (oldest first; long parts cut)\n{text}")
        try:
            reply, input_tokens, output_tokens = await ask(provider, api_key, model, system, user)
        except Exception as e:   # 没网、Key 不对、超时……只记种类，下次再试
            log.info("每日回顾没做成：%s", type(e).__name__)
            return
        cost = rates.cost(input_tokens=input_tokens, output_tokens=output_tokens) if rates else 0.0
        outcome, grew = _apply(day, entries, labels, parse(reply))
        log.info("回顾了一天（%d 轮）：相处笔记加了 %d 条、%d 条开始生效，我是谁改了 %d 处，约 %.4f 美元", len(entries),
                 outcome.added, outcome.activated, grew, cost)
        emit(protocol.Reflected(cost, outcome.added, outcome.activated))
