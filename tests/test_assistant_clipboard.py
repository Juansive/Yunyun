"""剪贴板的工具（assistant/tools/clipboard.py，阶段 11.5）和确认卡片上的样子。剪贴板是 conftest 换的假剪贴板。"""
import asyncio

from yunyun import i18n
from yunyun.assistant import clipboard, protocol
from yunyun.assistant.tools.clipboard import MAX_READ_CHARS, clipboard_tools
from yunyun.pet.assistant_confirm import detail_html, remember_text, title_key


def call(name, args):
    tool = next(t for t in clipboard_tools() if t.name == name)
    result = asyncio.run(tool.handler(args))
    return result["content"][0]["text"], bool(result.get("is_error"))


def test_copy_then_read():
    assert call("copy_to_clipboard", {"text": "周五见～"}) == ("Copied 4 characters to the clipboard.", False)
    assert clipboard.get_text() == "周五见～"
    assert call("read_clipboard", {}) == ("周五见～", False)
    assert call("copy_to_clipboard", {"text": ""})[1]


def test_reading_an_empty_or_huge_clipboard():
    assert "has no text" in call("read_clipboard", {})[0]
    clipboard.set_text("长" * (MAX_READ_CHARS + 5))
    text, error = call("read_clipboard", {})
    assert not error and text.startswith("长" * 10) and text.endswith("(… 5 more characters cut)")


def test_busy_clipboard_is_reported(monkeypatch):
    def busy(*args):
        raise OSError("另一个程序正开着剪贴板")
    monkeypatch.setattr(clipboard, "set_text", busy)
    monkeypatch.setattr(clipboard, "get_text", busy)
    assert call("copy_to_clipboard", {"text": "x"})[1] and call("read_clipboard", {})[1]


def test_confirm_card_for_reading_the_clipboard(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "zh")
    request = protocol.PermissionRequest(1, protocol.READ_CLIPBOARD_TOOL, {}, "clipboard", True)
    assert i18n.tr(title_key(request)) == "芸芸想看看你的剪贴板"
    assert "密码" in detail_html(request, "") and remember_text(request) == "这次对话都允许看剪贴板"
