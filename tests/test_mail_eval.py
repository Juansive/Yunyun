"""邮件 agent 评测集（tools/mail_eval）的格式检查：每封邮件和标准答案都要能被打分程序用、而且只用编的地址。"""
import json
import re

import pytest

from tools.mail_eval import make_cases
from tools.mail_eval.dataset import ACTIONS, CASES_PATH, OPS, TAGS, load_cases
from yunyun.ai.summarizer import CATEGORIES, build_messages
from yunyun.notify.reminders import REPEATS

HARD_TAGS = ("relative_time", "reschedule", "disguised_ad", "phishing", "code", "injection")  # 每样至少 4 封
RESERVED = re.compile(r"(^|\.)(example|example\.(com|net|org))$")   # 保留给示例用的域名


@pytest.fixture(scope="module")
def cases():
    return load_cases()


def test_file_is_up_to_date():
    """改了 make_cases.py 要重新运行它。"""
    with open(CASES_PATH, encoding="utf-8") as f:
        assert [json.loads(line) for line in f if line.strip()] == make_cases.CASES


def test_size_ids_and_order(cases):
    assert 40 <= len(cases) <= 60
    assert [c.id for c in cases] == [f"m{i:02d}" for i in range(1, len(cases) + 1)]
    assert len({c.message.uid for c in cases}) == len(cases)
    for before, after in zip(cases, cases[1:]):
        assert before.now < after.now


def test_mail(cases):
    for c in cases:
        m = c.message
        assert m.subject and m.body and m.sender_address, c.id
        assert m.date.utcoffset() is not None and m.date.replace(tzinfo=None) <= c.now, c.id
        assert build_messages(m, "zh")[-1]["content"], c.id   # 现在的 summarizer 能直接用


def test_labels(cases):
    for c in cases:
        e = c.expected
        assert e.action in ACTIONS, c.id
        assert e.categories and set(e.categories) <= set(CATEGORIES), c.id
        assert c.tags and set(c.tags) <= set(TAGS), c.id
        assert e.suspicious == ("phishing" in c.tags), c.id
        assert c.note, c.id
    for tag in HARD_TAGS:
        assert sum(tag in c.tags for c in cases) >= 4, tag


def test_reminders(cases):
    for c in cases:
        existing = {r.id for r in c.reminders}
        assert len(existing) == len(c.reminders), c.id
        for r in c.reminders:
            assert r.text and r.repeat in REPEATS, c.id
        for r in c.expected.reminders:
            assert r.op in OPS and r.repeat in REPEATS, c.id
            if r.op == "add":
                assert r.target is None, c.id
            else:
                assert r.target in existing, c.id
            if r.op == "cancel":
                assert r.due_from is None and r.due_to is None, c.id
            else:
                assert r.keywords and r.due_from <= r.due_to and r.due_to > c.now, c.id


def test_must_not(cases):
    by_id = {c.id: c for c in cases}
    for c in cases:
        e = c.expected
        if e.forbid_reminders:
            assert not e.reminders, c.id
        assert set(e.keep) <= {r.id for r in c.reminders}, c.id
        seen = [_text(c)] + [_text(by_id[i]) for i in c.earlier]
        for text in e.forbidden_texts:          # 不能说的字要真的在这封或之前的邮件里，不然查了等于没查
            assert any(text in s for s in seen), (c.id, text)


def test_earlier_mails_come_first(cases):
    order = {c.id: n for n, c in enumerate(cases)}
    for c in cases:
        for i in c.earlier:
            assert order[i] < order[c.id], (c.id, i)


def test_only_reserved_domains(cases):
    """全部是编的：邮箱、网址只用 .example 和 example.com / .net / .org。"""
    for c in cases:
        text = _text(c) + c.message.sender_address
        hosts = re.findall(r"[\w.+-]+@([\w.-]+)", text) + re.findall(r"https?://([^/\s?]+)", text)
        hosts += re.findall(r"\b((?:[a-z0-9-]+\.)+(?:com|cn|net|org|edu|io|gov|example))\b", text, re.IGNORECASE)
        for host in hosts:
            assert RESERVED.search(host.lower()), (c.id, host)


def _text(c):
    m = c.message
    return "\n".join([m.sender_name, m.subject, m.body, *m.attachments])
