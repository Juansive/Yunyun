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
    "menu_settings": {"zh": "设置…", "en": "Settings…", "ja": "設定…"},
    "menu_assistant": {"zh": "✦ 助手模式", "en": "✦ Assistant mode", "ja": "✦ アシスタントモード"},
    "menu_assistant_stop": {"zh": "■ 停止助手", "en": "■ Stop the assistant", "ja": "■ アシスタントを停止"},
    "menu_assistant_exit": {"zh": "退出助手模式", "en": "Exit assistant mode", "ja": "アシスタントモードを終了"},
    "menu_language": {"zh": "语言", "en": "Language", "ja": "言語"},
    "menu_always_on_top": {"zh": "始终在上", "en": "Always on Top", "ja": "常に最前面"},
    "menu_recent_mail": {"zh": "刚才的邮件", "en": "Recent Mail", "ja": "さっきのメール"},
    "menu_dismiss_mail": {"zh": "全部标为已看", "en": "Mark All as Seen", "ja": "すべて確認済みにする"},
    "setting_shortcut": {"zh": "桌面快捷方式", "en": "Desktop shortcut", "ja": "デスクトップのショートカット"},
    "button_create": {"zh": "创建", "en": "Create", "ja": "作成"},
    "menu_open_logs": {"zh": "打开日志文件夹", "en": "Open Log Folder", "ja": "ログフォルダーを開く"},
    "menu_autostart": {"zh": "开机时启动", "en": "Start with Windows", "ja": "Windows 起動時に開始"},
    "autostart_on": {
        "zh": "好哒，以后开机我就自己出来陪你～",
        "en": "Got it! I'll show up by myself when you turn on your PC~",
        "ja": "了解！これからはパソコンを起動したら自分で出てくるね～",
    },
    "autostart_off": {
        "zh": "好的，开机时我就不自己出来啦",
        "en": "Okay, I won't start automatically anymore.",
        "ja": "わかった、起動時に自動では出てこないね",
    },
    "autostart_failed": {
        "zh": "开机启动没设置成功……（{}）",
        "en": "Couldn't change the startup setting… ({})",
        "ja": "自動起動の設定に失敗しちゃった…（{}）",
    },
    "shortcut_description": {
        "zh": "芸芸：你的桌面邮件小助手",
        "en": "Yunyun: your desktop mail buddy",
        "ja": "芸芸：デスクトップのメール相棒",
    },
    "shortcut_created": {
        "zh": "桌面上多了一个芸芸的图标，以后双击它就能叫我出来啦～",
        "en": "There's a Yunyun icon on your desktop now. Double-click it to call me anytime~",
        "ja": "デスクトップに芸芸のアイコンができたよ。ダブルクリックでいつでも呼んでね～",
    },
    "shortcut_failed": {
        "zh": "快捷方式没建成……（{}）",
        "en": "Couldn't create the shortcut… ({})",
        "ja": "ショートカットを作れなかった…（{}）",
    },
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

    # ---- 设置窗口 ----
    "settings_title": {"zh": "芸芸 · 设置", "en": "Yunyun · Settings", "ja": "芸芸 · 設定"},
    "page_general": {"zh": "通用", "en": "General", "ja": "一般"},
    "page_mail": {"zh": "邮箱", "en": "Mail", "ja": "メール"},
    "page_ai": {"zh": "AI", "en": "AI", "ja": "AI"},
    "page_assistant": {"zh": "助手", "en": "Assistant", "ja": "アシスタント"},
    "page_history": {"zh": "历史", "en": "History", "ja": "履歴"},
    "quiet_label": {"zh": "免打扰", "en": "Do not disturb", "ja": "おやすみモード"},
    "quiet_to": {"zh": "至", "en": "to", "ja": "〜"},
    "quiet_hint": {
        "zh": "免打扰期间芸芸会睡觉；有新邮件时不激动、不跳出来，只在身上安静地计数。你打字时它会醒来陪你。",
        "en": "During this time Yunyun sleeps. New mail is only counted quietly on its badge—no excitement, no pop-ups. It still wakes up to keep you company when you type.",
        "ja": "この時間帯は芸芸が眠ります。新着メールは静かにカウントするだけで、はしゃいだり飛び出したりしません。入力するときは起きて付き合います。",
    },
    "quiet_start": {
        "zh": "晚安～免打扰时间到啦，有邮件我会安静地帮你记着",
        "en": "Good night~ Quiet time has started. I'll quietly keep count of any new mail.",
        "ja": "おやすみ～おやすみモードの時間だよ。新着メールは静かに数えておくね",
    },
    "quiet_end": {
        "zh": "早上好！免打扰结束啦～",
        "en": "Good morning! Quiet time is over~",
        "ja": "おはよう！おやすみモードは終わりだよ～",
    },
    "page_about": {"zh": "关于", "en": "About", "ja": "情報"},
    "history_note": {
        "zh": "这里保存最近 7 天芸芸展示过的提醒（不含邮件正文），超过 7 天会自动删除。",
        "en": "Reminders Yunyun showed you in the last 7 days (without email bodies). Older ones are deleted automatically.",
        "ja": "芸芸が表示した直近 7 日間の通知です（メール本文は含みません）。7 日を過ぎると自動で削除されます。",
    },
    "history_empty": {"zh": "（还没有历史记录）", "en": "(No history yet)", "ja": "（まだ履歴はありません）"},
    "history_clear": {"zh": "清空历史", "en": "Clear History", "ja": "履歴を消去"},
    "history_confirm_clear": {
        "zh": "确定清空所有历史记录吗？",
        "en": "Clear all history?",
        "ja": "すべての履歴を消去しますか？",
    },
    "date_today": {"zh": "今天", "en": "Today", "ja": "今日"},
    "date_yesterday": {"zh": "昨天", "en": "Yesterday", "ja": "昨日"},
    "date_month_day": {"zh": "{}月{}日", "en": "{}/{}", "ja": "{}月{}日"},
    "about_version": {"zh": "版本 {}", "en": "Version {}", "ja": "バージョン {}"},
    "about_privacy_title": {"zh": "隐私", "en": "Privacy", "ja": "プライバシー"},
    "about_privacy_text": {
        "zh": "· 芸芸不会改动你的邮箱：看过的邮件在邮箱里仍然是未读。\n"
              "· 只有你点了小红点，才会把邮件的发件人、标题、附件名和正文开头（最多 1500 字）发给你选择的 AI 服务商；拖走小红点清掉的邮件不会发送。\n"
              "· 芸芸只检测你有没有在打字，从不记录按了哪个键。\n"
              "· 日志里不写邮件内容、授权码和 API Key，邮箱地址会打码。",
        "en": "· Yunyun never changes your mailbox: emails it reads stay unread.\n"
              "· Only after you click the badge are the sender, subject, attachment names and the beginning of the body (up to 1,500 characters) sent to the AI provider you chose. Emails you dismiss are never sent.\n"
              "· Yunyun only notices whether you're typing — it never records which keys you press.\n"
              "· Logs never contain email content, authorization codes or API keys, and email addresses are masked.",
        "ja": "· 芸芸はメールボックスを変更しません。読んだメールも未読のままです。\n"
              "· バッジをクリックしたときだけ、差出人・件名・添付ファイル名・本文の冒頭（最大 1,500 文字）が選択した AI プロバイダーに送られます。消したメールは送られません。\n"
              "· 芸芸は入力しているかどうかだけを検知し、どのキーを押したかは記録しません。\n"
              "· ログにはメール本文・認証コード・API キーは書かれず、メールアドレスは伏せ字になります。",
    },
    "about_data_title": {"zh": "数据存放在哪里", "en": "Where your data lives", "ja": "データの保存場所"},
    "about_data_secrets": {"zh": "授权码、API Key", "en": "Auth codes, API keys", "ja": "認証コード・API キー"},
    "about_data_secrets_where": {
        "zh": "Windows 凭据管理器（yunyun-mail、yunyun-ai）",
        "en": "Windows Credential Manager (yunyun-mail, yunyun-ai)",
        "ja": "Windows 資格情報マネージャー（yunyun-mail、yunyun-ai）",
    },
    "about_data_config": {"zh": "邮箱地址、AI 服务商", "en": "Email addresses, AI provider", "ja": "メールアドレス・AI プロバイダー"},
    "about_data_state": {"zh": "提醒记录、日志", "en": "Reminder records, logs", "ja": "通知の記録・ログ"},
    "about_open_data": {"zh": "打开数据文件夹", "en": "Open Data Folder", "ja": "データフォルダーを開く"},

    # ---- 设置窗口：邮箱页 ----
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
    "onboarding_ai": {
        "zh": "邮箱设置好啦！再给我一个 AI 的 Key，我就能用一句话告诉你每封邮件是什么～不想设置的话直接关掉就好",
        "en": "Your mailbox is all set! Give me an AI key and I'll sum up each email in one line~ Just close the window if you'd rather not.",
        "ja": "メールの設定完了！AI のキーをくれたら、メールを一言で教えられるよ～設定しないなら閉じるだけで大丈夫",
    },
    "already_running": {
        "zh": "我在这儿呢～",
        "en": "I'm right here~",
        "ja": "ここにいるよ～",
    },
    "no_new_mail": {
        "zh": "现在没有新邮件哦～",
        "en": "No new mail right now~",
        "ja": "今は新しいメールはないよ～",
    },
    "mail_auth_expired": {
        "zh": "邮箱 {} 的授权好像失效了，右键我 → 设置… → 邮箱，重新添加一下吧～",
        "en": "The authorization for {} seems to have expired. Right-click me → Settings… → Mail to add it again~",
        "ja": "{} の認証が切れたみたい。右クリック → 設定… → メールからもう一度追加してね～",
    },
    # AI 用不了时的兜底提醒：发件人 + 标题
    "ai_fallback": {
        "zh": "{} 发来了「{}」",
        "en": "{} sent you “{}”",
        "ja": "{} から「{}」が届いたよ",
    },
    "ai_not_configured": {
        "zh": "还没设置 AI，我先用标题告诉你～右键我 → 设置… → AI，我就能更聪明啦",
        "en": "AI isn't set up yet, so I'll just use the subject~ Right-click me → Settings… → AI to make me smarter!",
        "ja": "AI がまだ設定されていないので、件名で伝えるね～右クリック → 設定… → AI で賢くなれるよ",
    },
    "ai_failed": {
        "zh": "AI 暂时没回应，我先用标题告诉你～",
        "en": "The AI isn't responding right now, so I'll use the subject~",
        "ja": "AI が応答しないので、件名で伝えるね～",
    },
    "ai_local_not_running": {
        "zh": "本地模型没能启动（Ollama 装好了吗？），我先用标题告诉你～",
        "en": "The local model couldn't start (is Ollama installed?), so I'll use the subject~",
        "ja": "ローカルモデルを起動できなかったので（Ollama はインストールされてる？）、件名で伝えるね～",
    },
    "ai_local_model_missing": {
        "zh": "本地模型还没下载（右键我 → 设置… → AI），我先用标题告诉你～",
        "en": "The local model isn't downloaded yet (right-click me → Settings… → AI), so I'll use the subject~",
        "ja": "ローカルモデルがまだダウンロードされていないので（右クリック → 設定… → AI）、件名で伝えるね～",
    },

    # ---- 卡片堆（放大后显示的原始信息）----
    "card_subject": {"zh": "标题", "en": "Subject", "ja": "件名"},
    "card_sender": {"zh": "发件人", "en": "From", "ja": "差出人"},
    "card_time": {"zh": "时间", "en": "Time", "ja": "日時"},
    "card_attachments": {"zh": "附件", "en": "Attachments", "ja": "添付"},
    "card_no_subject": {"zh": "（无标题）", "en": "(No subject)", "ja": "（件名なし）"},
    "card_detail_line": {"zh": "{}：{}", "en": "{}: {}", "ja": "{}：{}"},   # "标题：xxx"
    "list_separator": {"zh": "、", "en": ", ", "ja": "、"},                  # 多个附件之间

    # ---- 设置窗口：AI 页 ----
    "ai_provider": {"zh": "服务商", "en": "Provider", "ja": "プロバイダー"},
    "ai_model": {"zh": "模型", "en": "Model", "ja": "モデル"},
    "ai_key": {"zh": "API Key", "en": "API Key", "ja": "API キー"},
    "ai_base_url": {"zh": "接口地址", "en": "API URL", "ja": "API の URL"},
    "ai_key_optional": {"zh": "没有就留空", "en": "Leave empty if not needed", "ja": "不要なら空欄"},
    "provider_custom": {
        "zh": "自定义（自己的模型 / 接口）",
        "en": "Custom (your own model / API)",
        "ja": "カスタム（自分のモデル / API）",
    },
    "ai_hint_custom": {
        "zh": "填写任何兼容 OpenAI 接口的地址（一般以 /v1 结尾），在线的、自己电脑上的都可以，例如："
              "DeepSeek https://api.deepseek.com/v1 · LM Studio http://localhost:1234/v1。"
              "模型名按服务商的说明填；自己电脑上的模型一般不需要 Key。用 Ollama 的话，直接选上面的「本地模型（Ollama）」更快。",
        "en": "Enter any OpenAI-compatible API URL (usually ending in /v1), online or on your own PC, e.g. "
              "DeepSeek https://api.deepseek.com/v1 · LM Studio http://localhost:1234/v1. Use the model name from your "
              "provider; local models usually need no key. For Ollama, the \"Local model (Ollama)\" option above is faster.",
        "ja": "OpenAI 互換の API の URL（ふつうは /v1 で終わる）を入力。オンラインでも自分の PC 上でも使えます。例："
              "DeepSeek https://api.deepseek.com/v1 · LM Studio http://localhost:1234/v1。モデル名は提供元の説明どおりに。"
              "ローカルのモデルはふつうキー不要です。Ollama なら上の「ローカルモデル（Ollama）」のほうが速いです。",
    },
    "provider_zhipu": {"zh": "智谱 GLM（推荐，免费）", "en": "Zhipu GLM (recommended, free)", "ja": "Zhipu GLM（おすすめ・無料）"},
    "provider_siliconflow": {"zh": "硅基流动", "en": "SiliconFlow", "ja": "SiliconFlow"},
    "provider_dashscope": {"zh": "通义千问（阿里云百炼）", "en": "Qwen (Alibaba Cloud)", "ja": "Qwen（Alibaba Cloud）"},
    "provider_ollama": {
        "zh": "本地模型（Ollama，邮件不出电脑）",
        "en": "Local model (Ollama, mail stays on this PC)",
        "ja": "ローカルモデル（Ollama・メールは PC の外に出ません）",
    },
    "ai_hint_zhipu": {
        "zh": "Key 怎么拿：打开 {} 注册并登录 → 右上角头像 → API Keys → 添加新的 API Key，复制到这里。",
        "en": "How to get a key: open {} and sign in → avatar (top right) → API Keys → add a new API key, then paste it here.",
        "ja": "キーの取得：{} で登録・ログイン → 右上のアバター → API Keys → 新しい API キーを追加して、ここに貼り付け。",
    },
    "ai_hint_siliconflow": {
        "zh": "Key 怎么拿：打开 {} 注册并登录 → 左侧「API 密钥」→ 新建 API 密钥。默认的模型是免费的。",
        "en": "How to get a key: open {} and sign in → “API Keys” on the left → create a new key. The default model is free.",
        "ja": "キーの取得：{} で登録・ログイン → 左側の「API キー」→ 新規作成。デフォルトのモデルは無料です。",
    },
    "ai_hint_dashscope": {
        "zh": "Key 怎么拿：打开 {} 登录阿里云 → API-KEY → 创建。新用户有免费额度，用完后会按量收费。",
        "en": "How to get a key: open {} and sign in → API-KEY → create. New users get free quota; usage is billed after that.",
        "ja": "キーの取得：{} にログイン → API-KEY → 作成。新規ユーザーは無料枠あり、超過分は従量課金です。",
    },
    "ai_hint_ollama": {
        "zh": "用这台电脑上的免费开源模型，不用注册、不用 Key。先下载安装 Ollama：{}。芸芸会在处理邮件时自动启动它、用完就关，平时不占内存。"
              "模型第一次使用前要先下载（默认的 qwen3:1.7b 约 1.4 GB）。处理邮件时会临时多占约 1～2 GB 内存；有独立显卡会快很多。",
        "en": "Uses a free open model on this PC — no sign-up, no key. First install Ollama: {}. I start it only while reading mail "
              "and close it afterwards, so it uses no memory the rest of the time. The model must be downloaded before first use "
              "(the default qwen3:1.7b is about 1.4 GB). While reading mail it temporarily uses 1–2 GB of memory; a dedicated "
              "graphics card makes it much faster.",
        "ja": "この PC 上の無料オープンモデルを使います。登録もキーも不要です。まず Ollama をインストール：{}。メールを処理するときだけ私が起動し、"
              "終わったら閉じるので、普段はメモリを使いません。初回はモデルのダウンロードが必要です（既定の qwen3:1.7b は約 1.4 GB）。"
              "処理中は一時的にメモリを 1～2 GB 使います。専用 GPU があるとずっと速くなります。",
    },
    "ai_privacy_note_custom": {
        "zh": "使用自定义接口时，邮件的发件人、标题和正文开头（最多 1500 字）会发送到你填写的地址。请确认你信任这个服务。",
        "en": "With a custom API, each email's sender, subject and the beginning of its body (up to 1,500 characters) "
              "are sent to the URL you entered. Make sure you trust that service.",
        "ja": "カスタム API では、メールの差出人・件名・本文の冒頭（最大 1,500 文字）が入力した URL に送信されます。"
              "信頼できるサービスか確認してください。",
    },
    "ai_privacy_note_custom_local": {
        "zh": "你填的是本机或局域网里的地址：邮件只交给你自己网络里的这个模型，不会发到网上。",
        "en": "This URL is on your PC or local network: your mail only goes to that model and never over the internet.",
        "ja": "この PC かローカルネットワーク内の URL です。メールはそのモデルにだけ渡され、インターネットには送信されません。",
    },
    "ai_privacy_note_local": {
        "zh": "使用本地模型时，邮件只在这台电脑上处理，不会发送到网上。",
        "en": "With a local model, your mail is processed only on this PC and never sent over the internet.",
        "ja": "ローカルモデルでは、メールはこの PC の中だけで処理され、インターネットには送信されません。",
    },
    "ai_privacy_note": {
        "zh": "使用 AI 时，邮件的发件人、标题和正文开头（最多 1500 字）会发送给所选的服务商，用来生成提醒。API Key 只保存在这台电脑的 Windows 凭据管理器里。",
        "en": "When AI is used, each email's sender, subject and the beginning of its body (up to 1,500 characters) are sent to the selected provider to write the reminder. The API key is stored only in Windows Credential Manager on this PC.",
        "ja": "AI を使うと、メールの差出人・件名・本文の冒頭（最大 1,500 文字）が通知文の作成のため選択したプロバイダーに送信されます。API キーはこの PC の Windows 資格情報マネージャーにのみ保存されます。",
    },
    "ai_testing": {"zh": "正在测试连接……", "en": "Testing the connection…", "ja": "接続をテスト中…"},
    "ai_saved": {
        "zh": "设置好啦！之后芸芸会用 AI 帮你读邮件～",
        "en": "All set! I'll use AI to read your mail from now on~",
        "ja": "設定完了！これからは AI でメールを読むね～",
    },
    "ai_empty_key": {"zh": "请填写 API Key。", "en": "Please enter the API key.", "ja": "API キーを入力してください。"},
    "ai_empty_model": {"zh": "请填写模型名。", "en": "Please enter the model name.", "ja": "モデル名を入力してください。"},
    "ai_empty_base_url": {"zh": "请填写接口地址。", "en": "Please enter the API URL.", "ja": "API の URL を入力してください。"},
    "ai_bad_base_url": {
        "zh": "接口地址要以 http:// 或 https:// 开头。",
        "en": "The API URL must start with http:// or https://.",
        "ja": "API の URL は http:// か https:// で始めてください。",
    },
    "ai_error_not_found": {
        "zh": "找不到这个接口或模型：请检查接口地址（一般以 /v1 结尾）和模型名。",
        "en": "The API or model wasn't found. Check the API URL (usually ending in /v1) and the model name.",
        "ja": "API かモデルが見つかりません。URL（ふつうは /v1 で終わる）とモデル名を確認してください。",
    },
    "ai_error_custom_network": {
        "zh": "连不上你填写的地址：请检查地址是否正确；如果是自己电脑上的模型，确认它已经打开。",
        "en": "Couldn't reach that URL. Check that it's correct; if it's a model on your PC, make sure it's running.",
        "ja": "その URL に接続できません。URL が正しいか、自分の PC のモデルなら起動しているか確認してください。",
    },
    "ai_error_auth": {
        "zh": "API Key 不对，或者没有使用这个模型的权限。",
        "en": "The API key is wrong, or it can't use this model.",
        "ja": "API キーが正しくないか、このモデルを使う権限がありません。",
    },
    "ai_error_rate_limit": {
        "zh": "调用太频繁，或者免费额度用完了，稍后再试试。",
        "en": "Too many requests, or the free quota is used up. Please try again later.",
        "ja": "リクエストが多すぎるか、無料枠を使い切りました。しばらくしてから試してください。",
    },
    "ai_error_network": {
        "zh": "连不上服务商，请检查网络。",
        "en": "Couldn't reach the provider. Please check your network.",
        "ja": "プロバイダーに接続できません。ネットワークを確認してください。",
    },
    "ai_error_bad_response": {
        "zh": "服务商返回的内容看不懂，可以换个模型试试。",
        "en": "The provider's reply couldn't be understood. Try another model.",
        "ja": "プロバイダーの応答を解釈できません。別のモデルを試してください。",
    },
    "ai_error_other": {"zh": "出错了。", "en": "Something went wrong.", "ja": "エラーが発生しました。"},
    "ai_error_not_running": {
        "zh": "没找到 Ollama，或者它没能启动：请先安装 Ollama（装好就行，芸芸会在需要时自动启动它），再试一次。",
        "en": "Couldn't find or start Ollama. Please install Ollama (that's all — I'll start it when needed), then try again.",
        "ja": "Ollama が見つからないか、起動できませんでした。Ollama をインストールしてから（起動は私が必要なときにします）、もう一度試してください。",
    },
    "ai_error_model_missing": {
        "zh": "Ollama 里还没有模型 {}，点「下载模型」我来帮你下载～",
        "en": "Ollama doesn't have the model {} yet. Click “Download model” and I'll fetch it for you~",
        "ja": "Ollama にモデル {} がまだありません。「モデルをダウンロード」を押せば私がダウンロードするよ～",
    },
    "ai_download_model": {"zh": "下载模型", "en": "Download model", "ja": "モデルをダウンロード"},
    "ai_download_starting": {
        "zh": "正在让 Ollama 下载 {}……",
        "en": "Asking Ollama to download {}…",
        "ja": "Ollama で {} をダウンロードしています…",
    },
    "ai_downloading": {
        "zh": "正在下载 {}：{}%（{}）。关掉这个窗口也会在后台接着下载，下载完回来点「测试并保存」就行。",
        "en": "Downloading {}: {}% ({}). Closing this window won't stop it; when it's done, come back and click “Test and Save”.",
        "ja": "{} をダウンロード中：{}%（{}）。ウィンドウを閉じても裏で続きます。終わったら戻って「テストして保存」を押してね。",
    },
    "ai_downloaded": {
        "zh": "模型下载好了，正在测试……",
        "en": "Model downloaded. Testing…",
        "ja": "モデルのダウンロード完了。テスト中…",
    },
    "ai_download_failed": {
        "zh": "模型没下载成功，可以检查网络或模型名后再试一次。",
        "en": "The model couldn't be downloaded. Check your network or the model name and try again.",
        "ja": "モデルをダウンロードできませんでした。ネットワークかモデル名を確認して、もう一度試してください。",
    },

    # ---- 设置窗口：助手页（助手模式用的"大脑"，和上面邮件用的 AI 是两套设置）----
    "assistant_provider": {"zh": "服务商", "en": "Provider", "ja": "プロバイダー"},
    "assistant_provider_anthropic": {"zh": "Claude（Anthropic 官方）", "en": "Claude (Anthropic)", "ja": "Claude（Anthropic 公式）"},
    "assistant_model": {"zh": "模型", "en": "Model", "ja": "モデル"},
    "assistant_model_price": {   # 模型名 · 价格（每百万 token 多少美元）
        "zh": "{} · 每百万 token：输入 ${} / 输出 ${}",
        "en": "{} · per million tokens: ${} in / ${} out",
        "ja": "{} · 100 万トークンあたり：入力 ${} / 出力 ${}",
    },
    "assistant_model_custom": {"zh": "自己填写模型名…", "en": "Enter a model name…", "ja": "モデル名を入力…"},
    "assistant_model_placeholder": {"zh": "比如 claude-haiku-4-5", "en": "e.g. claude-haiku-4-5", "ja": "例：claude-haiku-4-5"},
    "assistant_key": {"zh": "API Key", "en": "API Key", "ja": "API キー"},
    "assistant_key_saved": {
        "zh": "已保存（要换的话，在这里粘贴新的）",
        "en": "Saved (paste a new one here to replace it)",
        "ja": "保存済み（変更するときはここに新しいキーを貼り付け）",
    },
    "assistant_key_placeholder": {"zh": "粘贴你的 API Key", "en": "Paste your API key", "ja": "API キーを貼り付け"},
    "assistant_budget": {"zh": "每次回答最多花", "en": "Max per reply", "ja": "1 回の回答の上限"},
    "assistant_budget_hint": {
        "zh": "每次回答：芸芸回答你一次（包括期间查网页、读网页）最多花这么多钱，快超过就停下来告诉你。"
              "用 Haiku 4.5 时一般一次 1 美分左右（每段对话的第一问稍贵一点）。\n"
              "每段对话：一段对话加起来最多花这么多钱，到了芸芸会提醒你开个新对话。",
        "en": "Per reply: the most Yunyun may spend answering you once (including web searches and page reads); "
              "she stops and tells you before going over. With Haiku 4.5 a reply costs about 1 cent "
              "(the first one in a chat a bit more).\n"
              "Per chat: the total for one chat; when it's reached, Yunyun suggests starting a new chat.",
        "ja": "1 回の回答：芸芸が 1 回答えるごと（途中のウェブ検索・閲覧を含む）の上限です。超えそうなら止めてお知らせします。"
              "Haiku 4.5 ならたいてい 1 回 1 セントほど（会話の最初の質問は少し高め）。\n"
              "1 つの会話：会話全体の合計の上限です。達したら新しい会話を始めるようお知らせします。",
    },
    "assistant_spent": {   # 今天、本月、一共（芸芸自己记的账）
        "zh": "芸芸花的钱：今天 {} · 本月 {} · 一共 {}（只算助手模式）。",
        "en": "Spent by Yunyun: today {} · this month {} · total {} (assistant mode only).",
        "ja": "芸芸が使った金額：今日 {} · 今月 {} · 合計 {}（アシスタントモードのみ）。",
    },
    "assistant_spent_balance": {   # Anthropic 没有查余额的接口，只能去官网看
        "zh": '<a href="{}">去官网看账户余额</a>',
        "en": '<a href="{}">Check your balance online</a>',
        "ja": '<a href="{}">残高を公式サイトで確認</a>',
    },
    "assistant_conversation_budget": {"zh": "每段对话最多花", "en": "Max per chat", "ja": "1 つの会話の上限"},
    "assistant_budget_low": {
        "zh": "⚠ 每次回答的上限有点低：每问一次至少要 1 美分左右，低于 $0.05 时芸芸可能总是答到一半就停。",
        "en": "⚠ The per-reply limit is quite low: each question costs at least about 1 cent, "
              "so below $0.05 Yunyun may often stop halfway.",
        "ja": "⚠ 1 回の上限が低めです：1 回の質問に最低 1 セントほどかかるので、$0.05 未満だと途中で止まることが多くなります。",
    },
    "assistant_budget_conversation_low": {
        "zh": "⚠ 每段对话的上限比每次回答的还低：一段对话可能只能问一次。",
        "en": "⚠ The per-chat limit is lower than the per-reply limit: a chat may only fit one question.",
        "ja": "⚠ 会話の上限が 1 回の上限より低いので、1 つの会話で 1 回しか質問できないかもしれません。",
    },
    "assistant_hint_anthropic": {
        "zh": "在 {} 申请 API Key（按用量付费，和 Claude 订阅分开计费）。点「测试并保存」只检查 Key 和模型，不会花钱。",
        "en": "Get an API key at {} (pay-as-you-go, billed separately from a Claude subscription). "
              "\"Test and Save\" only checks the key and model; it costs nothing.",
        "ja": "{} で API キーを取得してください（従量課金。Claude のサブスクリプションとは別に請求されます）。"
              "「テストして保存」はキーとモデルを確認するだけで、料金はかかりません。",
    },
    "assistant_privacy_note": {
        "zh": "助手模式里，你的提问和对话会发送给 {}；芸芸搜索、阅读的网页内容，以及选了工作文件夹时她读的文件、"
              "命令的结果也会经过它。API Key 只保存在这台电脑的 Windows 凭据管理器里。",
        "en": "In assistant mode, your questions and the conversation are sent to {}; web pages Yunyun searches and reads, "
              "and — with a work folder — the files she reads and command output, also pass through it. "
              "The API key is stored only in Windows Credential Manager on this PC.",
        "ja": "アシスタントモードでは、あなたの質問と会話が {} に送信されます。芸芸が検索・閲覧するウェブページの内容や、"
              "作業フォルダーを選んだ場合に読むファイル・コマンドの結果も経由します。API キーはこの PC の Windows 資格情報マネージャーにのみ保存されます。",
    },
    "assistant_testing": {"zh": "正在测试连接（不会花钱）……", "en": "Testing the connection (free)…", "ja": "接続をテスト中（無料）…"},
    "assistant_saved": {"zh": "连接成功，已保存！", "en": "Connected and saved!", "ja": "接続できました。保存しました！"},
    "assistant_empty_key": {"zh": "请先粘贴 API Key。", "en": "Please paste your API key first.", "ja": "まず API キーを貼り付けてください。"},
    "assistant_empty_model": {"zh": "请填写模型名。", "en": "Please enter the model name.", "ja": "モデル名を入力してください。"},
    "assistant_error_detail": {"zh": "（详细信息：{}）", "en": "(Details: {})", "ja": "（詳細：{}）"},
    # 出错的种类和 yunyun/assistant/protocol.py 的 ERROR_KINDS 一一对应（tests/test_i18n.py 会检查）
    "assistant_error_network": {
        "zh": "连不上服务商，检查一下网络或代理。",
        "en": "Couldn't reach the provider. Check your network or proxy.",
        "ja": "プロバイダーに接続できません。ネットワークかプロキシを確認してください。",
    },
    "assistant_error_auth": {
        "zh": "API Key 不对，或者已经过期了。",
        "en": "The API key is wrong or has expired.",
        "ja": "API キーが正しくないか、期限切れです。",
    },
    "assistant_error_billing": {
        "zh": "账户余额不足，去服务商的网站充值后再试。",
        "en": "Your account is out of credit. Top it up on the provider's website and try again.",
        "ja": "アカウントの残高が足りません。プロバイダーのサイトでチャージしてから、もう一度お試しください。",
    },
    "assistant_error_rate_limit": {
        "zh": "请求太频繁，被限流了，等一会儿再试。",
        "en": "Too many requests right now. Please wait a bit and try again.",
        "ja": "リクエストが多すぎます。少し待ってからもう一度お試しください。",
    },
    "assistant_error_budget": {
        "zh": "这段对话加起来花到上限了，芸芸先停下了。开个新对话就能继续（卡片堆右上角，或者 Ctrl+N）。",
        "en": "This chat has reached its total spending limit, so Yunyun stopped. Start a new chat to continue "
              "(top-right of the cards, or Ctrl+N).",
        "ja": "この会話の合計が上限に達したので、芸芸は止まりました。新しい会話を始めれば続けられます"
              "（カードの右上、または Ctrl+N）。",
    },
    "assistant_error_reply_budget": {
        "zh": "这次回答快超过上限了，芸芸先停下了。",
        "en": "This reply was about to go over the limit, so Yunyun stopped.",
        "ja": "今回の回答が上限を超えそうだったので、芸芸は止まりました。",
    },
    "assistant_error_missing_cli": {
        "zh": "找不到 Claude Code 程序，可能需要重新安装芸芸。",
        "en": "Claude Code couldn't be found. Yunyun may need to be reinstalled.",
        "ja": "Claude Code が見つかりません。芸芸の再インストールが必要かもしれません。",
    },
    "assistant_error_no_key": {
        "zh": "还没有填 API Key：右键芸芸 → 设置… → 助手。",
        "en": "No API key yet: right-click Yunyun → Settings… → Assistant.",
        "ja": "API キーがまだありません：芸芸を右クリック → 設定… → アシスタント。",
    },
    "assistant_error_worker": {
        "zh": "助手没能启动，或者意外退出了。可以再试一次，日志里有详细原因。",
        "en": "The assistant couldn't start or quit unexpectedly. Try again; the log has details.",
        "ja": "アシスタントを起動できなかったか、予期せず終了しました。もう一度お試しください。詳細はログにあります。",
    },
    "assistant_error_model": {
        "zh": "服务商那里没有这个模型，检查一下模型名。",
        "en": "The provider doesn't have this model. Check the model name.",
        "ja": "プロバイダーにこのモデルがありません。モデル名を確認してください。",
    },
    "assistant_error_other": {"zh": "出错了。", "en": "Something went wrong.", "ja": "エラーが発生しました。"},
    # ---- 助手模式的界面（pet/assistant_mode.py、assistant_input.py）----
    "assistant_send": {"zh": "发送", "en": "Send", "ja": "送信"},
    "assistant_stop": {"zh": "停止", "en": "Stop", "ja": "停止"},
    "assistant_input_placeholder": {"zh": "问芸芸点什么……", "en": "Ask Yunyun something…", "ja": "芸芸に聞いてみよう…"},
    "assistant_connecting": {"zh": "正在连接大脑……", "en": "Connecting…", "ja": "接続中…"},
    "assistant_thinking": {"zh": "芸芸在想……", "en": "Yunyun is thinking…", "ja": "芸芸が考え中…"},
    "assistant_tool_web_search": {"zh": "正在搜索网页……", "en": "Searching the web…", "ja": "ウェブを検索中…"},
    "assistant_tool_web_fetch": {"zh": "正在看网页……", "en": "Reading a web page…", "ja": "ウェブページを読んでいます…"},
    "assistant_tool_other": {"zh": "正在使用 {}……", "en": "Using {}…", "ja": "{} を使用中…"},
    "assistant_tool_read": {"zh": "正在读……", "en": "Reading…", "ja": "読んでいます…"},
    "assistant_tool_search_files": {"zh": "正在找文件……", "en": "Searching files…", "ja": "ファイルを検索中…"},
    "assistant_tool_edit": {"zh": "正在改……", "en": "Editing…", "ja": "編集中…"},
    "assistant_tool_write": {"zh": "正在写……", "en": "Writing…", "ja": "書いています…"},
    "assistant_tool_command": {"zh": "正在执行命令……", "en": "Running a command…", "ja": "コマンドを実行中…"},
    "assistant_waiting_confirm": {"zh": "等你确认……", "en": "Waiting for your OK…", "ja": "確認を待っています…"},
    # 确认卡片
    "assistant_confirm_command": {"zh": "芸芸想执行一条命令", "en": "Yunyun wants to run a command",
                                  "ja": "芸芸がコマンドを実行しようとしています"},
    "assistant_confirm_command_danger": {"zh": "芸芸想执行一条有风险的命令", "en": "Yunyun wants to run a risky command",
                                         "ja": "芸芸がリスクのあるコマンドを実行しようとしています"},
    "assistant_confirm_read_outside": {"zh": "芸芸想读工作文件夹以外的文件",
                                       "en": "Yunyun wants to read a file outside the work folder",
                                       "ja": "芸芸が作業フォルダー外のファイルを読もうとしています"},
    "assistant_confirm_search_outside": {"zh": "芸芸想在工作文件夹以外搜索",
                                         "en": "Yunyun wants to search outside the work folder",
                                         "ja": "芸芸が作業フォルダー外を検索しようとしています"},
    "assistant_confirm_edit": {"zh": "芸芸想改这个文件", "en": "Yunyun wants to edit this file",
                               "ja": "芸芸がこのファイルを編集しようとしています"},
    "assistant_confirm_write": {"zh": "芸芸想写这个文件（没有就新建，有就整个覆盖）",
                                "en": "Yunyun wants to write this file (creates it, or replaces it entirely)",
                                "ja": "芸芸がこのファイルを書こうとしています（なければ新規作成、あれば全体を上書き）"},
    "assistant_confirm_edit_sensitive": {"zh": "芸芸想改一个特殊的文件", "en": "Yunyun wants to change a special file",
                                         "ja": "芸芸が特別なファイルを変更しようとしています"},
    "assistant_confirm_write_outside": {"zh": "芸芸想写工作文件夹以外的文件",
                                        "en": "Yunyun wants to write a file outside the work folder",
                                        "ja": "芸芸が作業フォルダー外のファイルに書き込もうとしています"},
    "assistant_confirm_other": {"zh": "芸芸想用 {}", "en": "Yunyun wants to use {}", "ja": "芸芸が {} を使おうとしています"},
    "assistant_confirm_note_danger": {
        "zh": "这类命令可能删除文件、安装软件、联网下载或改动系统，所以每次都要问你。",
        "en": "Commands like this may delete files, install software, download things or change the system, "
              "so you'll be asked every time.",
        "ja": "この種のコマンドはファイルの削除、ソフトのインストール、ダウンロード、システムの変更をする可能性があるため、毎回確認します。",
    },
    "assistant_confirm_note_sensitive": {
        "zh": ".git、.claude 里的文件改了，可能让程序以后自动运行别的东西，所以每次都要问你。",
        "en": "Changing files in .git or .claude can make programs run other things later, so you'll be asked every time.",
        "ja": ".git や .claude のファイルを変えると、後で別のものが自動で実行される可能性があるため、毎回確認します。",
    },
    "assistant_confirm_note_unclear": {
        "zh": "看不清这条命令具体会做什么（比如命令里套命令、往文件里写），所以每次都要问你。",
        "en": "It's hard to tell exactly what this command does (e.g. nested commands, writing to files), "
              "so you'll be asked every time.",
        "ja": "このコマンドが何をするか正確に分からない（コマンドの入れ子、ファイルへの書き込みなど）ため、毎回確認します。",
    },
    "assistant_confirm_said_by": {"zh": "芸芸说：{}", "en": "Yunyun says: {}", "ja": "芸芸：{}"},
    "assistant_confirm_more": {"zh": "还有 {} 个", "en": "{} more", "ja": "あと {} 件"},
    "assistant_confirm_more_lines": {"zh": "……还有 {} 行", "en": "…{} more lines", "ja": "…あと {} 行"},
    # 改动对比（pet/change_diff.py）
    "assistant_diff_new_file": {"zh": "新文件", "en": "New file", "ja": "新しいファイル"},
    "assistant_diff_not_found": {
        "zh": "在文件里没找到要改的那一段（可能会改失败），下面只对比这一段：",
        "en": "Couldn't find the part to change in the file (the edit may fail); comparing just that part:",
        "ja": "変更する部分がファイル内に見つかりません（失敗するかもしれません）。その部分だけ比べます：",
    },
    "assistant_diff_unreadable": {
        "zh": "原文件太大或者不是文字，下面只显示改动的这一段：",
        "en": "The original file is too big or isn't text; showing just the change:",
        "ja": "元のファイルが大きすぎるかテキストではないため、変更部分だけ表示します：",
    },
    "assistant_diff_no_change": {"zh": "（内容没有变化）", "en": "(No actual change)", "ja": "（内容の変化なし）"},
    "assistant_confirm_reason": {
        "zh": "不同意的话，可以写一句理由告诉芸芸（Enter = 拒绝）",
        "en": "If not, you can tell Yunyun why (Enter = deny)",
        "ja": "断る場合は理由を一言どうぞ（Enter = 拒否）",
    },
    "assistant_confirm_deny": {"zh": "拒绝", "en": "Deny", "ja": "拒否"},
    "assistant_confirm_deny_tip": {"zh": "拒绝（Esc）", "en": "Deny (Esc)", "ja": "拒否（Esc）"},
    "assistant_confirm_allow": {"zh": "允许一次", "en": "Allow once", "ja": "今回だけ許可"},
    "assistant_confirm_remember_command": {"zh": "这次对话里 {} 都允许", "en": "Allow {} for this chat",
                                           "ja": "この会話では {} を許可"},
    "assistant_confirm_remember_folder": {"zh": "这次对话读这个文件夹都允许", "en": "Allow this folder for this chat",
                                          "ja": "この会話ではこのフォルダーを許可"},
    "assistant_confirm_remember_edit": {"zh": "这次对话改文件都允许", "en": "Allow edits for this chat",
                                        "ja": "この会話では編集を許可"},
    "assistant_confirm_waiting_said": {
        "zh": "有件事要你确认：右键我 →「✦ 助手模式」",
        "en": "I need your OK on something: right-click me → \"✦ Assistant mode\"",
        "ja": "確認してほしいことがあるよ：私を右クリック →「✦ アシスタントモード」",
    },
    "assistant_stopped": {"zh": "（已停止）", "en": "(Stopped)", "ja": "（停止しました）"},
    "assistant_cost": {   # 这一轮花费、这次对话累计（美元）
        "zh": "这一轮 ${:.4f} · 这次对话共 ${:.4f}",
        "en": "This reply ${:.4f} · This chat ${:.4f}",
        "ja": "今回 ${:.4f} · この会話の合計 ${:.4f}",
    },
    "assistant_cost_tokens": {   # 不按美元计费的服务商：输入 / 输出 token
        "zh": "这一轮 {} / {} token（输入 / 输出）",
        "en": "This reply {} / {} tokens (in / out)",
        "ja": "今回 {} / {} トークン（入力 / 出力）",
    },
    "assistant_new_chat": {"zh": "+ 新对话", "en": "+ New chat", "ja": "+ 新しい会話"},
    "assistant_new_chat_said": {"zh": "好，我们重新开始～", "en": "OK, let's start fresh!", "ja": "よし、新しく始めよう～"},
    "assistant_divider": {"zh": "新对话 · {}", "en": "New chat · {}", "ja": "新しい会話 · {}"},
    "assistant_folder_tip": {
        "zh": "工作文件夹：{}\n点一下换",
        "en": "Work folder: {}\nClick to change",
        "ja": "作業フォルダー：{}\nクリックで変更",
    },
    "assistant_folder_none_tip": {
        "zh": "还没选工作文件夹，现在只聊天\n点一下选一个（比如你的项目）",
        "en": "No work folder yet — chat only\nClick to pick one (e.g. your project)",
        "ja": "作業フォルダー未選択：今はおしゃべりだけ\nクリックで選ぶ（プロジェクトなど）",
    },
    "assistant_folder_choose": {"zh": "选择文件夹…", "en": "Choose folder…", "ja": "フォルダーを選ぶ…"},
    "assistant_folder_none": {"zh": "不用文件夹（只聊天）", "en": "No folder (chat only)", "ja": "フォルダーなし（おしゃべりだけ）"},
    "assistant_folder_dialog": {"zh": "选择芸芸的工作文件夹", "en": "Choose Yunyun's work folder",
                                "ja": "芸芸の作業フォルダーを選ぶ"},
    "assistant_folder_said": {
        "zh": "好，接下来在「{}」里干活～（新对话）",
        "en": "OK, working in \"{}\" now (new chat)",
        "ja": "よし、これから「{}」で作業するね～（新しい会話）",
    },
    "assistant_folder_none_said": {
        "zh": "好，不用文件夹了，我们只聊天～（新对话）",
        "en": "OK, no folder — just chatting now (new chat)",
        "ja": "よし、フォルダーなしでおしゃべりしよう～（新しい会話）",
    },
    "assistant_card_you": {"zh": "你：{}", "en": "You: {}", "ja": "あなた：{}"},
    "assistant_card_stopped": {"zh": "已停止", "en": "Stopped", "ja": "停止"},
    "assistant_card_error": {"zh": "出错了", "en": "Error", "ja": "エラー"},
    "assistant_card_empty": {"zh": "（没有回答）", "en": "(No answer)", "ja": "（回答なし）"},
    "assistant_card_tokens": {"zh": "{} / {} token", "en": "{} / {} tokens", "ja": "{} / {} トークン"},
    "assistant_card_files": {"zh": "改了 {} 个文件", "en": "{} file(s) changed", "ja": "{} 個のファイルを変更"},
    "assistant_card_undone": {"zh": "已撤销", "en": "Undone", "ja": "取り消し済み"},
    # 撤销（阅读框底部）
    "assistant_undo": {"zh": "↶ 撤销这次改动", "en": "↶ Undo these changes", "ja": "↶ この変更を取り消す"},
    "assistant_undo_confirm": {"zh": "确定撤销？再点一次", "en": "Sure? Click again", "ja": "本当に？もう一度クリック"},
    "assistant_undo_working": {"zh": "正在撤销……", "en": "Undoing…", "ja": "取り消し中…"},
    "assistant_undo_done": {"zh": "已撤销", "en": "Undone", "ja": "取り消し済み"},
    "assistant_undo_tip": {
        "zh": "把芸芸这一问改的 {} 个文件恢复原样，之后几问的改动也会一起撤销。\n"
              "用命令改的（比如命令生成、删掉的文件）撤销不了，可以用 git 恢复。",
        "en": "Restores the {} file(s) Yunyun changed in this reply; changes from later replies are undone too.\n"
              "Changes made by commands (e.g. files a command created or deleted) can't be undone — use git for those.",
        "ja": "このやり取りで芸芸が変更した {} 個のファイルを元に戻します。その後のやり取りの変更も一緒に取り消されます。\n"
              "コマンドによる変更（コマンドで作成・削除したファイルなど）は取り消せません。git で戻してください。",
    },
    "assistant_undo_tip_busy": {"zh": "芸芸回答完才能撤销", "en": "You can undo once Yunyun has finished replying",
                                "ja": "芸芸の回答が終わってから取り消せます"},
    "assistant_undo_tip_expired": {
        "zh": "开了新对话（或者助手重新启动过）以后就撤销不了了，可以用 git 恢复",
        "en": "Can't undo after a new chat (or after the assistant restarted) — use git to restore",
        "ja": "新しい会話を始めた後（またはアシスタントの再起動後）は取り消せません。git で戻してください",
    },
    "assistant_undo_said": {
        "zh": "撤销好了：{} 个文件恢复了原样～（用命令改的撤销不了哦）",
        "en": "Undone: {} file(s) are back to how they were (changes made by commands can't be undone)",
        "ja": "取り消したよ：{} 個のファイルを元に戻しました～（コマンドによる変更は戻せません）",
    },
    "assistant_undo_failed": {
        "zh": "撤销没做成，文件可能没有恢复。可以用 git 恢复。",
        "en": "Couldn't undo — the files may not be restored. You can use git to restore them.",
        "ja": "取り消しに失敗しました。ファイルは戻っていないかもしれません。git で戻せます。",
    },
    "assistant_reader_copy": {"zh": "复制", "en": "Copy", "ja": "コピー"},
    "assistant_reader_copied": {"zh": "已复制 ✓", "en": "Copied ✓", "ja": "コピーしました ✓"},
    "assistant_reader_copy_all": {"zh": "复制回答", "en": "Copy answer", "ja": "回答をコピー"},
    "assistant_reader_close": {"zh": "关闭（Esc）", "en": "Close (Esc)", "ja": "閉じる（Esc）"},
    "assistant_fix_in_settings": {
        "zh": "可以在这里改：右键芸芸 → 设置… → 助手。",
        "en": "You can change it in: right-click Yunyun → Settings… → Assistant.",
        "ja": "ここで変更できます：芸芸を右クリック → 設定… → アシスタント。",
    },
    "assistant_need_key": {
        "zh": "先帮我接上大脑吧：填好 API Key 就能用助手模式啦～",
        "en": "Connect my brain first: add an API key and assistant mode is ready!",
        "ja": "まず頭脳をつないでね：API キーを入れるとアシスタントモードが使えるよ～",
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
