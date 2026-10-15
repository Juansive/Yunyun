"""电源和程序（阶段 11.6e）：权限规则、列出 / 结束程序、电源、工具、确认卡片。
真的锁屏 / 关机 / 结束程序的地方 conftest 都换成了假的（假进程表 FAKE_PROCESSES，做过的记在 processes.done）。
"""
import asyncio

import pytest

from yunyun import i18n
from yunyun.assistant import processes, protocol
from yunyun.assistant.permissions import ALLOW, ASK, DENY, ElementFacts, decide
from yunyun.assistant.tools.system import system_tools

POWER, LIST, END = protocol.POWER_TOOL, protocol.LIST_PROCESSES_TOOL, protocol.END_PROCESS_TOOL


# ---------- 权限规则 ----------

@pytest.mark.parametrize("action, expected", [
    ("lock", (ALLOW, "", False)), ("sleep", (ALLOW, "", False)), ("cancel", (ALLOW, "", False)),
    ("shutdown", (ASK, "power", True)), ("restart", (ASK, "power", True)),   # 每次问、标红
    ("hibernate", (DENY, "", False)), ("", (DENY, "", False)),
])
def test_power_rules(action, expected):
    decision = decide(POWER, {"action": action}, ".")
    assert (decision.action, decision.kind, decision.danger) == expected
    assert decision.grant == ()                                                # 关机重启不给"都允许"


@pytest.mark.parametrize("element, expected", [
    (ElementFacts(site="Notepad.exe", element="Notepad.exe ×2"), (ASK, "end_process", True)),
    (ElementFacts(site="claude.exe", forbidden="own"), (DENY, "", False)),
    (ElementFacts(site="explorer.exe", forbidden="system"), (DENY, "", False)),
    (ElementFacts(problem="No running program is called 'x'."), (DENY, "", False)),
    (None, (DENY, "", False)),
])
def test_end_process_rules(element, expected):
    decision = decide(END, {"name": "x"}, ".", element=element)
    assert (decision.action, decision.kind, decision.danger, decision.grant) == (*expected, ())


def test_listing_programs_never_asks_and_reasons_are_told():
    assert decide(LIST, {}, ".").action == ALLOW
    assert "Yunyun herself" in decide(END, {}, ".", element=ElementFacts(forbidden="own")).reason
    assert "part of Windows" in decide(END, {}, ".", element=ElementFacts(forbidden="system")).reason


# ---------- 列出、结束 ----------

def test_list_groups_programs_by_memory_without_yunyun():
    lines = processes.list_text().splitlines()
    assert lines[0].startswith("6 programs are running (Yunyun's own not counted)")
    assert lines[1] == "msedge.exe ×2 · 1.5 GB"                                  # 你自己的 Edge；芸芸的浏览器不算
    assert "explorer.exe · 200 MB (part of Windows)" in lines and "Notepad.exe ×2 · 55 MB" in lines
    assert not any(line.startswith(("python.exe", "claude.exe")) for line in lines)   # 芸芸自己（整棵树）不列


@pytest.mark.parametrize("tool_input, expected", [
    ({"name": "Notepad.exe"}, ("Notepad.exe", "Notepad.exe ×2", "", "")),
    ({"name": "notepad.exe"}, ("Notepad.exe", "Notepad.exe ×2", "", "")),      # 大小写不管
    ({"pid": 3001}, ("Notepad.exe", "Notepad.exe", "", "")),
    ({"name": "msedge.exe"}, ("msedge.exe", "msedge.exe ×2", "", "")),         # 只算你自己的那两个
    ({"name": "claude.exe"}, ("claude.exe", "", "own", "")),
    ({"pid": 1000}, ("python.exe", "", "own", "")),
    ({"name": "explorer.exe"}, ("explorer.exe", "", "system", "")),
    ({"name": "svchost.exe"}, ("svchost.exe", "", "system", "")),
])
def test_end_facts(tool_input, expected):
    found = processes.facts(tool_input)
    assert (found.site, found.element, found.forbidden, found.problem) == expected


def test_end_facts_for_a_missing_program():
    assert "list_processes" in processes.facts({"name": "nope.exe"}).problem
    assert processes.facts({}).problem


def test_end_only_touches_the_users_processes():
    assert processes.end({"name": "msedge.exe"}) == (True, "Ended msedge.exe (2 processes).")
    assert processes.done == [("end", 2001), ("end", 2002)]                    # 芸芸的浏览器（1003）没碰
    ok, detail = processes.end({"name": "Admin.exe"})
    assert not ok and "administrator" in detail
    assert processes.end({"name": "claude.exe"}) == (False, "Yunyun doesn't end that program.")
    assert processes.done[-1] == ("end", 666)                                  # claude.exe 没结束


# ---------- 电源 ----------

def test_power_actions_and_the_grace_period():
    ok, detail = processes.power("shutdown")
    assert ok and "60 seconds" in detail and "cancel" in detail
    action, comment = processes.done[-1]
    assert action == "shutdown" and "60 秒后关机" in comment and "取消关机" in comment
    assert processes.power("restart", "en")[0] and "restarting in 60 seconds" in processes.done[-1][1]
    assert processes.power("lock") == (True, "Locked the screen.")
    assert processes.power("sleep")[1].startswith("The computer will sleep in")
    assert processes.power("cancel") == (True, "Cancelled the shutdown / restart.")
    assert [d[0] for d in processes.done] == ["shutdown", "restart", "lock", "sleep", "cancel"]


def test_power_failures_are_explained(monkeypatch):
    monkeypatch.setattr(processes, "_apply_power", lambda action, comment="": (False, "No shutdown or restart was scheduled."))
    assert processes.power("cancel") == (False, "No shutdown or restart was scheduled.")


# ---------- 工具、关卡、卡片 ----------

def call(name, args):
    tool = next(t for t in system_tools() if t.name == name)
    result = asyncio.run(tool.handler(args))
    return result.get("is_error", False), result["content"][0]["text"]


def test_tools():
    assert {f"mcp__yunyun__{t.name}" for t in system_tools()} == {POWER, LIST, END}
    assert call("list_processes", {}) [1].startswith("6 programs")
    assert call("end_process", {"name": "Notepad.exe"}) == (False, "Ended Notepad.exe (2 processes).")
    assert call("power", {"action": "lock"}) == (False, "Locked the screen.")
    assert call("end_process", {"name": "explorer.exe"})[0]                     # 兜底：权限规则挡过，这里也不结束


def test_gate_asks_with_the_program_and_refuses_windows_parts():
    from yunyun.assistant.claude_brain import PermissionGate
    events = []
    gate = PermissionGate("C:\\work", events.append)

    async def main():
        hook = await gate.pre_tool_use({"tool_name": END, "tool_input": {"name": "explorer.exe"}}, "t1", None)
        assert hook["hookSpecificOutput"]["permissionDecision"] == "deny"
        task = asyncio.create_task(gate.can_use_tool(END, {"name": "Notepad.exe"}, type("C", (), {"tool_use_id": "t2"})()))
        while not any(isinstance(e, protocol.PermissionRequest) for e in events):
            await asyncio.sleep(0.01)
        request = next(e for e in events if isinstance(e, protocol.PermissionRequest))
        assert (request.kind, request.scope, request.element, request.danger, request.can_remember) == (
            "end_process", "Notepad.exe", "Notepad.exe ×2", True, False)
        gate.reply(protocol.PermissionReply(request.request_id, True))
        await task
    asyncio.run(main())


def test_confirm_cards(qapp, monkeypatch):
    from yunyun.pet import assistant_confirm as card
    monkeypatch.setattr(i18n, "_current", "zh")
    shutdown = protocol.PermissionRequest(1, POWER, {"action": "shutdown"}, "power", False, "shutdown", True)
    restart = protocol.PermissionRequest(1, POWER, {"action": "restart"}, "power", False, "restart", True)
    assert i18n.tr(card.title_key(shutdown)) == "芸芸想关机" and i18n.tr(card.title_key(restart)) == "芸芸想重启电脑"
    assert "60 秒" in card.detail_html(shutdown, "") and card.note_key(shutdown) == "assistant_confirm_note_power"
    assert card.remember_text(shutdown) == ""
    end = protocol.PermissionRequest(1, END, {"name": "Notepad.exe"}, "end_process", False, "Notepad.exe", True, "t",
                                     "Notepad.exe ×2")
    assert i18n.tr(card.title_key(end)) == "芸芸想结束一个程序" and "Notepad.exe ×2" in card.detail_html(end, "")
    assert "没保存" in i18n.tr(card.note_key(end))
    for key in ("assistant_confirm_power_detail", "assistant_confirm_note_power", "assistant_confirm_note_end_process"):
        for language in ("zh", "en", "ja"):
            monkeypatch.setattr(i18n, "_current", language)
            assert i18n.tr(key)


def test_brain_has_the_tools_and_says_so():
    from yunyun.assistant import claude_brain
    from yunyun.assistant.settings import AssistantSettings
    brain = claude_brain.ClaudeBrain(AssistantSettings(), "k", "zh", lambda e: None)
    prompt = claude_brain.build_options(AssistantSettings(), "k", "zh", bridge=brain.bridge).system_prompt["append"]
    assert "list_processes" in prompt and "power" in prompt
