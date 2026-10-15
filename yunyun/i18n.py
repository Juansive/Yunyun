"""界面文字（气泡 + 右键菜单 + 托盘）的多语言（中 / 英 / 日）。

用法：
    from yunyun import i18n
    i18n.tr("exit_hint")   # 返回当前语言的文字

选择的语言保存在 QSettings 里（Windows 上存在注册表
HKEY_CURRENT_USER\\Software\\Yunyun\\Yunyun），下次启动自动恢复。

Qt 自带的文字（确认框的 Yes / No 按钮等）用 Qt 的翻译文件跟着切换（qt_translation_dir）。
"""
import logging
from pathlib import Path

import PySide6
from PySide6.QtCore import QCoreApplication, QSettings, QTranslator

LANGUAGES = {"zh": "中文", "en": "English", "ja": "日本語"}  # 代码 → 菜单里显示的名字
DEFAULT_LANGUAGE = "zh"
_SETTINGS_KEY = "bubble/language"
# Qt 自带文字的翻译文件（打包时 build_exe 只留这两种）；英文是 Qt 本来的样子，不用装
QT_TRANSLATIONS = {"zh": "qtbase_zh_CN", "ja": "qtbase_ja"}

log = logging.getLogger(__name__)

# 每条文字一个 key，下面是各语言的版本。以后加新文字就在这里加一项。
TEXTS = {
    "app_name": {"zh": "芸芸", "en": "Yunyun", "ja": "芸芸（ユンユン）"},

    # ---- 右键菜单 ----
    "menu_settings": {"zh": "设置…", "en": "Settings…", "ja": "設定…"},
    "menu_assistant": {"zh": "✦ 助手模式", "en": "✦ Assistant mode", "ja": "✦ アシスタントモード"},
    "menu_assistant_stop": {"zh": "■ 停止助手", "en": "■ Stop the assistant", "ja": "■ アシスタントを停止"},
    "menu_assistant_hush": {"zh": "别说了", "en": "Stop talking", "ja": "話すのをやめる"},
    "menu_assistant_hide_cards": {"zh": "隐藏卡片", "en": "Hide cards", "ja": "カードを隠す"},
    "menu_assistant_reset_place": {"zh": "卡片回到芸芸旁边", "en": "Put cards back next to Yunyun",
                                   "ja": "カードを芸芸のそばに戻す"},
    "menu_assistant_exit": {"zh": "退出助手模式", "en": "Exit assistant mode", "ja": "アシスタントモードを終了"},
    # 助手模式开着时的两个子菜单（2026-10-16 精简右键菜单）
    "menu_assistant_more": {"zh": "助手", "en": "Assistant", "ja": "アシスタント"},
    "menu_mail": {"zh": "邮件", "en": "Mail", "ja": "メール"},
    "menu_language": {"zh": "语言", "en": "Language", "ja": "言語"},
    "pet_size": {"zh": "芸芸大小", "en": "Size", "ja": "芸芸の大きさ"},   # 设置 → 通用的滑块
    "pet_size_reset": {"zh": "恢复默认", "en": "Reset", "ja": "元に戻す"},
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
    "window_minimize": {"zh": "最小化", "en": "Minimize", "ja": "最小化"},
    "window_maximize": {"zh": "最大化", "en": "Maximize", "ja": "最大化"},
    "window_restore": {"zh": "向下还原", "en": "Restore", "ja": "元に戻す"},
    "window_close": {"zh": "关闭", "en": "Close", "ja": "閉じる"},
    "page_general": {"zh": "通用", "en": "General", "ja": "一般"},
    "page_mail": {"zh": "邮箱", "en": "Mail", "ja": "メール"},
    "page_ai": {"zh": "AI", "en": "AI", "ja": "AI"},
    "page_assistant": {"zh": "助手", "en": "Assistant", "ja": "アシスタント"},
    "page_notebook": {"zh": "记事本", "en": "Notebook", "ja": "メモ帳"},
    "page_voice": {"zh": "语音", "en": "Voice", "ja": "音声"},
    "page_watch": {"zh": "旁观", "en": "Watch", "ja": "見守り"},
    # ---- 设置 → 旁观（pet/watch_page.py，阶段 8）和提醒（pet/watch_reminder.py）----
    "watch_intro": {
        "zh": "旁观模式：你自己用 Claude Code（终端、VS Code、Claude 桌面版都行）时，它做完一轮、等你确认或者让你选的时候，"
              "芸芸提醒你；你正看着那个窗口时不打扰。芸芸被挡住时在任务栏上方弹一个小条，点一下就切过去。不用打开助手模式。",
        "en": "Watch mode: when you use Claude Code yourself (terminal, VS Code or the Claude desktop app), Yunyun lets you "
              "know when it finishes a reply, needs your permission or wants you to pick an option — but not while you're "
              "looking at that window. If Yunyun is covered, a small bar pops up above the taskbar; click it to switch "
              "over. Assistant mode doesn't need to be open.",
        "ja": "見守りモード：あなた自身が Claude Code（ターミナル、VS Code、Claude デスクトップ版）を使うとき、"
              "返答が終わった・確認が必要・選んでほしいときに芸芸が知らせます。そのウィンドウを見ている間は邪魔しません。"
              "芸芸が隠れているときはタスクバーの上に小さなバーが出て、クリックすると切り替わります。アシスタントモードは不要です。",
    },
    "watch_status_connected": {"zh": "✓ 已经连接", "en": "✓ Connected", "ja": "✓ 接続済み"},
    "watch_status_disconnected": {"zh": "还没有连接", "en": "Not connected", "ja": "未接続"},
    "watch_status_partial": {"zh": "只连了一半（你可能手动改过设置）：点\"重新连接\"修好",
                             "en": "Only half connected (the settings may have been edited by hand): click \"Reconnect\"",
                             "ja": "半分だけ接続されています（設定を手で変えたかも）：「再接続」で直せます"},
    "watch_status_broken": {"zh": "Claude Code 的设置文件看不懂（不是合法的 JSON），芸芸不会动它，请先修好",
                            "en": "Claude Code's settings file isn't valid JSON; Yunyun won't touch it — please fix it first",
                            "ja": "Claude Code の設定ファイルが正しい JSON ではありません。芸芸は触りません。先に直してください"},
    "watch_settings_file": {"zh": "会改的文件：{}", "en": "File it changes: {}", "ja": "変更するファイル：{}"},
    "watch_connect": {"zh": "连接我的 Claude Code", "en": "Connect my Claude Code", "ja": "Claude Code に接続"},
    "watch_reconnect": {"zh": "重新连接", "en": "Reconnect", "ja": "再接続"},
    "watch_disconnect": {"zh": "断开", "en": "Disconnect", "ja": "切断"},
    "watch_test": {"zh": "发一条测试提醒", "en": "Send a test reminder", "ja": "テスト通知を送る"},
    "watch_note": {
        "zh": "连接 = 在这个文件里加两条 hook：做完时、等你确认时运行芸芸的小脚本（几十毫秒就完；芸芸没开时什么都不做）。"
              "改之前先备份到同一个文件夹；你自己的 hook 和别的设置不动；断开只删芸芸的两条。"
              "已经开着的 Claude Code 要重新打开才生效。",
        "en": "Connecting adds two hooks to this file: Yunyun's tiny script runs when a reply is done and when your "
              "permission is needed (it takes milliseconds and does nothing if Yunyun isn't running). The file is backed "
              "up first, in the same folder; your own hooks and other settings are left alone, and disconnecting removes "
              "only Yunyun's two. Claude Code sessions that are already open need a restart to pick it up.",
        "ja": "接続すると、このファイルにフックを 2 つ追加します：返答が終わったとき・確認が必要なときに芸芸の小さなスクリプトが"
              "動きます（数十ミリ秒で終わり、芸芸が起動していなければ何もしません）。変更前に同じフォルダーにバックアップし、"
              "あなたのフックや他の設定には触りません。切断すると芸芸の 2 つだけを削除します。"
              "すでに開いている Claude Code は開き直すと反映されます。",
    },
    "watch_connect_confirm": {
        "zh": "芸芸要在你的 Claude Code 设置里加两条 hook：\n{}\n\n改之前会先备份到同一个文件夹。继续吗？",
        "en": "Yunyun will add two hooks to your Claude Code settings:\n{}\n\nThe file is backed up first. Continue?",
        "ja": "芸芸が Claude Code の設定にフックを 2 つ追加します：\n{}\n\n変更前にバックアップします。続けますか？",
    },
    "watch_connected": {"zh": "连接好了。原来的设置备份在：{}", "en": "Connected. The old settings are backed up at: {}",
                        "ja": "接続しました。元の設定のバックアップ：{}"},
    "watch_connected_new": {"zh": "连接好了（原来没有设置文件，新建了一个）。",
                            "en": "Connected (there was no settings file, so a new one was created).",
                            "ja": "接続しました（設定ファイルがなかったので新しく作りました）。"},
    "watch_disconnected": {"zh": "断开了：芸芸的两条 hook 删掉了。", "en": "Disconnected: Yunyun's two hooks were removed.",
                           "ja": "切断しました：芸芸のフック 2 つを削除しました。"},
    "watch_error_broken": {"zh": "没改：设置文件不是合法的 JSON（{}）", "en": "Nothing changed: the settings file isn't valid JSON ({})",
                           "ja": "変更していません：設定ファイルが正しい JSON ではありません（{}）"},
    "watch_error_io": {"zh": "读写设置文件出错（{}）", "en": "Couldn't read or write the settings file ({})",
                       "ja": "設定ファイルの読み書きに失敗しました（{}）"},
    "watch_test_text": {"zh": "这是一条测试提醒～", "en": "This is a test reminder~", "ja": "これはテスト通知です～"},
    "watch_stop": {"zh": "Claude Code 做完了 · {}", "en": "Claude Code is done · {}", "ja": "Claude Code 完了 · {}"},
    "watch_stop_plain": {"zh": "Claude Code 做完了", "en": "Claude Code is done", "ja": "Claude Code 完了"},
    "watch_permission": {"zh": "Claude Code 等你确认 · {}", "en": "Claude Code needs your OK · {}",
                         "ja": "Claude Code が確認待ち · {}"},
    "watch_permission_plain": {"zh": "Claude Code 等你确认", "en": "Claude Code needs your OK", "ja": "Claude Code が確認待ち"},
    "watch_choice": {"zh": "Claude Code 等你选一下 · {}", "en": "Claude Code wants you to choose · {}",
                     "ja": "Claude Code が選択待ち · {}"},
    "watch_choice_plain": {"zh": "Claude Code 等你选一下", "en": "Claude Code wants you to choose",
                           "ja": "Claude Code が選択待ち"},
    # ---- 设置 → 记事本（pet/notebook_page.py，阶段 10 的 B）----
    "notebook_enabled": {"zh": "让芸芸用记事本（记住你的习惯、项目的规矩）",
                         "en": "Let Yunyun keep a notebook (your habits, project rules)",
                         "ja": "芸芸にメモ帳を使わせる（あなたの習慣・プロジェクトの決まり）"},
    "notebook_hint": {
        "zh": "芸芸聊天、干活时会把以后用得上的事记下来（你的偏好、项目的约定、踩过的坑）；你说\"记住……\"时一定记。"
              "\"关于你\"的每段对话都会用到，项目的只在同一个工作文件夹里用到。记事本只存在你的电脑上，"
              "每段对话开始时目录会发给你选的服务商。改动从下一段对话开始生效。",
        "en": "While chatting and working, Yunyun notes things that will be useful later (your preferences, project "
              "conventions, pitfalls), and always does when you say \"remember …\". Entries about you are used in "
              "every chat; project entries only in the same work folder. The notebook stays on your computer; its index "
              "is sent to your chosen provider at the start of each chat. Changes apply from the next chat.",
        "ja": "芸芸は会話や作業の中で、後で役立つこと（あなたの好み、プロジェクトの決まり、ハマりどころ）を記録します。"
              "「覚えておいて」と言われたら必ず記録します。「あなたについて」はすべての会話で、プロジェクトの項目は同じ"
              "作業フォルダーでだけ使われます。メモ帳はパソコンだけに保存され、会話の始めに目次が選んだプロバイダーに送られます。"
              "変更は次の会話から有効です。",
    },
    "rapport_enabled": {"zh": "让芸芸回想以前的对话（相处笔记 + 回忆；每段新对话开始时回想一下上一段，一般不到 1 美分）",
                        "en": "Let Yunyun look back at earlier chats (rapport notes + recollections; she looks back at "
                              "the last chat when a new one starts, usually under 1¢)",
                        "ja": "芸芸に前の会話を振り返らせる（付き合いメモ＋思い出。新しい会話のたびに前の会話を振り返る。ふつう 1 セント未満）"},
    "rapport_hint": {
        "zh": "相处笔记：芸芸只记\"怎么跟你相处\"（称呼、语气、玩笑尺度、你累的时候希望她怎么做），接在她的性格后面。"
              "她自己观察到的要在两段对话里都出现才会用上，只出现一次的是\"观察中\"（30 天没再出现就扔掉）；你明确说过的马上用上。"
              "回忆：每段对话记一句话的摘要（留一年），最近 5 段每段对话都带着，她能接上话；更早的她会自己翻。"
              "回想时你和她的问答会发给你选的服务商（用最便宜的模型），笔记和回忆只存在你的电脑上。",
        "en": "Rapport notes: Yunyun only notes how to get along with you (how to address you, tone, humor, what helps "
              "when you're tired), added after her personality. What she notices herself is used only after it shows "
              "up in two chats; seen once, it stays \"observing\" (dropped after 30 days). Things you ask for directly "
              "apply right away. Recollections: a one-line summary of each chat (kept for a year); the latest 5 come "
              "with every chat so she can pick up where you left off, and she looks up older ones herself. When she "
              "looks back, your questions and her answers are sent to your provider (its cheapest model); notes and "
              "recollections stay on this PC.",
        "ja": "付き合いメモ：芸芸は「あなたとの付き合い方」（呼び方・口調・冗談の加減・疲れたときにどうしてほしいか）だけを記録し、"
              "性格のあとに付け加えます。自分で気づいたことは 2 つの会話で出てきてから使い、1 回だけのものは「観察中」"
              "（30 日出てこなければ消します）。あなたがはっきり頼んだことはすぐ使います。"
              "思い出：会話ごとに一行の要約を残します（1 年間）。最新の 5 件は毎回の会話に付けるので話をつなげられ、"
              "それより前は芸芸が自分で調べます。振り返るときは、あなたの質問と芸芸の回答を選んだプロバイダー（いちばん安いモデル）に"
              "送ります。メモと思い出はこの PC にだけ保存されます。",
    },
    "recollections_group": {"zh": "回忆（{} 段）", "en": "Recollections ({})", "ja": "思い出（{} 件）"},
    "recollections_edit_title": {"zh": "编辑回忆", "en": "Edit recollection", "ja": "思い出を編集"},
    "rapport_group": {"zh": "相处笔记（{} 条在用，{} 条观察中）", "en": "Rapport notes ({} in use, {} observing)",
                      "ja": "付き合いメモ（使用中 {} 件、観察中 {} 件）"},
    "rapport_waiting": {"zh": "观察中", "en": "observing", "ja": "観察中"},
    "rapport_explicit": {"zh": "你明确说过的", "en": "You asked for this", "ja": "あなたがはっきり頼んだこと"},
    "rapport_seen": {"zh": "在 {} 段对话里出现过", "en": "Seen in {} chat(s)", "ja": "{} つの会話で出てきた"},
    "rapport_edit_title": {"zh": "编辑相处笔记", "en": "Edit rapport note", "ja": "付き合いメモを編集"},
    "notebook_about_you": {"zh": "关于你（{}）", "en": "About you ({})", "ja": "あなたについて（{}）"},
    "notebook_project": {"zh": "项目：{}（{}）", "en": "Project: {} ({})", "ja": "プロジェクト：{}（{}）"},
    "notebook_empty": {
        "zh": "记事本还是空的。和芸芸聊天时说\"记住……\"，或者让她干活时自己记。",
        "en": "The notebook is empty. Say \"remember …\" to Yunyun, or let her take notes while she works.",
        "ja": "メモ帳はまだ空です。芸芸に「覚えておいて」と言うか、作業中に記録してもらいましょう。",
    },
    "notebook_edit": {"zh": "编辑…", "en": "Edit…", "ja": "編集…"},
    "notebook_delete": {"zh": "删除", "en": "Delete", "ja": "削除"},
    "notebook_clear": {"zh": "全部清空", "en": "Clear all", "ja": "すべて消去"},
    "notebook_clear_confirm": {"zh": "确定要删掉记事本里的全部 {} 条吗？删掉就找不回来了。",
                               "en": "Delete all {} entries in the notebook? This can't be undone.",
                               "ja": "メモ帳の {} 件をすべて削除しますか？元に戻せません。"},
    "notebook_edit_title": {"zh": "编辑记事本", "en": "Edit notebook entry", "ja": "メモ帳を編集"},
    "notebook_title_label": {"zh": "标题（一句话）", "en": "Title (one line)", "ja": "タイトル（一文）"},
    "notebook_content_label": {"zh": "细节（可以不填）", "en": "Details (optional)", "ja": "詳細（省略可）"},
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
    # 新版本提醒（notify/update.py）
    "update_available": {
        "zh": "有新版本 {} 啦～右键我就能去下载",
        "en": "Version {} is out! Right-click me to download it~",
        "ja": "新しいバージョン {} が出たよ～右クリックでダウンロードできるよ",
    },
    "menu_update": {"zh": "⬆ 下载新版本 {}", "en": "⬆ Download version {}", "ja": "⬆ 新バージョン {} をダウンロード"},
    "setting_auto_update": {"zh": "自动检查新版本", "en": "Check for new versions automatically", "ja": "新しいバージョンを自動で確認"},
    "setting_auto_update_tip": {
        "zh": "每天最多问一次 GitHub 最新的版本号，不会发送你的任何信息。",
        "en": "Asks GitHub for the latest version number at most once a day. None of your information is sent.",
        "ja": "1 日に 1 回まで GitHub に最新のバージョン番号を問い合わせます。あなたの情報は一切送りません。",
    },
    "about_check_update": {"zh": "检查更新", "en": "Check for Updates", "ja": "更新を確認"},
    "about_checking": {"zh": "正在检查……", "en": "Checking…", "ja": "確認中…"},
    "about_up_to_date": {"zh": "已经是最新版本啦。", "en": "You're on the latest version.", "ja": "最新バージョンです。"},
    "about_update_found": {"zh": "有新版本 {}：{}", "en": "Version {} is available: {}", "ja": "新しいバージョン {} があります：{}"},
    "about_download": {"zh": "去下载", "en": "Download", "ja": "ダウンロード"},
    "about_update_failed": {
        "zh": "没查到：连不上 GitHub，等会儿再试试。",
        "en": "Couldn't reach GitHub. Please try again later.",
        "ja": "GitHub に接続できませんでした。あとでもう一度試してね。",
    },
    "about_privacy_title": {"zh": "隐私", "en": "Privacy", "ja": "プライバシー"},
    "about_privacy_text": {
        "zh": "· 芸芸不会改动你的邮箱：看过的邮件在邮箱里仍然是未读。\n"
              "· 只有你点了小红点，才会把邮件的发件人、标题、附件名和正文开头（最多 1500 字）发给你选择的 AI 服务商；拖走小红点清掉的邮件不会发送。\n"
              "· 芸芸只检测你有没有在打字，从不记录按了哪个键。\n"
              "· 日志里不写邮件内容、授权码和 API Key，邮箱地址会打码。\n"
              "· 每天最多问一次 GitHub 最新的版本号（设置 → 通用 可以关掉），不发送你的任何信息。",
        "en": "· Yunyun never changes your mailbox: emails it reads stay unread.\n"
              "· Only after you click the badge are the sender, subject, attachment names and the beginning of the body (up to 1,500 characters) sent to the AI provider you chose. Emails you dismiss are never sent.\n"
              "· Yunyun only notices whether you're typing — it never records which keys you press.\n"
              "· Logs never contain email content, authorization codes or API keys, and email addresses are masked.\n"
              "· At most once a day, Yunyun asks GitHub for the latest version number (you can turn this off in Settings → General). None of your information is sent.",
        "ja": "· 芸芸はメールボックスを変更しません。読んだメールも未読のままです。\n"
              "· バッジをクリックしたときだけ、差出人・件名・添付ファイル名・本文の冒頭（最大 1,500 文字）が選択した AI プロバイダーに送られます。消したメールは送られません。\n"
              "· 芸芸は入力しているかどうかだけを検知し、どのキーを押したかは記録しません。\n"
              "· ログにはメール本文・認証コード・API キーは書かれず、メールアドレスは伏せ字になります。\n"
              "· 1 日に 1 回まで GitHub に最新のバージョン番号を問い合わせます（設定 → 一般 でオフにできます）。あなたの情報は一切送りません。",
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
    # 授权码怎么拿：{} 是能点的链接（mail_page.HELP_URLS）
    "mail_hint_qq": {
        "zh": "授权码怎么拿：打开 {} 登录 → 设置 → 账号与安全 → 开启「IMAP/SMTP 服务」→ 生成授权码（16 位字母）。",
        "en": "How to get the code: open {} and sign in → Settings → Account & Security → enable “IMAP/SMTP service” → generate an authorization code (16 letters).",
        "ja": "認証コードの取得：{} を開いてログイン → 設定 → アカウントとセキュリティ →「IMAP/SMTPサービス」を有効化 → 認証コード（16文字）を生成。",
    },
    "mail_hint_163": {
        "zh": "授权码怎么拿：打开 {} 登录 → 设置 → POP3/SMTP/IMAP → 开启「IMAP/SMTP 服务」→ 新增授权密码。",
        "en": "How to get the code: open {} and sign in → Settings → POP3/SMTP/IMAP → enable “IMAP/SMTP service” → add an authorization password.",
        "ja": "認証コードの取得：{} を開いてログイン → 設定 → POP3/SMTP/IMAP →「IMAP/SMTPサービス」を有効化 → 認証パスワードを追加。",
    },
    "mail_hint_gmail": {
        "zh": "Gmail 要先开启两步验证，再打开 {} 生成一个 16 位的应用专用密码填在这里（Google 账号的菜单里常常找不到这一项，点链接最快）。",
        "en": "Turn on 2-Step Verification first, then open {} to create a 16-character app password and enter it here (it's often hidden in the Google Account menus—the link is quickest).",
        "ja": "まず2段階認証を有効にし、{} を開いて16文字のアプリパスワードを作成して入力します（Googleアカウントのメニューでは見つけにくいので、リンクからが早いです）。",
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
    # 提醒（阶段 11.5）：到点时气泡 / 系统通知；错过的补一句；设置 → 提醒
    "reminder_title": {"zh": "芸芸提醒你", "en": "Reminder from Yunyun", "ja": "芸芸からのお知らせ"},
    "reminder_ring": {"zh": "⏰ 提醒：{}", "en": "⏰ Reminder: {}", "ja": "⏰ リマインダー：{}"},
    "reminder_missed": {"zh": "⏰ 刚才错过了一个提醒（{}）：{}", "en": "⏰ You missed a reminder ({}): {}",
                        "ja": "⏰ さっきのリマインダー（{}）：{}"},
    "page_reminders": {"zh": "提醒", "en": "Reminders", "ja": "リマインダー"},
    "reminders_intro": {
        "zh": "让芸芸提醒你：在助手模式里说\"25 分钟后叫我\"\"每天下午 3 点提醒我喝水\"。"
              "到点芸芸冒气泡、弹 Windows 通知（助手模式开着时还会说出来）；芸芸没开着时错过的，下次打开补一句。",
        "en": "Ask Yunyun in assistant mode: \"remind me in 25 minutes\", \"remind me to drink water every day at 3 pm\". "
              "At that time she shows a bubble and a Windows notification (and says it aloud if assistant mode is "
              "open); if she wasn't running, she tells you about missed ones next time.",
        "ja": "アシスタントモードで「25 分後に呼んで」「毎日午後 3 時に水を飲むよう知らせて」と頼んでください。"
              "時間になると吹き出しと Windows の通知で知らせます（アシスタントモードが開いていれば読み上げも）。"
              "芸芸が起動していなかったときの分は、次に起動したときに知らせます。",
    },
    "reminders_empty": {"zh": "还没有提醒。", "en": "No reminders yet.", "ja": "リマインダーはまだありません。"},
    "reminders_when": {"zh": "时间", "en": "When", "ja": "時間"},
    "reminders_repeat": {"zh": "重复", "en": "Repeat", "ja": "繰り返し"},
    "reminders_text": {"zh": "内容", "en": "What", "ja": "内容"},
    "reminders_repeat_": {"zh": "—", "en": "—", "ja": "—"},
    "reminders_repeat_daily": {"zh": "每天", "en": "Every day", "ja": "毎日"},
    "reminders_repeat_weekdays": {"zh": "工作日", "en": "Weekdays", "ja": "平日"},
    "reminders_repeat_weekly": {"zh": "每周", "en": "Every week", "ja": "毎週"},
    "weekday_names": {"zh": "周一,周二,周三,周四,周五,周六,周日", "en": "Mon,Tue,Wed,Thu,Fri,Sat,Sun",
                      "ja": "月,火,水,木,金,土,日"},
    "reminders_today": {"zh": "今天 {}", "en": "Today {}", "ja": "今日 {}"},
    "reminders_tomorrow": {"zh": "明天 {}", "en": "Tomorrow {}", "ja": "明日 {}"},
    "reminders_delete": {"zh": "删除", "en": "Delete", "ja": "削除"},
    "reminders_clear": {"zh": "全部清空", "en": "Clear all", "ja": "すべて削除"},
    "reminders_clear_confirm": {"zh": "确定要删掉全部 {} 个提醒吗？", "en": "Delete all {} reminders?",
                                "ja": "{} 件のリマインダーをすべて削除しますか？"},
    "menu_reminders": {"zh": "提醒（{}）…", "en": "Reminders ({})…", "ja": "リマインダー（{}）…"},
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
    "card_mailbox": {"zh": "收件邮箱", "en": "Received at", "ja": "受信アカウント"},
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
    # AI 页的"隐私"一块：哪些邮件不交给 AI（ai/privacy.py）
    "privacy_title": {"zh": "隐私：这些邮件不交给 AI", "en": "Privacy: keep these emails from AI", "ja": "プライバシー：これらのメールは AI に渡さない"},
    "privacy_accounts": {"zh": "整个邮箱：", "en": "Whole mailboxes:", "ja": "メールボックス全体："},
    "privacy_senders": {"zh": "发件人 / 域名：", "en": "Senders / domains:", "ja": "差出人 / ドメイン："},
    "privacy_placeholder": {"zh": "boss@company.com 或 @icbc.com.cn", "en": "boss@company.com or @mybank.com", "ja": "boss@company.com または @mybank.co.jp"},
    "privacy_add": {"zh": "添加", "en": "Add", "ja": "追加"},
    "privacy_bad_sender": {
        "zh": "请填邮箱地址（boss@company.com）或 @域名（@icbc.com.cn，这个域名发来的都算）。",
        "en": "Enter an email address (boss@company.com) or @domain (@mybank.com covers everything from that domain).",
        "ja": "メールアドレス（boss@company.com）か @ドメイン（@mybank.co.jp、そのドメインからのメールすべて）を入力してください。",
    },
    "privacy_explain": {
        "zh": "这些邮件只显示发件人和标题（带 🔒），一个字都不发给 AI，本地模型也不给。改了马上生效。",
        "en": "These emails show only the sender and subject (with 🔒); nothing is sent to any AI, local models included. Changes apply right away.",
        "ja": "これらのメールは差出人と件名だけを表示し（🔒 付き）、ローカルモデルを含めどの AI にも一切送りません。変更はすぐに反映されます。",
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
    "assistant_provider_deepseek": {"zh": "DeepSeek（深度求索）", "en": "DeepSeek", "ja": "DeepSeek"},
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
    "assistant_balance": {   # 能查余额的服务商（DeepSeek）：金额 + 官网链接
        "zh": '账户余额：{}（<a href="{}">去官网看明细</a>）',
        "en": 'Account balance: {} (<a href="{}">details</a>)',
        "ja": 'アカウント残高：{}（<a href="{}">公式サイトで詳細</a>）',
    },
    "assistant_balance_empty": {
        "zh": '账户余额：{}，不够用了，<a href="{}">充值</a>后才能用。',
        "en": 'Account balance: {}. That\'s not enough; <a href="{}">top up</a> before using it.',
        "ja": 'アカウント残高：{}。足りないため、<a href="{}">チャージ</a>してから使ってください。',
    },
    "assistant_conversation_budget": {"zh": "每段对话最多花", "en": "Max per chat", "ja": "1 つの会話の上限"},
    # 对话太长时自动整理（阶段 10）
    "assistant_compact": {"zh": "对话太长时自动整理", "en": "Tidy up long chats", "ja": "長い会話を自動で整理"},
    "assistant_compact_off": {"zh": "不整理", "en": "Never", "ja": "整理しない"},
    "assistant_compact_at": {"zh": "超过 {} token", "en": "Over {} tokens", "ja": "{} トークンを超えたら"},
    "assistant_compact_recommended": {"zh": "（推荐）", "en": " (recommended)", "ja": "（おすすめ）"},
    "assistant_compact_hint": {
        "zh": "整理：芸芸把前面的对话写成一段摘要，之后每一问只带着摘要，不再带着全部前文——"
              "聊久了也不容易变笨，也更省钱；个别细节可能会丢。卡片底部的\"对话 x/y\"是现在的长度。新对话开始生效。",
        "en": "Tidying up: Yunyun condenses the earlier chat into a summary, and later questions carry only the summary "
              "instead of the whole history — she stays sharp in long chats and it costs less, though some details "
              "may be lost. \"Chat x/y\" at the bottom of a card shows the current length. Applies from the next chat.",
        "ja": "整理：芸芸が前の会話を要約にまとめ、その後の質問では会話全体ではなく要約だけを送ります。"
              "長く話しても賢さを保ちやすく、料金も節約できます（細かい点が少し抜けることがあります）。"
              "カード下の「会話 x/y」が今の長さです。次の会話から有効です。",
    },
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
    "assistant_hint_deepseek": {
        "zh": "在 {} 申请 API Key（按用量付费，先充值）。这家不能搜索网页，但能读你给的网址。"
              "点「测试并保存」只检查 Key 和模型，不会花钱。",
        "en": "Get an API key at {} (pay-as-you-go; top up first). This provider can't search the web, "
              "but it can read pages you give it. \"Test and Save\" only checks the key and model; it costs nothing.",
        "ja": "{} で API キーを取得してください（従量課金。先にチャージが必要です）。ウェブ検索はできませんが、"
              "指定した URL は読めます。「テストして保存」はキーとモデルを確認するだけで、料金はかかりません。",
    },
    "assistant_privacy_note": {
        "zh": "助手模式里，你的提问和对话会发送给 {}；芸芸搜索、阅读的网页内容，以及选了工作文件夹时她读的文件、"
              "命令的结果也会经过它；芸芸看屏幕时，截图也会发过去（芸芸自己的窗口不截）。"
              "API Key 只保存在这台电脑的 Windows 凭据管理器里。",
        "en": "In assistant mode, your questions and the conversation are sent to {}; web pages Yunyun searches and reads, "
              "and — with a work folder — the files she reads and command output, also pass through it; when Yunyun "
              "looks at your screen, the screenshot is sent too (her own windows are left out). "
              "The API key is stored only in Windows Credential Manager on this PC.",
        "ja": "アシスタントモードでは、あなたの質問と会話が {} に送信されます。芸芸が検索・閲覧するウェブページの内容や、"
              "作業フォルダーを選んだ場合に読むファイル・コマンドの結果も経由します。芸芸が画面を見るときは、"
              "スクリーンショットも送られます（芸芸自身のウィンドウは写りません）。"
              "API キーはこの PC の Windows 資格情報マネージャーにのみ保存されます。",
    },
    "assistant_screenshot_ask": {"zh": "截图前先问我", "en": "Ask me before taking a screenshot",
                                 "ja": "スクリーンショットの前に確認する"},
    # 芸芸的性格（阶段 11）
    "assistant_user_name": {"zh": "怎么称呼你", "en": "What to call you", "ja": "あなたの呼び方"},
    "assistant_user_name_placeholder": {"zh": "空着就叫\"你\"，比如\"小明\"\"主人\"",
                                        "en": "Leave empty for \"you\", e.g. \"Alex\", \"Master\"",
                                        "ja": "空欄なら名前で呼びません。例：「たろう」「ご主人さま」"},
    "assistant_personality": {"zh": "芸芸的性格", "en": "Yunyun's personality", "ja": "芸芸の性格"},
    "assistant_personality_default": {"zh": "傲娇可爱（默认）", "en": "Tsundere and cute (default)",
                                      "ja": "ツンデレでかわいい（デフォルト）"},
    "assistant_personality_custom": {"zh": "自己设定", "en": "Write my own", "ja": "自分で設定"},
    "assistant_personality_extra_placeholder": {
        "zh": "补充几句（可不填），比如\"多用类比讲解\"",
        "en": "Add a few words (optional), e.g. \"Explain things with analogies\"",
        "ja": "補足（任意）。例：「たとえ話で説明して」",
    },
    "assistant_personality_custom_placeholder": {
        "zh": "写芸芸是什么性格、怎么说话，比如\"温柔的大姐姐，说话慢条斯理\"",
        "en": "Describe Yunyun's personality and way of talking, e.g. \"a gentle big sister who speaks calmly\"",
        "ja": "芸芸の性格や話し方を書いてください。例：「おっとりした優しいお姉さん」",
    },
    "assistant_personality_extra_hint": {
        "zh": "最多 {} 字，新开的对话生效。",
        "en": "Up to {} characters. Takes effect in new chats.",
        "ja": "{} 文字まで。新しい会話から反映されます。",
    },
    "assistant_personality_custom_hint": {
        "zh": "替换默认性格；\"不编造\"和能做什么的说明照旧保留。最多 {} 字，新开的对话生效。",
        "en": "Replaces the default personality; the rules (such as never making things up) stay. "
              "Up to {} characters. Takes effect in new chats.",
        "ja": "デフォルトの性格の代わりになります。「作り話をしない」などの決まりはそのまま残ります。"
              "{} 文字まで。新しい会話から反映されます。",
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
    # 对话太长时自动整理（阶段 10）：问的时候正在整理 → 进度行；卡片底行的对话长度、整理状态
    "assistant_compacting": {"zh": "芸芸在整理前面的对话……", "en": "Yunyun is tidying up the earlier chat…",
                             "ja": "芸芸が前の会話を整理しています…"},
    "assistant_context": {"zh": "对话 {}/{}", "en": "Chat {}/{}", "ja": "会話 {}/{}"},
    "assistant_context_plain": {"zh": "对话 {}", "en": "Chat {}", "ja": "会話 {}"},
    "assistant_compact_running": {"zh": "正在整理对话…", "en": "Tidying up…", "ja": "会話を整理中…"},
    "assistant_compact_done": {"zh": "前面的对话整理好了", "en": "Earlier chat tidied up", "ja": "前の会話を整理しました"},
    "assistant_tool_web_search": {"zh": "正在搜索网页……", "en": "Searching the web…", "ja": "ウェブを検索中…"},
    "assistant_tool_web_fetch": {"zh": "正在看网页……", "en": "Reading a web page…", "ja": "ウェブページを読んでいます…"},
    "assistant_tool_other": {"zh": "正在使用 {}……", "en": "Using {}…", "ja": "{} を使用中…"},
    "assistant_tool_read": {"zh": "正在读……", "en": "Reading…", "ja": "読んでいます…"},
    "assistant_tool_search_files": {"zh": "正在找文件……", "en": "Searching files…", "ja": "ファイルを検索中…"},
    "assistant_tool_edit": {"zh": "正在改……", "en": "Editing…", "ja": "編集中…"},
    "assistant_tool_write": {"zh": "正在写……", "en": "Writing…", "ja": "書いています…"},
    "assistant_tool_command": {"zh": "正在执行命令……", "en": "Running a command…", "ja": "コマンドを実行中…"},
    "assistant_tool_remember": {"zh": "正在记到记事本……", "en": "Noting it down…", "ja": "メモ帳に記録中…"},
    "assistant_tool_recall": {"zh": "正在翻记事本……", "en": "Checking the notebook…", "ja": "メモ帳を見ています…"},
    "assistant_tool_recall_chats": {"zh": "正在翻回忆……", "en": "Looking back at earlier chats…",
                                    "ja": "前の会話を思い出しています…"},
    "assistant_tool_set_reminder": {"zh": "正在设提醒……", "en": "Setting a reminder…", "ja": "リマインダーを設定しています…"},
    "assistant_tool_reminders": {"zh": "正在看提醒……", "en": "Checking reminders…", "ja": "リマインダーを確認しています…"},
    "assistant_tool_copy": {"zh": "正在放进剪贴板……", "en": "Copying to the clipboard…", "ja": "クリップボードに入れています…"},
    "assistant_tool_read_clipboard": {"zh": "正在看剪贴板……", "en": "Reading the clipboard…",
                                      "ja": "クリップボードを見ています…"},
    "assistant_tool_windows": {"zh": "正在整理窗口……", "en": "Handling windows…", "ja": "ウィンドウを操作しています…"},
    "assistant_tool_forget": {"zh": "正在从记事本里划掉……", "en": "Crossing it out of the notebook…",
                              "ja": "メモ帳から消しています…"},
    "assistant_tool_screenshot": {"zh": "正在看屏幕……", "en": "Looking at the screen…", "ja": "画面を見ています…"},
    "assistant_tool_find_app": {"zh": "正在找软件……", "en": "Looking for the app…", "ja": "ソフトを探しています…"},
    "assistant_tool_open_app": {"zh": "正在打开软件……", "en": "Opening the app…", "ja": "ソフトを開いています…"},
    "assistant_tool_browser_open": {"zh": "正在用浏览器打开……", "en": "Opening in the browser…",
                                    "ja": "ブラウザーで開いています…"},
    "assistant_tool_browser_read": {"zh": "正在看浏览器里的网页……", "en": "Reading the page in the browser…",
                                    "ja": "ブラウザーのページを読んでいます…"},
    "assistant_tool_browser_back": {"zh": "浏览器后退……", "en": "Going back in the browser…",
                                    "ja": "ブラウザーで戻っています…"},
    "assistant_tool_browser_click": {"zh": "正在网页上点击……", "en": "Clicking on the page…",
                                     "ja": "ページ上でクリックしています…"},
    "assistant_tool_browser_type": {"zh": "正在网页上输入……", "en": "Typing on the page…",
                                    "ja": "ページに入力しています…"},
    "assistant_tool_browser_select": {"zh": "正在网页上选择……", "en": "Choosing on the page…",
                                      "ja": "ページで選んでいます…"},
    "assistant_tool_browser_download": {"zh": "正在下载……", "en": "Downloading…", "ja": "ダウンロードしています…"},
    # 芸芸用你的鼠标时（11.6c）：角落小条；你动了鼠标、她交还时的气泡（自己设定性格时用 _plain）
    "assistant_mouse_notice": {"zh": "芸芸在用鼠标……动一下鼠标就还给你", "en": "Yunyun is using the mouse… move it to take it back",
                               "ja": "芸芸がマウスを使っています…動かせば返します"},
    "assistant_mouse_back": {"zh": "哼，鼠标还你啦～要继续就跟我说一声。",
                             "en": "Hmph, the mouse is yours again~ Tell me if you want me to continue.",
                             "ja": "ふん、マウスは返したよ～続けてほしかったら言ってね。"},
    "assistant_mouse_back_plain": {"zh": "鼠标还你啦～要继续就跟我说一声。",
                                   "en": "The mouse is yours again~ Tell me if you want me to continue.",
                                   "ja": "マウスは返したよ～続けてほしかったら言ってね。"},
    # 操作桌面软件（阶段 11.6b）
    "assistant_tool_ui_read": {"zh": "正在看软件窗口……", "en": "Reading the app window…",
                               "ja": "ソフトのウィンドウを読んでいます…"},
    "assistant_tool_ui_click": {"zh": "正在软件里按按钮……", "en": "Pressing a button in the app…",
                                "ja": "ソフトのボタンを押しています…"},
    "assistant_tool_ui_type": {"zh": "正在软件里输入……", "en": "Typing in the app…", "ja": "ソフトに入力しています…"},
    "assistant_tool_ui_key": {"zh": "正在软件里按键……", "en": "Pressing keys in the app…",
                              "ja": "ソフトでキーを押しています…"},
    "assistant_tool_power": {"zh": "正在操作电源……", "en": "Handling power…", "ja": "電源を操作しています…"},
    "assistant_tool_list_processes": {"zh": "正在看有哪些程序在运行……", "en": "Checking running programs…",
                                      "ja": "動いているプログラムを確認しています…"},
    "assistant_tool_end_process": {"zh": "正在结束程序……", "en": "Ending a program…", "ja": "プログラムを終了しています…"},
    "assistant_tool_ui_click_at": {"zh": "正在按截图上的位置点……", "en": "Clicking a spot from the screenshot…",
                                   "ja": "スクリーンショットの位置をクリックしています…"},
    "assistant_downloaded_said": {"zh": "下载好了：{}（在\"下载\"文件夹里）", "en": "Downloaded: {} (in your Downloads folder)",
                                  "ja": "ダウンロードしたよ：{}（「ダウンロード」フォルダーにあります）"},
    # 相处笔记（阶段 11.3）：回想以前的对话后，有新的开始生效（一天最多一次；自己设定性格时用 _plain）
    "assistant_rapport_learned": {"zh": "哼……我好像更了解你一点了。", "en": "Hmph... I think I know you a little better now.",
                                  "ja": "ふん……ちょっとだけ、あなたのことが分かってきた気がする。"},
    "assistant_rapport_learned_plain": {"zh": "我好像更了解你一点了。", "en": "I think I know you a little better now.",
                                        "ja": "あなたのこと、少し分かってきた気がする。"},
    # 打开软件（阶段 6）：打开后气泡说一声
    "assistant_open_app_said": {"zh": "帮你打开「{}」啦", "en": "Opened “{}” for you", "ja": "「{}」を開いたよ"},
    # 看屏幕（阶段 5）：截之前气泡说一声；操作记录里截的是哪里
    "assistant_screenshot_said": {"zh": "我看一下屏幕哦", "en": "Let me take a look at your screen",
                                  "ja": "ちょっと画面を見るね"},
    "assistant_screenshot_target_window": {"zh": "你刚才用的窗口", "en": "the window you were using",
                                           "ja": "直前に使っていたウィンドウ"},
    "assistant_screenshot_target_screen": {"zh": "整块屏幕", "en": "the whole screen", "ja": "画面全体"},
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
    "assistant_confirm_screenshot": {"zh": "芸芸想看一下你的屏幕", "en": "Yunyun wants to look at your screen",
                                     "ja": "芸芸があなたの画面を見ようとしています"},
    "assistant_confirm_screenshot_note": {
        "zh": "截图会发给你选的服务商，芸芸自己的窗口不截。",
        "en": "The screenshot is sent to your chosen provider; Yunyun's own windows are left out.",
        "ja": "スクリーンショットは選んだプロバイダーに送られます。芸芸自身のウィンドウは写りません。",
    },
    "assistant_confirm_open_app": {"zh": "芸芸想打开一个软件", "en": "Yunyun wants to open an app",
                                   "ja": "芸芸がソフトを開こうとしています"},
    "assistant_confirm_clipboard": {"zh": "芸芸想看看你的剪贴板", "en": "Yunyun wants to read your clipboard",
                                    "ja": "芸芸がクリップボードを見ようとしています"},
    "assistant_confirm_clipboard_note": {
        "zh": "剪贴板里的文字会发给你选的服务商。如果你刚复制过密码之类的，先别允许。",
        "en": "The text on your clipboard is sent to your chosen provider. If you just copied a password or the like, "
              "don't allow it.",
        "ja": "クリップボードの文字は選んだプロバイダーに送られます。パスワードなどをコピーしたばかりなら許可しないでください。",
    },
    "assistant_confirm_remember_clipboard": {"zh": "这次对话都允许看剪贴板", "en": "Allow clipboard for this chat",
                                             "ja": "この会話ではクリップボードを許可"},
    "assistant_confirm_open_app_note": {
        "zh": "开始菜单里的这个软件。芸芸只负责打开，不会操作软件里面。",
        "en": "This app from your Start menu. Yunyun only opens it and won't operate anything inside.",
        "ja": "スタートメニューにあるこのソフトです。芸芸は開くだけで、ソフトの中は操作しません。",
    },
    # 网页上点击、输入、选择（阶段 6.2b）
    "assistant_confirm_web_click": {"zh": "芸芸想在网页上点一下", "en": "Yunyun wants to click on a web page",
                                    "ja": "芸芸がページ上でクリックしようとしています"},
    "assistant_confirm_web_type": {"zh": "芸芸想在网页上输入", "en": "Yunyun wants to type on a web page",
                                   "ja": "芸芸がページに入力しようとしています"},
    "assistant_confirm_web_select": {"zh": "芸芸想在网页上选一项", "en": "Yunyun wants to choose an option on a web page",
                                     "ja": "芸芸がページで項目を選ぼうとしています"},
    "assistant_confirm_web_enter": {"zh": "输入后按回车", "en": "Then press Enter", "ja": "入力後に Enter を押す"},
    "assistant_confirm_web_download": {"zh": "芸芸想从网页下载一个文件", "en": "Yunyun wants to download a file",
                                       "ja": "芸芸がファイルをダウンロードしようとしています"},
    "assistant_confirm_download_to": {"zh": "存到：{}", "en": "Saved to: {}", "ja": "保存先：{}"},
    "assistant_confirm_note_web_download": {
        "zh": "下载每次都要问你。",
        "en": "You'll be asked before every download.",
        "ja": "ダウンロードは毎回確認します。",
    },
    "assistant_confirm_note_web_risky": {
        "zh": "看起来是提交、下单、付款、发送、删除或者下载这类做了就撤不回的操作，所以每次都要问你。",
        "en": "This looks like submitting, ordering, paying, sending, deleting or downloading — things that can't be "
              "undone — so you'll be asked every time.",
        "ja": "送信・注文・支払い・削除・ダウンロードなど、取り消せない操作のようなので、毎回確認します。",
    },
    "assistant_confirm_remember_web": {"zh": "这次对话在 {} 都允许", "en": "Allow on {} for this chat",
                                       "ja": "この会話では {} で許可"},
    # 软件里读窗口、点击、输入、按键（阶段 11.6b）
    "assistant_confirm_ui_read": {"zh": "芸芸想看一个软件窗口里的内容", "en": "Yunyun wants to read an app window",
                                  "ja": "芸芸がソフトのウィンドウを読もうとしています"},
    "assistant_confirm_ui_read_note": {
        "zh": "窗口里的按钮、文字会发给你选的服务商（和截图一样）。",
        "en": "The window's buttons and text are sent to your chosen provider (like a screenshot).",
        "ja": "ウィンドウのボタンや文字は選んだプロバイダーに送られます（スクリーンショットと同じです）。",
    },
    "assistant_confirm_ui_click": {"zh": "芸芸想在软件里按一下", "en": "Yunyun wants to press something in an app",
                                   "ja": "芸芸がソフトの中で押そうとしています"},
    "assistant_confirm_ui_type": {"zh": "芸芸想在软件里输入", "en": "Yunyun wants to type in an app",
                                  "ja": "芸芸がソフトに入力しようとしています"},
    "assistant_confirm_ui_key": {"zh": "芸芸想在软件里按键", "en": "Yunyun wants to press keys in an app",
                                 "ja": "芸芸がソフトでキーを押そうとしています"},
    "assistant_confirm_ui_keys": {"zh": "按键：{}", "en": "Keys: {}", "ja": "キー：{}"},
    "assistant_confirm_note_ui_risky": {
        "zh": "看起来是发送、删除、付款、关闭、回车这类做了就撤不回的操作，所以每次都要问你。",
        "en": "This looks like sending, deleting, paying, closing or pressing Enter — things that can't be undone — "
              "so you'll be asked every time.",
        "ja": "送信・削除・支払い・閉じる・Enter など、取り消せない操作のようなので、毎回確認します。",
    },
    # 电源和程序（11.6e）
    "assistant_confirm_power_shutdown": {"zh": "芸芸想关机", "en": "Yunyun wants to shut down the computer",
                                         "ja": "芸芸がパソコンをシャットダウンしようとしています"},
    "assistant_confirm_power_restart": {"zh": "芸芸想重启电脑", "en": "Yunyun wants to restart the computer",
                                        "ja": "芸芸がパソコンを再起動しようとしています"},
    "assistant_confirm_power_detail": {
        "zh": "允许以后过 60 秒才执行（Windows 会提示），这期间跟芸芸说\u201c取消关机\u201d就能撤回。",
        "en": "It happens 60 seconds after you allow it (Windows shows a notice); until then, tell Yunyun \u201ccancel\u201d "
              "to stop it.",
        "ja": "許可してから 60 秒後に実行します（Windows が通知します）。それまでに芸芸に\u300c取り消して\u300dと言えば止められます。",
    },
    "assistant_confirm_note_power": {
        "zh": "没保存的东西会丢，先保存好再允许。所以每次都要问你。",
        "en": "Unsaved work will be lost, so save first. That's why you're asked every time.",
        "ja": "保存していないものは失われるので、先に保存してください。そのため毎回確認します。",
    },
    "assistant_confirm_end_process": {"zh": "芸芸想结束一个程序", "en": "Yunyun wants to end a program",
                                      "ja": "芸芸がプログラムを終了しようとしています"},
    "assistant_confirm_note_end_process": {
        "zh": "直接结束：这个程序里没保存的东西会丢。所以每次都要问你。",
        "en": "It's force-quit: anything unsaved in it is lost. That's why you're asked every time.",
        "ja": "強制終了します：保存していないものは失われます。そのため毎回確認します。",
    },
    "assistant_confirm_note_ui_point": {
        "zh": "这个软件芸芸读不到里面，是按截图上的位置点的：看清楚红圈在哪再允许。",
        "en": "Yunyun can't see inside this app, so it clicks by position on the screenshot: check where the red circle "
              "is before allowing.",
        "ja": "このソフトは中を読めないので、スクリーンショットの位置でクリックします。赤い丸の場所を確かめてから許可してください。",
    },
    "assistant_confirm_remember_ui": {"zh": "这次对话在 {} 都允许", "en": "Allow in {} for this chat",
                                      "ja": "この会話では {} で許可"},
    "assistant_confirm_remember_ui_read": {"zh": "这次对话都允许看窗口", "en": "Allow reading windows for this chat",
                                           "ja": "この会話ではウィンドウの読み取りを許可"},
    "assistant_web_element": {"zh": "{}「{}」", "en": "{} “{}”", "ja": "{}「{}」"},
    "assistant_web_role_button": {"zh": "按钮", "en": "Button", "ja": "ボタン"},
    "assistant_web_role_link": {"zh": "链接", "en": "Link", "ja": "リンク"},
    "assistant_web_role_textbox": {"zh": "输入框", "en": "Text box", "ja": "入力欄"},
    "assistant_web_role_searchbox": {"zh": "搜索框", "en": "Search box", "ja": "検索欄"},
    "assistant_web_role_combobox": {"zh": "下拉框", "en": "Drop-down", "ja": "ドロップダウン"},
    "assistant_web_role_listbox": {"zh": "列表", "en": "List", "ja": "リスト"},
    "assistant_web_role_checkbox": {"zh": "复选框", "en": "Checkbox", "ja": "チェックボックス"},
    "assistant_web_role_radio": {"zh": "单选项", "en": "Radio button", "ja": "ラジオボタン"},
    "assistant_web_role_switch": {"zh": "开关", "en": "Switch", "ja": "スイッチ"},
    "assistant_web_role_tab": {"zh": "标签", "en": "Tab", "ja": "タブ"},
    "assistant_web_role_menuitem": {"zh": "菜单项", "en": "Menu item", "ja": "メニュー項目"},
    "assistant_web_role_option": {"zh": "选项", "en": "Option", "ja": "選択肢"},
    "assistant_web_role_img": {"zh": "图片", "en": "Image", "ja": "画像"},
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
    "assistant_confirm_remember_screenshot": {"zh": "这次对话截图都允许", "en": "Allow screenshots for this chat",
                                              "ja": "この会話ではスクリーンショットを許可"},
    "assistant_confirm_remember_open_app": {"zh": "这次对话都允许打开它", "en": "Allow opening it for this chat",
                                            "ja": "この会話ではこのソフトを開くのを許可"},
    "assistant_confirm_waiting_said": {
        "zh": "有件事要你确认：点我一下～",
        "en": "I need your OK on something — click me!",
        "ja": "確認してほしいことがあるよ：私をクリックしてね～",
    },
    "assistant_stopped": {"zh": "（已停止）", "en": "(Stopped)", "ja": "（停止しました）"},
    "assistant_done_said": {"zh": "做完啦", "en": "All done", "ja": "できたよ"},   # 输入框收起着时答完：气泡第一行
    # 芸芸被挡住时，任务栏上方的角落小条（pet/assistant_notice.py）
    "assistant_notice_confirm": {"zh": "{}：{}", "en": "{}: {}", "ja": "{}：{}"},   # 确认卡片的标题：目标
    "assistant_notice_done": {"zh": "芸芸：{}", "en": "Yunyun: {}", "ja": "芸芸：{}"},
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
    "assistant_card_remembered": {"zh": "记了 {} 条", "en": "{} noted", "ja": "{} 件メモ"},
    "assistant_card_images": {"zh": "带 {} 张图", "en": "{} image(s)", "ja": "画像 {} 枚"},
    # 输入框里粘贴 / 拖进来的图片（阶段 5.5、5.6）
    "assistant_image_question": {"zh": "看看这张图", "en": "Take a look at this image", "ja": "この画像を見て"},
    "assistant_images_unsupported": {
        "zh": "现在用的大脑看不了图片：设置 → 助手换一个能看图的服务商，或者把要问的内容打成文字。",
        "en": "The current model can't read images: choose a provider that can in Settings → Assistant, "
              "or type the question as text.",
        "ja": "今のモデルは画像を読めません。設定 → アシスタントで画像に対応したプロバイダーを選ぶか、文字で質問してください。",
    },
    "assistant_images_too_many": {"zh": "一次最多带 {} 张图片", "en": "Up to {} images at a time",
                                  "ja": "画像は一度に {} 枚までです"},
    "assistant_card_undone": {"zh": "已撤销", "en": "Undone", "ja": "取り消し済み"},
    # 历史对话（设置 → 助手）
    "assistant_history_note": {
        "zh": "问答卡片保留 {} 天，只存在这台电脑上（里面有完整的对话内容）。",
        "en": "Chat cards are kept for {} days, only on this PC (they contain the full conversations).",
        "ja": "やり取りのカードは {} 日間、この PC にだけ保存されます（会話の全文を含みます）。",
    },
    "assistant_history_clear": {"zh": "清空历史对话", "en": "Clear chat history", "ja": "履歴を消去"},
    "assistant_history_confirm": {
        "zh": "要清空助手的历史对话吗？\n问答卡片、Claude Code 的对话记录、撤销用的文件备份、芸芸的回忆（每段对话的摘要）"
              "都会删掉，清空后就不能撤销以前的改动了。",
        "en": "Clear the assistant's chat history?\nChat cards, Claude Code's conversation records, the file backups "
              "used for undo and Yunyun's recollections (chat summaries) will be deleted; earlier changes can no longer "
              "be undone.",
        "ja": "アシスタントの履歴を消去しますか？\nやり取りのカード、Claude Code の会話記録、取り消し用のファイルのバックアップ、"
              "芸芸の思い出（会話の要約）が削除され、以前の変更は取り消せなくなります。",
    },
    "assistant_history_cleared": {"zh": "历史对话已清空。", "en": "Chat history cleared.", "ja": "履歴を消去しました。"},
    # 历史对话面板
    "menu_assistant_history": {"zh": "历史对话…", "en": "Chat history…", "ja": "会話の履歴…"},
    "assistant_history_button": {"zh": "历史", "en": "History", "ja": "履歴"},
    "assistant_history_title": {"zh": "历史对话（保留 {} 天）", "en": "Chat history (kept {} days)",
                                "ja": "会話の履歴（{} 日間保存）"},
    "assistant_history_hint": {"zh": "点一段看内容，可以接着聊", "en": "Click one to read it and continue it",
                               "ja": "クリックで内容を見て、続きができます"},
    "assistant_history_turns": {"zh": "{} 问", "en": "{} Q", "ja": "{} 問"},
    "assistant_history_chat_only": {"zh": "只聊天", "en": "Chat only", "ja": "会話のみ"},
    "assistant_history_back": {"zh": "← 返回", "en": "← Back", "ja": "← 戻る"},
    "assistant_history_continue": {"zh": "接着聊", "en": "Continue", "ja": "続きから"},
    "assistant_history_empty": {
        "zh": "还没有以前的对话（只保留最近 {} 天）。",
        "en": "No earlier chats yet (only the last {} days are kept).",
        "ja": "以前の会話はまだありません（直近 {} 日分だけ保存）。",
    },
    "assistant_history_recovered": {
        "zh": "这段有些是从 Claude Code 的记录里找回的：没有每一问的花费，也不知道当时哪些操作是你允许的。",
        "en": "Part of this was recovered from Claude Code's records: no per-question cost, "
              "and it's unknown which actions you approved.",
        "ja": "一部は Claude Code の記録から復元したものです：質問ごとの費用や、どの操作を許可したかは分かりません。",
    },
    "assistant_continue_said": {"zh": "好，接着 {} 的对话～", "en": "OK, picking up the chat from {}!",
                                "ja": "よし、{} の会話の続きから～"},
    "assistant_continue_folder_missing": {
        "zh": "原来的文件夹「{}」找不到了，先在现在的文件夹里接着聊。",
        "en": "The original folder \"{}\" is gone, so we'll continue in the current folder.",
        "ja": "元のフォルダー「{}」が見つからないので、今のフォルダーで続けます。",
    },
    "assistant_answer_confirm_first": {"zh": "先回答一下确认哦～", "en": "Please answer the confirmation first!",
                                       "ja": "先に確認に答えてね～"},
    # 操作记录（阅读框里"芸芸做了几件事"）
    "assistant_actions_toggle": {"zh": "芸芸做了 {} 件事", "en": "Yunyun did {} thing(s)", "ja": "芸芸がしたこと（{} 件）"},
    "assistant_action_read": {"zh": "读", "en": "Read", "ja": "読む"},
    "assistant_action_search": {"zh": "找文件", "en": "Search", "ja": "検索"},
    "assistant_action_edit": {"zh": "改", "en": "Edit", "ja": "編集"},
    "assistant_action_write": {"zh": "写", "en": "Write", "ja": "書く"},
    "assistant_action_command": {"zh": "执行", "en": "Run", "ja": "実行"},
    "assistant_action_web_search": {"zh": "搜网页", "en": "Web search", "ja": "ウェブ検索"},
    "assistant_action_web_fetch": {"zh": "看网页", "en": "Read page", "ja": "ページを読む"},
    "assistant_action_remember": {"zh": "记一条", "en": "Note", "ja": "メモ"},
    "assistant_action_recall": {"zh": "翻记事本", "en": "Check notes", "ja": "メモを見る"},
    "assistant_action_recall_chats": {"zh": "翻回忆", "en": "Recall chats", "ja": "前の会話を見る"},
    "assistant_action_set_reminder": {"zh": "设提醒", "en": "Set reminder", "ja": "リマインダー設定"},
    "assistant_action_reminders": {"zh": "看提醒", "en": "List reminders", "ja": "リマインダー確認"},
    "assistant_action_cancel_reminder": {"zh": "取消提醒", "en": "Cancel reminder", "ja": "リマインダー取消"},
    "assistant_action_copy": {"zh": "放进剪贴板", "en": "Copy", "ja": "コピー"},
    "assistant_action_read_clipboard": {"zh": "看剪贴板", "en": "Read clipboard", "ja": "クリップボードを見る"},
    "assistant_action_list_windows": {"zh": "看开着的窗口", "en": "List windows", "ja": "ウィンドウ一覧"},
    "assistant_action_window": {"zh": "切换窗口", "en": "Window", "ja": "ウィンドウ操作"},
    "assistant_action_forget": {"zh": "划掉一条", "en": "Remove note", "ja": "メモを消す"},
    "assistant_action_screenshot": {"zh": "看屏幕", "en": "Screenshot", "ja": "画面を見る"},
    "assistant_action_find_app": {"zh": "找软件", "en": "Find app", "ja": "ソフトを探す"},
    "assistant_action_open_app": {"zh": "打开软件", "en": "Open app", "ja": "ソフトを開く"},
    "assistant_action_browser_open": {"zh": "浏览器打开", "en": "Browser: open", "ja": "ブラウザーで開く"},
    "assistant_action_browser_read": {"zh": "浏览器读页", "en": "Browser: read", "ja": "ブラウザーで読む"},
    "assistant_action_browser_back": {"zh": "浏览器后退", "en": "Browser: back", "ja": "ブラウザーで戻る"},
    "assistant_action_browser_click": {"zh": "网页点击", "en": "Web: click", "ja": "ページでクリック"},
    "assistant_action_browser_type": {"zh": "网页输入", "en": "Web: type", "ja": "ページに入力"},
    "assistant_action_browser_select": {"zh": "网页选择", "en": "Web: choose", "ja": "ページで選択"},
    "assistant_action_browser_download": {"zh": "网页下载", "en": "Web: download", "ja": "ダウンロード"},
    "assistant_action_ui_read": {"zh": "看软件窗口", "en": "App: read", "ja": "ソフトを読む"},
    "assistant_action_ui_click": {"zh": "软件里按一下", "en": "App: press", "ja": "ソフトで押す"},
    "assistant_action_ui_type": {"zh": "软件里输入", "en": "App: type", "ja": "ソフトに入力"},
    "assistant_action_ui_key": {"zh": "软件里按键", "en": "App: keys", "ja": "ソフトでキー"},
    "assistant_action_power": {"zh": "电源", "en": "Power", "ja": "電源"},
    "assistant_action_list_processes": {"zh": "列出程序", "en": "List programs", "ja": "プログラム一覧"},
    "assistant_action_end_process": {"zh": "结束程序", "en": "End program", "ja": "プログラム終了"},
    "assistant_action_ui_click_at": {"zh": "按位置点", "en": "App: click spot", "ja": "位置をクリック"},
    "assistant_action_failed": {"zh": "（没成功）", "en": " (failed)", "ja": "（失敗）"},
    "assistant_decision_auto": {"zh": "不用问", "en": "No need to ask", "ja": "確認不要"},
    "assistant_decision_granted": {"zh": "之前允许过同类", "en": "Allowed earlier", "ja": "以前に許可済み"},
    "assistant_decision_allowed": {"zh": "你允许了", "en": "You allowed", "ja": "許可しました"},
    "assistant_decision_remembered": {"zh": "你允许了，同类不再问", "en": "Allowed, no more asks",
                                      "ja": "許可（同種は確認なし）"},
    "assistant_decision_denied": {"zh": "你拒绝了", "en": "You denied", "ja": "拒否しました"},
    "assistant_decision_blocked": {"zh": "不允许", "en": "Not allowed", "ja": "許可されていません"},
    "assistant_decision_stopped": {"zh": "停下了，没做", "en": "Stopped, not done", "ja": "停止したので未実行"},
    "assistant_decision_unknown": {"zh": "（当时没记）", "en": "(not recorded)", "ja": "（記録なし）"},
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
    "assistant_reader_read": {"zh": "🔊 读一遍", "en": "🔊 Read aloud", "ja": "🔊 読み上げる"},
    "menu_assistant_read_again": {"zh": "再读一遍刚才的回答", "en": "Read the last answer again", "ja": "さっきの回答をもう一度読む"},
    # "这句不是跟你说的"（7.9 ⑤）：语音误收的那一问撤回，大脑就像没听过
    "assistant_reader_withdraw": {"zh": "🙈 不是跟你说的", "en": "🙈 Wasn't talking to you",
                                  "ja": "🙈 あなたに言ったんじゃない"},
    "menu_assistant_withdraw": {"zh": "忽略刚才那句（不是跟她说的）", "en": "Ignore that last line (wasn't for her)",
                                "ja": "さっきの言葉を取り消す（芸芸に言ったんじゃない）"},
    "assistant_withdrawn_said": {"zh": "好，当我没听见～", "en": "Okay, I didn't hear that~", "ja": "うん、聞かなかったことにするね～"},
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

    # ---- 语音（阶段 7：voice/、设置 → 语音 pet/voice_page.py）----
    "voice_intro": {
        "zh": "在助手模式里，可以按住快捷键直接对芸芸说话，芸芸也可以出声回答你。",
        "en": "In assistant mode you can hold a hotkey and just talk to Yunyun, and Yunyun can read replies aloud.",
        "ja": "アシスタントモードでは、ホットキーを押しながら芸芸に話しかけられます。芸芸も声で返事できます。",
    },
    "voice_hotkey_label": {"zh": "说话快捷键：", "en": "Push-to-talk key:", "ja": "話すキー："},
    "voice_hotkey_clear": {"zh": "清除", "en": "Clear", "ja": "クリア"},
    "voice_hotkey_none": {
        "zh": "还没设：点左边的框，按下想用的组合键（比如 Ctrl+Shift+Space 或 F9）",
        "en": "Not set: click the box on the left and press the keys you want (e.g. Ctrl+Shift+Space or F9)",
        "ja": "未設定：左の欄をクリックして、使いたいキーを押してください（例：Ctrl+Shift+Space や F9）",
    },
    "voice_hotkey_ok": {"zh": "✓ 按住 {} 说话，松开就发送", "en": "✓ Hold {} to talk, release to send",
                        "ja": "✓ {} を押しながら話して、離すと送信"},
    "voice_hotkey_ready": {"zh": "✓ 可以用：打开助手模式后，按住 {} 说话", "en": "✓ Ready: open assistant mode, then hold {} to talk",
                           "ja": "✓ 使えます：アシスタントモードを開いてから {} を押しながら話してね"},
    "voice_hotkey_quiet": {"zh": "现在是免打扰时段，{} 暂时不管用", "en": "It's quiet hours now, so {} is off for the moment",
                           "ja": "いまはおやすみ時間なので、{} は一時的に使えません"},
    "voice_hotkey_taken": {"zh": "{} 被别的程序占着了，换一个吧", "en": "{} is already used by another app — pick another",
                           "ja": "{} はほかのアプリが使っています。別のキーにしてね"},
    "voice_hotkey_invalid": {
        "zh": "这个键不能用：单独的字母、数字、空格要配上 Ctrl、Alt 或 Win（F1～F24 可以单独用）",
        "en": "That key can't be used: letters, digits and Space need Ctrl, Alt or Win (F1–F24 work alone)",
        "ja": "このキーは使えません：文字・数字・スペースには Ctrl、Alt か Win を付けてください（F1～F24 は単独で OK）",
    },
    "voice_hotkey_note": {
        "zh": "快捷键只在助手模式开着时有用，免打扰时段里不管用；那时候别的程序收不到这个组合键，选一个平时不用的。",
        "en": "The hotkey only works while assistant mode is open, and not during quiet hours. While it works, other apps "
              "don't receive that key combination, so pick one you don't normally use.",
        "ja": "ホットキーはアシスタントモードを開いているときだけ有効で、おやすみ時間は無効です。有効なあいだは"
              "ほかのアプリにそのキーが届かないので、普段使わない組み合わせにしてね。",
    },
    "voice_model_label": {"zh": "识别模型：", "en": "Speech model:", "ja": "音声認識モデル："},
    "voice_model_missing": {"zh": "还没下载（约 {} MB，只下一次）", "en": "Not downloaded (about {} MB, one time)",
                            "ja": "未ダウンロード（約 {} MB、1 回だけ）"},
    "voice_model_ready": {"zh": "✓ 已下载", "en": "✓ Downloaded", "ja": "✓ ダウンロード済み"},
    "voice_model_progress": {"zh": "正在下载… {} / {} MB", "en": "Downloading… {} / {} MB", "ja": "ダウンロード中… {} / {} MB"},
    "voice_model_download": {"zh": "下载", "en": "Download", "ja": "ダウンロード"},
    "voice_model_cancel": {"zh": "取消", "en": "Cancel", "ja": "キャンセル"},
    "voice_model_remove": {"zh": "删除", "en": "Delete", "ja": "削除"},
    "voice_model_done": {"zh": "✓ 下载好了，可以按住快捷键说话了", "en": "✓ Done — you can hold the hotkey and talk now",
                         "ja": "✓ ダウンロード完了。ホットキーを押しながら話せます"},
    "voice_model_failed_network": {
        "zh": "下载失败：连不上（Hugging Face 和国内镜像都试过了），过一会儿再试试",
        "en": "Download failed: couldn't connect (tried Hugging Face and a mirror). Try again later.",
        "ja": "ダウンロード失敗：つながりませんでした（Hugging Face とミラーの両方を試しました）。少ししてからもう一度どうぞ",
    },
    "voice_model_failed_corrupt": {"zh": "下载的文件不对，已经删掉了，再试一次", "en": "The downloaded file was wrong and has been removed — try again",
                                   "ja": "ダウンロードしたファイルが正しくないので削除しました。もう一度どうぞ"},
    "voice_model_failed_disk": {"zh": "写不进硬盘（空间不够？）", "en": "Couldn't write to disk (out of space?)",
                                "ja": "ディスクに書き込めません（空き容量は？）"},
    "voice_model_failed_cancelled": {"zh": "已取消", "en": "Cancelled", "ja": "キャンセルしました"},
    "voice_listening": {"zh": "在听…松开就发送", "en": "Listening… release to send", "ja": "聞いてるよ…離すと送信"},
    "voice_recognizing": {"zh": "听到了，我看看你说的是…", "en": "Got it, working out what you said…", "ja": "聞こえたよ、いま文字にしてる…"},
    "voice_not_heard": {"zh": "没听清，再说一次？", "en": "I didn't catch that — try again?", "ja": "聞き取れなかった、もう一回？"},
    "voice_hold_hint": {"zh": "要按住快捷键说话，说完再松开哦", "en": "Hold the hotkey while you talk, then let go",
                        "ja": "ホットキーを押したまま話して、終わったら離してね"},
    "voice_busy": {"zh": "我还在忙这个，做完再听你说（急的话先点停止）", "en": "I'm still on this one — I'll listen when I'm done (or press Stop)",
                   "ja": "いまこれをやってるから、終わったら聞くね（急ぎなら停止を押して）"},
    "voice_need_model": {
        "zh": "要先下载语音识别模型（约 240 MB），我把设置打开了",
        "en": "The speech model needs to be downloaded first (about 240 MB) — I've opened the settings",
        "ja": "まず音声認識モデル（約 240 MB）をダウンロードしてね。設定を開いたよ",
    },
    "voice_error_mic": {
        "zh": "打不开麦克风：看看插好没有，或者在 Windows 设置 → 隐私和安全性 → 麦克风 里允许桌面应用使用",
        "en": "Can't open the microphone: check it's plugged in, or allow desktop apps in Windows Settings → Privacy & security → Microphone",
        "ja": "マイクを開けません：つながっているか確認するか、Windows の設定 → プライバシーとセキュリティ → マイク でデスクトップアプリを許可してね",
    },
    "voice_error_listen": {"zh": "这句没识别出来，再说一次？", "en": "Couldn't make out that one — try again?",
                           "ja": "うまく認識できなかった、もう一回？"},
    "voice_watch": {"zh": "始终在听：叫一声\"芸芸\"就能说话，不用按键", "en": "Always listening: just say \"Yunyun\", no key needed",
                    "ja": "いつも聞く：「芸芸」と呼べば話せる（キー不要）"},
    "voice_watch_note": {
        "zh": "始终在听：只在助手模式开着时听；全屏时、免打扰时段不听。开头没叫\"芸芸\"的话在本机识别后马上扔掉，"
              "不会发出去。开着时芸芸左上角有个绿色的小麦克风。用语音问她时，她说完后几秒内接着说不用再叫名字"
              "（小麦克风外面一圈绿条倒数，只说\"嗯、哦\"这类不算）。她说话时叫一声\"芸芸\"或者说\"别说了\"就能打断她"
              "（戴耳机最灵；外放时她自己的声音也进麦克风，要说得比她大声一点）。",
        "en": "Always listening: only while assistant mode is open; not in full screen or during quiet hours. Anything that "
              "doesn't start with \"Yunyun\" is recognized on this computer and thrown away at once — never sent. While it's "
              "on, a small green microphone sits at Yunyun's top-left. After she answers a spoken question, you can go on for "
              "a few seconds without saying her name (a green ring counts down around the microphone; a lone \"um\" or \"oh\" "
              "doesn't count). While she's talking, say \"Yunyun\" or \"stop\" to cut her off (works best with headphones; "
              "through speakers her own voice reaches the mic too, so speak up).",
        "ja": "いつも聞く：アシスタントモードを開いているときだけ。全画面のとき・おやすみ時間は聞きません。"
              "「芸芸」で始まらない言葉はこのパソコンで認識してすぐ捨て、送りません。オンのあいだは芸芸の左上に緑の小さなマイクが出ます。"
              "声で聞いたときは、答えたあと数秒間は名前を呼ばずに続けて話せます（マイクのまわりの緑の輪がカウントダウン。"
              "「えー」「うん」だけは数えません）。芸芸が話しているときに「芸芸」と呼ぶか「やめて」と言えば止められます"
              "（ヘッドホンだと確実。スピーカーだと芸芸の声もマイクに入るので、少し大きめの声で）。",
    },
    "voice_follow_label": {"zh": "接着说：", "en": "Follow-up:", "ja": "続けて話す："},
    "voice_follow_choice": {"zh": "她说完 {} 秒内不用再叫\"芸芸\"", "en": "No need to say \"Yunyun\" for {} s after she answers",
                            "ja": "答えたあと {} 秒間は「芸芸」と呼ばなくていい"},
    "menu_assistant_watch": {"zh": "始终在听（叫\"芸芸\"）", "en": "Always listening (say \"Yunyun\")",
                             "ja": "いつも聞く（「芸芸」と呼ぶ）"},
    "voice_called": {"zh": "嗯？我在听，说吧", "en": "Yes? I'm listening.", "ja": "うん？聞いてるよ"},
    "voice_hushed": {"zh": "好，不说了", "en": "OK, I'll stop.", "ja": "わかった、やめるね"},
    "voice_watch_on_said": {"zh": "开始听啦：叫一声\"芸芸\"就行", "en": "I'm listening now — just say \"Yunyun\"",
                            "ja": "聞いてるね：「芸芸」って呼んでね"},
    "voice_watch_off_said": {"zh": "好，不一直听了", "en": "OK, I'll stop listening", "ja": "わかった、聞くのをやめるね"},
    "voice_watch_failed": {"zh": "始终在听出了问题，先停下了；右键菜单里可以再打开",
                           "en": "Always listening ran into a problem and stopped; you can turn it back on from the right-click menu",
                           "ja": "「いつも聞く」で問題が起きたので止めました。右クリックメニューからまたオンにできます"},
    "voice_hotkey_taken_said": {
        "zh": "说话快捷键 {} 被别的程序占着，去 设置 → 语音 换一个吧",
        "en": "The push-to-talk key {} is used by another app — change it in Settings → Voice",
        "ja": "話すキー {} はほかのアプリが使っています。設定 → 音声 で変えてね",
    },
    "voice_reply_label": {"zh": "芸芸出声回答：", "en": "Read replies aloud:", "ja": "声で返事："},
    "voice_reply_voice": {"zh": "你用语音问时（推荐）", "en": "When you ask by voice (recommended)",
                          "ja": "声で聞いたとき（おすすめ）"},
    "voice_reply_always": {"zh": "每次回答都说", "en": "Every reply", "ja": "毎回"},
    "voice_reply_never": {"zh": "不说", "en": "Never", "ja": "話さない"},
    "voice_preview": {"zh": "▶ 试听", "en": "▶ Preview", "ja": "▶ 試聴"},
    "voice_preview_text": {   # 试听时说的话（界面语言的那句；声音也跟着界面语言）
        "zh": "你好，我是芸芸。以后我就用这个声音回答你啦。",
        "en": "Hi, I'm Yunyun. This is how I'll sound when I answer you.",
        "ja": "こんにちは、芸芸だよ。これからこの声で答えるね。",
    },
    "voice_privacy_note": {
        "zh": "说话用的是微软的在线语音（免费）：要读出来的回答会发给微软合成声音；连不上时这一句改用 Windows 自带的声音"
              "（不联网，机器味重一点）。只读正文：代码、表格、网址不读，文件路径只读文件名。"
              "你说的话只在这台电脑里识别成文字，录音不上传、不保存。",
        "en": "Speech uses Microsoft's free online voices: the reply text being read is sent to Microsoft to make the "
              "sound. If that fails, Yunyun uses the voice built into Windows for that sentence (offline, a bit robotic). "
              "Only the prose is read: code, tables and links are skipped, and file paths become just the file name. "
              "What you say is turned into text on this computer only; recordings are never uploaded or saved.",
        "ja": "声には Microsoft の無料オンライン音声を使います：読み上げる返事の文字が Microsoft に送られて音声になります。"
              "つながらないときはその文だけ Windows 標準の声（オフライン、少し機械的）を使います。"
              "本文だけ読みます：コード・表・URL は読まず、ファイルパスはファイル名だけ読みます。"
              "あなたの声はこのパソコンの中だけで文字にし、録音はアップロードも保存もしません。",
    },
    "voice_code_note": {"zh": "代码在卡片里。", "en": "The code is in the card.", "ja": "コードはカードにあるよ。"},
    "voice_error_audio": {
        "zh": "芸芸放不出声音：没找到扬声器，或者被别的程序占着。",
        "en": "Yunyun can't play sound: no speaker was found, or another app is using it.",
        "ja": "音を出せません：スピーカーが見つからないか、ほかのアプリが使っています。",
    },
    "voice_error_preview": {"zh": "这次没合成出声音，过一会儿再试试。", "en": "Couldn't make the sound this time — try again in a moment.",
                            "ja": "今回は音声を作れませんでした。少し待ってからもう一度どうぞ。"},
}

_current = DEFAULT_LANGUAGE  # 模块级变量：整个程序共享"当前语言"
_qt_translator = None        # 现在装着的 Qt 翻译（切换语言时先卸掉）


def load_language():
    """启动时调用：从 QSettings 读取上次选择的语言。"""
    global _current  # 要在函数里修改模块级变量，必须先声明 global
    value = QSettings().value(_SETTINGS_KEY, DEFAULT_LANGUAGE)
    _current = value if value in LANGUAGES else DEFAULT_LANGUAGE
    _apply_qt_translation()


def qt_translation_dir():
    """Qt 翻译文件所在的文件夹（开发版在 PySide6 包里，打包后在 _internal\\PySide6 里，都是同一个相对位置）。"""
    return Path(PySide6.__file__).parent / "translations"


def _apply_qt_translation():
    """让 Qt 自带的文字跟着当前语言。没有 QApplication 时（比如只用到文字的小进程）什么都不做。"""
    global _qt_translator
    app = QCoreApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    name = QT_TRANSLATIONS.get(_current)
    if name is None:
        return
    translator = QTranslator(app)
    if translator.load(name, str(qt_translation_dir())):
        app.installTranslator(translator)
        _qt_translator = translator
    else:   # 找不到文件也不影响使用，只是按钮还是英文
        log.warning("没找到 Qt 的翻译文件 %s（在 %s）", name, qt_translation_dir())


def current_language():
    return _current


def set_language(code):
    """切换语言，并保存下来。"""
    if code not in LANGUAGES:
        raise ValueError(f"不支持的语言：{code}")
    global _current
    _current = code
    QSettings().setValue(_SETTINGS_KEY, code)
    _apply_qt_translation()


def tr(key, *args):
    """返回 key 对应的、当前语言的文字（tr 是 translate 的缩写）。

    文字里有 {} 占位符时，按顺序填入 args，例如 tr("mail_confirm_remove", "a@qq.com")。
    """
    text = TEXTS[key][_current]
    return text.format(*args) if args else text
