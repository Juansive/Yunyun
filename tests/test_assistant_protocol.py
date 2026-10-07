"""芸芸和助手子进程之间的消息格式（assistant/protocol.py）。"""
import json

import pytest

from yunyun.assistant import protocol
from yunyun.assistant.protocol import (
    COMMANDS, EVENTS, Ask, Error, PermissionRequest, ProtocolError, TextDelta, TurnFinished, decode, encode,
)

SAMPLES = [
    protocol.Ask("帮我看看这个报错"),
    protocol.Interrupt(),
    protocol.PermissionReply(3, False, "这次先不要"),
    protocol.Quit(),
    protocol.Ready("anthropic", "claude-haiku-4-5"),
    protocol.TextDelta("你好～"),
    protocol.ToolStarted("toolu_1", "WebSearch", "Python 3.13 发布日期"),
    protocol.ToolFinished("toolu_1", ok=False),
    protocol.PermissionRequest(3, "Bash", {"command": "pytest -q"}),
    protocol.TurnFinished(0.0123, 0.05, 1200, 80),
    protocol.TurnFinished(stopped=True),
    protocol.Error("auth", "401 invalid x-api-key"),
]


def test_every_message_type_has_a_sample():
    assert {type(m) for m in SAMPLES} == set(COMMANDS + EVENTS)


@pytest.mark.parametrize("message", SAMPLES, ids=lambda m: m.TYPE)
def test_roundtrip(message):
    assert decode(encode(message)) == message


def test_one_message_is_exactly_one_line():
    line = encode(TextDelta("第一行\n第二行\r\n第三行"))
    assert line.endswith(b"\n") and line.count(b"\n") == 1   # 文字里的换行被转义了
    assert decode(line).text == "第一行\n第二行\r\n第三行"


def test_chinese_is_kept_as_utf8():
    line = encode(Ask("芸芸"))
    assert "芸芸".encode("utf-8") in line                       # 不是 芸 这种转义
    assert json.loads(line) == {"type": "ask", "text": "芸芸"}


def test_unknown_fields_are_ignored():
    """以后给消息加了字段，旧版本照样能读。"""
    line = json.dumps({"type": "text", "text": "hi", "new_field": 1})
    assert decode(line) == TextDelta("hi")


def test_defaults_fill_missing_optional_fields():
    assert decode('{"type": "turn_finished"}') == TurnFinished()
    assert decode('{"type": "error", "kind": "network"}') == Error("network")


@pytest.mark.parametrize("line", [
    "不是 JSON",
    '{"text": "没有 type"}',
    '{"type": "no_such_type"}',
    '["type", "ask"]',
    '{"type": "ask"}',                                          # 缺了必须的 text
    b"\xff\xfe",
])
def test_bad_lines_raise_protocol_error(line):
    with pytest.raises(ProtocolError):
        decode(line)


def test_permission_request_keeps_nested_input():
    message = PermissionRequest(1, "Edit", {"file_path": "a.py", "edits": [{"old": "x", "new": "y"}]})
    assert decode(encode(message)).input == message.input


def test_error_kinds_are_unique():
    assert len(set(protocol.ERROR_KINDS)) == len(protocol.ERROR_KINDS)
