"""多语言文字的检查：以后新增文字时漏翻了，这里会立刻报错。"""
import re
import string

from yunyun import i18n


def test_every_text_has_all_languages():
    missing = [(key, lang) for key, texts in i18n.TEXTS.items() for lang in i18n.LANGUAGES if not texts.get(lang)]
    assert missing == []


def test_placeholders_match_across_languages():
    """同一条文字，各语言的 {} 占位符个数要一样，不然 tr(key, ...) 填参数时会出错。"""
    for key, texts in i18n.TEXTS.items():
        counts = {lang: sum(1 for _, field, _, _ in string.Formatter().parse(t) if field is not None)
                  for lang, t in texts.items()}
        assert len(set(counts.values())) == 1, (key, counts)


def test_every_assistant_error_kind_has_a_message():
    """助手报的每一种错（protocol.ERROR_KINDS）界面上都要有对应的说明。"""
    from yunyun.assistant.protocol import ERROR_KINDS
    assert [kind for kind in ERROR_KINDS if f"assistant_error_{kind}" not in i18n.TEXTS] == []


def test_qt_dialog_buttons_follow_language(qapp):
    """确认框的 Yes / No 跟着芸芸的语言（Qt 自己的翻译文件）；切回英文就卸掉。"""
    from PySide6.QtWidgets import QMessageBox

    def buttons():
        box = QMessageBox(QMessageBox.Icon.Question, "", "", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        return [b.text() for b in box.buttons()]

    try:
        i18n.set_language("zh")
        assert buttons() == ["是(&Y)", "否(&N)"]
        i18n.set_language("ja")
        assert buttons() == ["はい(&Y)", "いいえ(&N)"]
        i18n.set_language("en")
        assert buttons() == ["&Yes", "&No"]
    finally:
        i18n.set_language(i18n.DEFAULT_LANGUAGE)


def test_no_ampersand_in_menu_and_button_texts():
    """菜单 / 按钮文字里的 & 会被 Qt 当成快捷键标记吃掉。"""
    bad = [key for key, texts in i18n.TEXTS.items()
           if re.match(r"(menu_|mail_test|mail_remove|mail_close)", key) and any("&" in t for t in texts.values())]
    assert bad == []
