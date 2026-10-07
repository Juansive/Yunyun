"""芸芸和助手子进程之间的消息格式（assistant/protocol.py）。"""
import json
import os
import subprocess
import sys
import time

import pytest

from yunyun.paths import PROJECT_ROOT

from yunyun.assistant import protocol
from yunyun.assistant.protocol import (
    COMMANDS, EVENTS, Ask, Error, PermissionRequest, ProtocolError, TextDelta, TurnFinished, decode, encode,
)

SAMPLES = [
    protocol.Ask("帮我看看这个报错"),
    protocol.Interrupt(),
    protocol.PermissionReply(3, False, "这次先不要"),
    protocol.PermissionReply(4, True, remember=True),
    protocol.Rewind("9b1f0c2e-uuid"),
    protocol.Quit(),
    protocol.Ready("anthropic", "claude-haiku-4-5"),
    protocol.TextDelta("你好～"),
    protocol.ToolStarted("toolu_1", "WebSearch", "Python 3.13 发布日期"),
    protocol.ToolFinished("toolu_1", ok=False),
    protocol.PermissionRequest(3, "Bash", {"command": "pytest -q"}),
    protocol.PermissionRequest(4, "Bash", {"command": "rm x"}, "command", False, "rm", True, "toolu_9"),
    protocol.ToolDecision("toolu_9", "Bash", "denied", "rm x"),
    protocol.TurnFinished(0.0123, 0.05, 1200, 80),
    protocol.TurnFinished(stopped=True),
    protocol.TurnFinished(0.01, 0.02, checkpoint="9b1f0c2e-uuid", files=("F:\\proj\\a.py", "F:\\proj\\b.py")),
    protocol.Rewound("9b1f0c2e-uuid", True),
    protocol.Rewound("9b1f0c2e-uuid", False, "No checkpoint"),
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


def test_read_lines_joins_lines_split_across_reads():
    read_end, write_end = os.pipe()
    os.write(write_end, b'{"type": "quit"}\n{"type": "ask", ')
    os.write(write_end, '"text": "你好"}\n最后一行没有换行'.encode("utf-8"))
    os.close(write_end)   # 管道断了：读到头
    lines = list(protocol.read_lines(read_end))
    os.close(read_end)
    assert [decode(line) for line in lines[:2]] == [protocol.Quit(), Ask("你好")]
    assert lines[2] == "最后一行没有换行".encode("utf-8")


def test_reader_thread_does_not_crash_the_process_on_exit():
    """读线程卡着等标准输入时主程序结束：要正常、马上退出。

    以前用 `for line in sys.stdin.buffer`，退出时 Python 拿不到 sys.stdin 的锁，等 2.5 秒后崩溃（退出码 0xC0000005）。
    """
    code = ("import threading\n"
            "from yunyun.assistant.protocol import read_lines\n"
            "threading.Thread(target=lambda: list(read_lines(0)), daemon=True).start()\n"
            "print('started', flush=True)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=PROJECT_ROOT, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout.readline().strip() == b"started"
        started = time.time()
        proc.wait(timeout=10)                       # 标准输入还开着（读线程正卡着），主程序已经结束
        assert proc.returncode == 0, proc.stderr.read()
        assert time.time() - started < 1.5
    finally:
        proc.stdin.close()
        if proc.poll() is None:
            proc.kill()
