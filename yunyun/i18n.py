"""界面文字（气泡 + 右键菜单 + 托盘）的多语言（中 / 英 / 日）。

用法：
    from yunyun import i18n
    i18n.tr("exit_hint")   # 返回当前语言的文字

选择的语言保存在 QSettings 里（Windows 上存在注册表
HKEY_CURRENT_USER\\Software\\Yunyun\\Yunyun），下次启动自动恢复。
"""
from PySide6.QtCore import QSettings

LANGUAGES = {"zh": "中文", "en": "English", "ja": "日本語"}  # 代码 → 菜单里显示的名字
DEFAULT_LANGUAGE = "zh"
_SETTINGS_KEY = "bubble/language"

# 每条文字一个 key，下面是各语言的版本。以后加新文字就在这里加一项。
TEXTS = {
    "app_name": {"zh": "芸芸", "en": "Yunyun", "ja": "芸芸（ユンユン）"},

    # ---- 右键菜单 ----
    "menu_settings": {"zh": "设置", "en": "Settings", "ja": "設定"},
    "menu_language": {"zh": "语言", "en": "Language", "ja": "言語"},
    "menu_always_on_top": {"zh": "始终在上", "en": "Always on Top", "ja": "常に最前面"},
    "menu_pause":    {"zh": "暂停", "en": "Pause", "ja": "一時停止"},
    "menu_resume":   {"zh": "开始", "en": "Resume", "ja": "再開"},
    "menu_quit":     {"zh": "退出", "en": "Quit", "ja": "終了"},

    # ---- 气泡 ----
    "exit_hint": {
        "zh": "快速双击退出键我就退出啦！",
        "en": "Double-click Quit quickly and I'll go!",
        "ja": "「終了」をすばやくダブルクリックしたら終了するね！",
    },
    "language_changed": {
        "zh": "好的，之后我就用中文和你说话啦～",
        "en": "Okay! I'll talk to you in English from now on~",
        "ja": "わかった！これからは日本語で話すね～",
    },
    "test_mail": {
        "zh": "（测试）收到新邮件啦！昨天加油的发票到了！",
        "en": "(Test) New mail! Yesterday's fuel receipt has arrived!",
        "ja": "（テスト）新着メール！昨日の給油の領収書が届いたよ！",
    },
}

_current = DEFAULT_LANGUAGE  # 模块级变量：整个程序共享"当前语言"


def load_language():
    """启动时调用：从 QSettings 读取上次选择的语言。"""
    global _current  # 要在函数里修改模块级变量，必须先声明 global
    value = QSettings().value(_SETTINGS_KEY, DEFAULT_LANGUAGE)
    _current = value if value in LANGUAGES else DEFAULT_LANGUAGE


def current_language():
    return _current


def set_language(code):
    """切换语言，并保存下来。"""
    if code not in LANGUAGES:
        raise ValueError(f"不支持的语言：{code}")
    global _current
    _current = code
    QSettings().setValue(_SETTINGS_KEY, code)


def tr(key):
    """返回 key 对应的、当前语言的文字（tr 是 translate 的缩写）。"""
    return TEXTS[key][_current]
