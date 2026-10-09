"""打开软件（阶段 6）：开始菜单列表和按名字找（assistant/apps.py）、权限规则、两个工具（assistant/tools/apps.py）、
会话怎么转、确认卡片怎么写。不读真的开始菜单、不真的打开软件：conftest 换成了 FAKE_APPS，打开的都记在 apps.opened。
"""
import asyncio

import pytest

from conftest import FAKE_APPS
from fake_assistant_process import FakeProcess
from yunyun import i18n
from yunyun.assistant import apps, permissions, protocol
from yunyun.assistant.permissions import ALLOW, ASK, DENY
from yunyun.assistant.session import AssistantSession
from yunyun.assistant.tools.apps import app_tools
from yunyun.assistant.tools.bridge import Bridge
from yunyun.pet import assistant_confirm

CALC = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"


# ---------- 列表、按名字找 ----------

def test_list_is_sorted_cached_and_drops_strange_ids(monkeypatch):
    calls = []
    monkeypatch.setattr(apps, "_query_start_apps", lambda: calls.append(1) or [dict(i) for i in FAKE_APPS])
    names = [app.name for app in apps.list_apps()]
    assert "坏的" not in names and len(names) == len(FAKE_APPS) - 1     # AppID 里有 ".." 的不要
    assert names == sorted(names, key=str.casefold)
    apps.list_apps()
    assert len(calls) == 1                                               # 第二次用缓存
    apps.list_apps(refresh=True)
    assert len(calls) == 2


def test_list_survives_a_broken_start_menu(monkeypatch):
    def broken():
        raise OSError("PowerShell 被禁用了")
    monkeypatch.setattr(apps, "_query_start_apps", broken)
    assert apps.list_apps() == ()                                        # 读不到：空的，不报错
    monkeypatch.setattr(apps, "_query_start_apps", lambda: [{"Name": "计算器", "AppID": CALC}])
    assert len(apps.list_apps()) == 1
    monkeypatch.setattr(apps, "_query_start_apps", broken)
    assert len(apps.list_apps(refresh=True)) == 1                       # 以前读到过：先用着


def test_single_app_from_powershell_is_not_a_list(monkeypatch):
    """只有一个软件时 ConvertTo-Json 给的是一个对象（_query_start_apps 包成列表）；里面的怪东西跳过。"""
    monkeypatch.setattr(apps, "_query_start_apps", lambda: [{"Name": "计算器", "AppID": CALC}, "怪", {"Name": ""}])
    assert apps.list_apps() == (apps.App("计算器", CALC),)


def search(query):
    return [app.name for app in apps.search(query)]


def test_search_ranks_exact_then_prefix_then_contains():
    assert search("计算器") == ["计算器"]
    assert search("计算") == ["计算器"]
    assert search("visual studio") == ["Visual Studio Code", "Visual Studio Installer"]   # 都是开头一样：短的在前
    assert search("CODE") == ["Visual Studio Code"]                      # 不分大小写、包含也算
    assert search("studio code") == ["Visual Studio Code"]
    assert search("7zip") == ["7-Zip File Manager"]                      # 不管空格和标点
    assert search("installer visual") == ["Visual Studio Installer"]    # 每个词都包含
    assert search("photoshop") == [] and search("  ") == []


def test_find_exact_rereads_the_list_once_when_not_found(monkeypatch):
    assert apps.find_exact("visual studio CODE").app_id == "Microsoft.VisualStudioCode"
    calls = []
    monkeypatch.setattr(apps, "_query_start_apps", lambda: calls.append(1) or [{"Name": "新装的", "AppID": "X.New"}])
    assert apps.find_exact("新装的").app_id == "X.New"                   # 缓存里没有：重新读一次（可能刚装的）
    assert apps.find_exact("没有的") is None and len(calls) == 2


def test_launch_opens_it_through_the_apps_folder(monkeypatch):
    assert apps.launch(CALC) == (True, "")
    assert apps.opened == ["shell:AppsFolder\\" + CALC]
    assert apps.launch("..\\..\\Windows\\System32\\cmd.exe")[0] is False   # 看起来不对劲的不打开
    assert apps.launch('a" b')[0] is False and apps.launch("")[0] is False
    assert len(apps.opened) == 1

    def fail(target):
        raise OSError(2, "找不到文件")
    monkeypatch.setattr(apps, "_startfile", fail)
    ok, detail = apps.launch(CALC)
    assert not ok and "could not open" in detail


# ---------- 权限规则 ----------

KNOWN = {"计算器", "visual studio code"}


@pytest.mark.parametrize("tool, tool_input, grants, known, expected", [
    (protocol.FIND_APP_TOOL, {"query": "计算"}, (), None, (ALLOW, "")),               # 找：不用问
    (protocol.OPEN_APP_TOOL, {"name": "计算器"}, (), KNOWN, (ASK, "open_app")),        # 打开：每次问
    (protocol.OPEN_APP_TOOL, {"name": "Visual Studio Code"}, (), KNOWN, (ASK, "open_app")),
    (protocol.OPEN_APP_TOOL, {"name": "计算器"}, (("open_app", "计算器"),), KNOWN, (ALLOW, "")),   # 选过"都允许打开它"
    (protocol.OPEN_APP_TOOL, {"name": "记事本"}, (("open_app", "计算器"),), None, (ASK, "open_app")),  # 只管那一个
    (protocol.OPEN_APP_TOOL, {"name": "cmd.exe"}, (), KNOWN, (DENY, "")),            # 开始菜单里没有：拒绝
    (protocol.OPEN_APP_TOOL, {"name": ""}, (), None, (DENY, "")),
    (protocol.OPEN_APP_TOOL, {}, (), None, (DENY, "")),
])
def test_open_app_rules(tool, tool_input, grants, known, expected):
    decision = permissions.decide(tool, tool_input, "C:\\work", frozenset(grants), apps=known)
    assert (decision.action, decision.kind) == expected


def test_open_app_grant_is_per_app_and_case_insensitive():
    decision = permissions.decide(protocol.OPEN_APP_TOOL, {"name": "Visual Studio Code"}, "C:\\work", apps=KNOWN)
    assert decision.grant == (("open_app", "visual studio code"),) and decision.scope == "Visual Studio Code"
    again = permissions.decide(protocol.OPEN_APP_TOOL, {"name": "visual studio code"}, "C:\\work",
                               frozenset(decision.grant), apps=KNOWN)
    assert again.action == ALLOW and again.by_grant
    denied = permissions.decide(protocol.OPEN_APP_TOOL, {"name": "Photoshop"}, "C:\\work", apps=KNOWN)
    assert "find_app" in denied.reason                                   # 告诉模型先去找


def test_gate_checks_the_name_against_the_start_menu():
    from yunyun.assistant import claude_brain
    gate = claude_brain.PermissionGate("C:\\work", lambda event: None)
    assert gate.decide(protocol.OPEN_APP_TOOL, {"name": "计算器"}).kind == "open_app"
    assert gate.decide(protocol.OPEN_APP_TOOL, {"name": "不存在的软件"}).action == DENY


# ---------- 两个工具（子进程这边） ----------

def run_tools(name, args, answer=None):
    """调一个工具；芸芸那边由 answer(request) → OpenAppReply，或 None（停下了）。返回 (发出的请求, 工具结果)。"""
    async def main():
        emitted = []
        bridge = Bridge(emitted.append)
        tool = next(t for t in app_tools(bridge) if t.name == name)
        task = asyncio.create_task(tool.handler(args))
        for _ in range(50):   # 等工具把请求发出来（中间有 to_thread）
            if emitted or task.done():
                break
            await asyncio.sleep(0.01)
        if emitted:
            reply = answer(emitted[0]) if answer else None
            if reply is None:
                bridge.cancel_all()
            else:
                bridge.reply(reply)
        return emitted, await task
    return asyncio.run(main())


def text_of(result):
    return result["content"][0]["text"]


def test_find_app_lists_matching_names():
    _, result = run_tools("find_app", {"query": "visual studio"})
    assert text_of(result).splitlines()[1:] == ["Visual Studio Code", "Visual Studio Installer"]
    assert not result.get("is_error")
    _, result = run_tools("find_app", {"query": "photoshop"})
    assert "No app" in text_of(result) and "other words" in text_of(result)
    _, result = run_tools("find_app", {"query": " "})
    assert result["is_error"]


def test_find_app_says_when_the_start_menu_cannot_be_read(monkeypatch):
    monkeypatch.setattr(apps, "_query_start_apps", lambda: [])
    _, result = run_tools("find_app", {"query": "计算器"})
    assert result["is_error"] and "Start menu" in text_of(result)


def test_open_app_asks_yunyun_to_open_it():
    emitted, result = run_tools("open_app", {"name": "计算器"}, lambda r: protocol.OpenAppReply(r.request_id, True))
    assert emitted == [protocol.OpenAppRequest(1, "计算器", CALC)]
    assert "Opened" in text_of(result) and not result.get("is_error")
    assert apps.opened == []                                             # 子进程这边不自己打开


def test_open_app_failures_are_errors_for_the_model():
    emitted, result = run_tools("open_app", {"name": "没有这个"})
    assert emitted == [] and result["is_error"] and "find_app" in text_of(result)
    _, result = run_tools("open_app", {"name": "计算器"},
                          lambda r: protocol.OpenAppReply(r.request_id, False, "Windows could not open it."))
    assert result["is_error"] and "could not open" in text_of(result)
    _, result = run_tools("open_app", {"name": "计算器"})                 # 停下了
    assert result["is_error"] and "stopped" in text_of(result)


def test_tool_names_match_the_protocol():
    names = {f"mcp__yunyun__{t.name}" for t in app_tools(Bridge(print))}
    assert names == {protocol.FIND_APP_TOOL, protocol.OPEN_APP_TOOL}


# ---------- 会话、确认卡片 ----------

@pytest.fixture
def session(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    processes = []
    s = AssistantSession(make_process=lambda language, parent, resume="": processes.append(
        FakeProcess(language, parent, resume)) or processes[-1])
    s.processes = processes
    yield s
    s.close()


def test_session_passes_open_app_requests_up_and_replies_down(session):
    requests = []
    session.open_app_requested.connect(requests.append)
    session.ask("帮我打开计算器")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.ToolStarted("t1", protocol.OPEN_APP_TOOL, "计算器"))
    assert session.progress == "正在打开软件…… 计算器"
    process.say(protocol.OpenAppRequest(1, "计算器", CALC))
    assert requests == [protocol.OpenAppRequest(1, "计算器", CALC)]
    session.answer(protocol.OpenAppReply(1, True))
    assert process.sent == [protocol.OpenAppReply(1, True)]


def test_late_open_app_after_stop_is_refused(session):
    session.ask("打开计算器")
    process = session.processes[0]
    process.say(protocol.Ready("anthropic", "m"), protocol.TurnFinished(stopped=True))
    process.say(protocol.OpenAppRequest(3, "计算器", CALC))
    assert process.sent == [protocol.OpenAppReply(3, False, "The user stopped the task.")]
    assert apps.opened == []


def test_confirm_card_names_the_app(qapp, monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    request = protocol.PermissionRequest(1, protocol.OPEN_APP_TOOL, {"name": "计算器<b>"}, "open_app", True, "计算器")
    assert i18n.tr(assistant_confirm.title_key(request)) == "芸芸想打开一个软件"
    detail = assistant_confirm.detail_html(request, "")
    assert "计算器&lt;b&gt;" in detail and "不会操作软件里面" in detail       # 名字转义了
    assert assistant_confirm.remember_text(request) == "这次对话都允许打开它"
    assert assistant_confirm.note_key(request) is None


def test_action_log_names_the_tools(qapp, monkeypatch):
    from datetime import datetime
    from yunyun.assistant.session import Action
    from yunyun.pet.assistant_reader import actions_html
    monkeypatch.setattr(i18n, "_current", "zh")
    html = actions_html([Action(datetime.now(), protocol.FIND_APP_TOOL, "计算", "auto", True),
                         Action(datetime.now(), protocol.OPEN_APP_TOOL, "计算器", "allowed", True)])
    assert "找软件" in html and "打开软件" in html and "计算器" in html
