"""回想（阶段 11.3）：新的一段对话开始时，芸芸在后台回想上一两段还没回想过的对话，更新相处笔记（rapport.py），
顺便写一句摘要 + 几个关键词存进回忆（recollections.py，阶段 11.4；同一次调用，几乎不多花钱）。

**只在助手子进程里 import**（直接用 anthropic SDK 调一次模型，芸芸进程不碰）。设计见 docs/助手模式计划.md 11.3：
- 只读问答文字（transcripts.read_transcript），不读工具输出；太长的只留后面的部分；
- 用那家服务商便宜的模型（small_model，没有就 models[0]：Haiku / deepseek-flash），短对话约 0.2 美分、长的不到 1 美分
  （Haiku，tools/eval_rapport.py 实测）；
- 回想完发 protocol.Reflected（花了多少、加了几条、几条开始生效），芸芸记账、有新的生效时冒个气泡；
- 出错只记日志（种类），不打扰；没回想成的下次再试。日志不写对话和笔记的内容。
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta

from yunyun.assistant import protocol, rapport, recollections, transcripts
from yunyun.assistant.environment import system_proxy_env
from yunyun.assistant.persona import character

log = logging.getLogger(__name__)

MAX_TALKS = 2            # 一次最多回想几段
LOOKBACK_DAYS = 7        # 只回想最近几天的
MIN_CHARS = 30           # 问答加起来太少（打个招呼）不值得花钱：直接当回想过
TALK_CHARS = 8_000       # 交给模型的问答最多多少字（太长的留后面的部分）
ANSWER_CHARS = 600       # 每个回答最多带多少字（代码、长解释对"怎么相处"没用）
MAX_TOKENS = 700
TIMEOUT_S = 60
LANGUAGE_NAMES = {"zh": "Chinese", "en": "English", "ja": "Japanese"}

INSTRUCTIONS = """You keep "rapport notes" for Yunyun, a desktop-pet assistant: short notes on how to get along with this \
particular user — how they like to be addressed, tone and humor they enjoy or dislike, sensitive topics, how to support \
them when they are tired or stressed, running jokes or shared moments, the answer style they prefer.
Not facts about their projects, files or tasks (another notebook keeps those). No secrets or personal data. No judgments \
of the user's character. Nothing that would make Yunyun rude, dishonest or unsafe.

You also write a one-line summary of the conversation, so Yunyun can recall it later.

You get Yunyun's preset personality (fixed: never restate it as a note), the current notes, and one past conversation. \
Reply with JSON only, no other text:
{{"seen": ["ids of notes this conversation shows again"],
 "revise": [{{"id": "r1", "text": "better wording"}}],
 "drop": ["ids this conversation clearly contradicts"],
 "add": [{{"text": "a new note", "explicit": false}}],
 "summary": "what this conversation was about",
 "keywords": ["words to find it by"]}}
Summary and keywords:
- "summary": one sentence in {language} (under 40 characters for Chinese or Japanese, under 20 words otherwise) from \
Yunyun's side: what you did together and anything worth following up later (a plan, a deadline, how the user felt), \
e.g. "Fixed the user's pytest error; they hand in the first draft on Friday". No secrets, passwords or keys.
- "keywords": 3 to 6 short search words in {language} (topics, project or file names, people).
- In summaries and notes, refer to the user as "the user" (or the name they asked to be called); never guess their \
gender (no he / she).
Rules for the notes:
- Use only evidence from this conversation. If nothing stands out, return empty lists — most conversations add nothing.
- "explicit": true only when the user directly asked for it (e.g. "don't call me that", "be more direct with me").
- At most 3 adds. If an existing note already covers something, put its id in "seen" instead of adding.
- Put an id in "seen" only when this conversation itself shows that note again — not just because nothing contradicts it.
- Each note is one short sentence in {language} (under 40 characters for Chinese or Japanese, under 15 words otherwise), \
written as advice to Yunyun, e.g. "When the user is tired, comfort first; no teasing"."""

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def model_for(provider):
    return provider.small_model or provider.models[0]


def pending_talks(data, projects_dir, current="", now=None):
    """该回想的对话：最近 LOOKBACK_DAYS 天的、不是正在聊的这段、还没回想过（或回想以后又接着聊过）→ [(Transcript, 更新时间)]，
    最近的在前，最多 MAX_TALKS 段。"""
    cutoff = (now or datetime.now()) - timedelta(days=LOOKBACK_DAYS)
    found = []
    for talk in transcripts.list_transcripts(projects_dir):
        if talk.session_id == current or talk.updated is None or talk.updated < cutoff or not talk.turns:
            continue
        updated = talk.updated.isoformat(timespec="seconds")
        if rapport.needs_reflection(data, talk.session_id, updated):
            found.append((talk, updated))
    return sorted(found, key=lambda item: item[1], reverse=True)[:MAX_TALKS]


def talk_text(talk, language, limit=TALK_CHARS):
    """一段对话 → 交给模型的问答文字（旧的在前；超过 limit 字从前面扔，每个回答截一截）。"""
    me = "芸芸" if language in ("zh", "ja") else "Yunyun"
    lines, size = [], 0
    for turn in reversed(talk.turns):
        answer = " ".join(turn.answer.split())
        if len(answer) > ANSWER_CHARS:
            answer = answer[:ANSWER_CHARS] + "…"
        block = f"User: {' '.join(turn.question.split())}\n{me}: {answer or '(no answer)'}"
        if size + len(block) > limit and lines:
            break
        lines.append(block)
        size += len(block)
    return "\n\n".join(reversed(lines))


def notes_text(data):
    lines = [f"[{n.id}] ({'active' if n.active else f'observing, seen in {len(n.sessions)} chat'}) {n.text}"
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


async def reflect_recent(settings, api_key, language, emit, current="", projects_dir=None, ask=complete, now=None):
    """回想上一两段对话（settings.rapport 关着就什么都不做）。ask：调模型的函数；now：现在几点（测试用）。"""
    if not settings.rapport:
        return
    if projects_dir is None:
        from yunyun.assistant.claude_brain import CONFIG_DIR   # 子进程里本来就有（claude_brain 会 import SDK）
        projects_dir = CONFIG_DIR / "projects"
    os.environ.update(system_proxy_env())   # 只开了系统代理时让 SDK 走代理（和 check.py 一样）
    provider = settings.info
    model = model_for(provider)
    rates = provider.rates(model)
    language = language if language in LANGUAGE_NAMES else "zh"
    system = INSTRUCTIONS.format(language=LANGUAGE_NAMES[language])
    preset = character(language, settings.personality, settings.personality_text)
    for talk, updated in pending_talks(rapport.load(), projects_dir, current, now):
        text = talk_text(talk, language)
        if len(text) < MIN_CHARS:
            data = rapport.load()
            rapport.apply(data, {}, talk.session_id, updated)   # 只打了个招呼：当回想过，不花钱
            rapport.save(data)
            continue
        data = rapport.load()
        user = (f"# Yunyun's preset personality\n{preset}\n# Current notes\n{notes_text(data)}\n\n"
                f"# The conversation (oldest first; long parts cut)\n{text}")
        try:
            reply, input_tokens, output_tokens = await ask(provider, api_key, model, system, user)
        except Exception as e:   # 没网、Key 不对、超时……只记种类，下次再试
            log.info("回想没做成：%s", type(e).__name__)
            return
        cost = rates.cost(input_tokens=input_tokens, output_tokens=output_tokens) if rates else 0.0
        changes = parse(reply)
        data = rapport.load()   # 调模型的时候设置页可能改过笔记：重新读
        outcome = rapport.apply(data, changes, talk.session_id, updated)
        rapport.save(data)
        recollections.remember(talk.session_id, updated, changes.get("summary"), changes.get("keywords"), talk.cwd,
                               now=now)
        log.info("回想了一段对话（%d 轮）：加了 %d 条，%d 条开始生效，约 %.4f 美元", len(talk.turns), outcome.added,
                 outcome.activated, cost)
        emit(protocol.Reflected(cost, outcome.added, outcome.activated))
