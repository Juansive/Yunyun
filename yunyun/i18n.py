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
    "menu_mail_accounts": {"zh": "邮箱账号…", "en": "Mail Accounts…", "ja": "メールアカウント…"},
    "menu_dismiss_mail": {"zh": "全部标为已看", "en": "Mark All as Seen", "ja": "すべて確認済みにする"},
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
    "mail_first_run": {
        "zh": "你好呀，我是芸芸！先告诉我你的邮箱吧，有新邮件我第一时间告诉你～",
        "en": "Hi, I'm Yunyun! Tell me your email and I'll let you know when new mail arrives~",
        "ja": "こんにちは、芸芸だよ！メールアドレスを教えてくれたら、新着メールをすぐ知らせるね～",
    },

    # ---- 邮箱账号窗口 ----
    "mail_dialog_title": {"zh": "芸芸 · 邮箱账号", "en": "Yunyun · Mail Accounts", "ja": "芸芸 · メールアカウント"},
    "mail_accounts_label": {"zh": "已添加的邮箱", "en": "Added accounts", "ja": "追加済みのアカウント"},
    "mail_no_accounts": {"zh": "（还没有添加邮箱）", "en": "(No accounts yet)", "ja": "（まだありません）"},
    "mail_remove": {"zh": "删除所选", "en": "Remove Selected", "ja": "選択を削除"},
    "mail_confirm_remove": {
        "zh": "确定删除 {} 吗？保存的授权码也会一起删除。",
        "en": "Remove {}? Its saved code will be deleted too.",
        "ja": "{} を削除しますか？保存された認証コードも削除されます。",
    },
    "mail_add_title": {"zh": "添加邮箱", "en": "Add an Account", "ja": "アカウントを追加"},
    "mail_provider": {"zh": "邮箱类型", "en": "Provider", "ja": "種類"},
    "mail_address": {"zh": "邮箱地址", "en": "Email", "ja": "メールアドレス"},
    "mail_password": {"zh": "授权码", "en": "Auth code", "ja": "認証コード"},
    "provider_qq": {"zh": "QQ邮箱", "en": "QQ Mail", "ja": "QQメール"},
    "provider_163": {"zh": "163邮箱", "en": "163 Mail", "ja": "163メール"},
    "provider_gmail": {"zh": "Gmail", "en": "Gmail", "ja": "Gmail"},
    "mail_hint_qq": {
        "zh": "授权码怎么拿：QQ 邮箱网页版 → 设置 → 账号与安全 → 开启「IMAP/SMTP 服务」→ 生成授权码（16 位字母）。",
        "en": "How to get the code: QQ Mail (web) → Settings → Account & Security → enable “IMAP/SMTP service” → generate an authorization code (16 letters).",
        "ja": "認証コードの取得：QQメール（Web版）→ 設定 → アカウントとセキュリティ →「IMAP/SMTPサービス」を有効化 → 認証コード（16文字）を生成。",
    },
    "mail_hint_163": {
        "zh": "授权码怎么拿：163 邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启「IMAP/SMTP 服务」→ 新增授权密码。",
        "en": "How to get the code: 163 Mail (web) → Settings → POP3/SMTP/IMAP → enable “IMAP/SMTP service” → add an authorization password.",
        "ja": "認証コードの取得：163メール（Web版）→ 設定 → POP3/SMTP/IMAP →「IMAP/SMTPサービス」を有効化 → 認証パスワードを追加。",
    },
    "mail_hint_gmail": {
        "zh": "Gmail 要先开启两步验证，再到 Google 账号 → 安全性 → 应用专用密码，生成一个 16 位密码填在这里。",
        "en": "Turn on 2-Step Verification first, then create a 16-character app password in Google Account → Security → App passwords, and enter it here.",
        "ja": "まず2段階認証を有効にし、Googleアカウント → セキュリティ → アプリパスワードで16文字のパスワードを作成して入力します。",
    },
    "mail_privacy_note": {
        "zh": "授权码只保存在这台电脑的 Windows 凭据管理器里，不会写进任何文件，以后也不用再输入。",
        "en": "The code is kept only in Windows Credential Manager on this PC—never in a file—and you won't need to enter it again.",
        "ja": "認証コードはこのPCのWindows資格情報マネージャーにのみ保存され、ファイルには書き込まれません。再入力も不要です。",
    },
    # 注意：按钮 / 菜单文字里的 & 会被 Qt 当成快捷键标记吃掉，所以英文不用 "Test & Save"
    "mail_test_and_save": {"zh": "测试并保存", "en": "Test and Save", "ja": "テストして保存"},
    "mail_close": {"zh": "关闭", "en": "Close", "ja": "閉じる"},
    "mail_testing": {"zh": "正在连接邮箱，请稍候……", "en": "Connecting to your mailbox…", "ja": "メールボックスに接続中…"},
    "mail_saved": {
        "zh": "添加成功！以后芸芸会帮你留意这个邮箱。",
        "en": "Added! I'll keep an eye on this mailbox.",
        "ja": "追加しました！このメールボックスを見守るね。",
    },
    "mail_bad_address": {"zh": "邮箱地址格式不对。", "en": "That email address doesn't look right.", "ja": "メールアドレスの形式が正しくありません。"},
    "mail_empty_password": {"zh": "请填写授权码。", "en": "Please enter the code.", "ja": "認証コードを入力してください。"},
    "mail_error_connect": {
        "zh": "连接不上邮箱服务器，请检查网络。",
        "en": "Couldn't reach the mail server. Please check your network.",
        "ja": "メールサーバーに接続できません。ネットワークを確認してください。",
    },
    "mail_error_login": {
        "zh": "登录失败：请检查邮箱地址和授权码，并确认已开启 IMAP 服务。",
        "en": "Login failed: check the address and code, and make sure IMAP is enabled.",
        "ja": "ログインに失敗しました：アドレスと認証コード、IMAPが有効かを確認してください。",
    },
    "mail_error_inbox": {
        "zh": "登录成功了，但打不开收件箱。",
        "en": "Logged in, but couldn't open the inbox.",
        "ja": "ログインできましたが、受信トレイを開けません。",
    },
    "mail_error_no_password": {"zh": "出错了。", "en": "Something went wrong.", "ja": "エラーが発生しました。"},
    "mail_error_other": {"zh": "出错了。", "en": "Something went wrong.", "ja": "エラーが発生しました。"},
    "mail_error_detail": {"zh": "（服务器返回：{}）", "en": "(Server said: {})", "ja": "（サーバーの応答：{}）"},

    # ---- 小红点 / 新邮件 ----
    "badge_hidden_hint": {
        "zh": "好哦，小红点先藏起来～右键我就能把它找回来哦",
        "en": "Okay, I've hidden the badge~ Right-click me to bring it back!",
        "ja": "了解、バッジを隠したよ～右クリックすれば戻せるよ！",
    },
    "mail_dismissed": {
        "zh": "好～这 {} 封我就先不管啦",
        "en": "Got it~ I'll skip those {} for now.",
        "ja": "了解～その {} 通は今回スルーするね",
    },
    "mail_auth_expired": {
        "zh": "邮箱 {} 的授权好像失效了，右键我 → 设置 → 邮箱账号，重新添加一下吧～",
        "en": "The authorization for {} seems to have expired. Right-click me → Settings → Mail Accounts to add it again~",
        "ja": "{} の認証が切れたみたい。右クリック → 設定 → メールアカウントからもう一度追加してね～",
    },
    # M3 接入真正的 AI 之前，用标题拼出来的"假"提醒
    "fake_summary": {
        "zh": "嘿！「{}」来啦！",
        "en": "Hey! “{}” just arrived!",
        "ja": "ねえ！「{}」が届いたよ！",
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


def tr(key, *args):
    """返回 key 对应的、当前语言的文字（tr 是 translate 的缩写）。

    文字里有 {} 占位符时，按顺序填入 args，例如 tr("mail_confirm_remove", "a@qq.com")。
    """
    text = TEXTS[key][_current]
    return text.format(*args) if args else text
