"""芸芸的程序入口：创建各个模块，并用信号把它们连起来。

运行方式（在项目根目录）：
    .\\.venv\\Scripts\\python -m yunyun.main
"""
import logging
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from yunyun import ai, config, i18n, logs, single_instance
from yunyun.activity.keyboard import KeyboardMonitor
from yunyun.config import ConfigError
from yunyun.mail import accounts
from yunyun.mail.imap_client import MailError
from yunyun.mail.service import MailService
from yunyun.notify.history import HistoryEntry, MailHistory, default_path as history_path
from yunyun.notify.update import UpdateChecker
from yunyun.notify.quiet import QuietHours
from yunyun.pet.card_deck import MailCard, mailbox_label
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import StateMachine
from yunyun.pet.tray import PetTray
from yunyun.pet.window import PetWindow


def main():
    app = QApplication(sys.argv)  # 每个 Qt 程序有且只有一个 QApplication
    app.setOrganizationName("Yunyun")  # QSettings 用这两个名字决定设置存在哪里
    app.setApplicationName("Yunyun")
    logs.setup_logging()               # 出错信息写进日志文件（要在设置好程序名之后，才知道写到哪里）

    # 同时只运行一个芸芸：已经在运行了，就把它叫出来，自己退出
    if single_instance.notify_running_instance():
        logging.getLogger(__name__).info("芸芸已经在运行，叫它出来后本次启动退出")
        return 0
    guard = single_instance.InstanceGuard(parent=app)

    if config.migrate_legacy_config():  # 旧版本的配置在项目根目录：复制到 %APPDATA%（只做一次）
        logging.getLogger(__name__).info("已把旧的 config.yaml 复制到 %s", config.CONFIG_PATH.parent)
    i18n.load_language()               # 恢复上次选择的语言

    # 必须在 QApplication 之后（QPixmap 需要它）；加载时就缩小到显示需要的大小，省内存
    sprites = load_sprites(max_size=PetWindow.sprite_pixels(PetWindow.saved_scale()))   # 按用户调的大小载入
    machine = StateMachine()
    pet = PetWindow(sprites)
    keyboard = KeyboardMonitor()

    def on_second_launch():       # 用户又启动了一次：把芸芸叫出来，说一声
        pet.bring_to_front()
        pet.say(i18n.tr("already_running"))

    guard.show_requested.connect(on_second_launch)

    # 用信号把模块连起来：各模块之间互不 import，以后换界面层时只换 PetWindow
    keyboard.key_pressed.connect(machine.on_key)            # 敲键盘 → 状态机（参数：是不是按住不放的自动重复）
    keyboard.key_released.connect(machine.on_key_release)   # 松开键 → 状态机
    machine.state_changed.connect(pet.set_state)            # 状态变化 → 换画面
    machine.typed.connect(pet.tap)                          # 工作中按下一个键 → 芸芸的手落下
    machine.released.connect(pet.release)                   # 松开 → 芸芸抬手（按住不放就一直落着）
    pet.speaking_changed.connect(machine.set_talking)       # 头顶有气泡 → 说话的样子（气泡都消失了再回去）

    # ---- 邮件：后台定时检查 → 小红点；点小红点 → 读邮件 → AI 写一句提醒 ----
    # describe 在后台线程里运行（要联网）；语言在调用时读取，切换语言后立刻生效
    mail = MailService(process_message=lambda address, message: ai.describe(message, i18n.current_language(), address))
    history = MailHistory(history_path())   # 展示过的卡片存 7 天，可以找回
    pet.history = history

    # ---- 免打扰：时段内芸芸睡觉，新邮件只安静地计数 ----
    quiet = QuietHours()
    pet.quiet_hours = quiet
    machine.set_quiet(quiet.active)             # 启动时就在免打扰时段里：直接睡（不说"晚安"）

    def on_quiet_changed(active):
        machine.set_quiet(active)
        pet.say(i18n.tr("quiet_start" if active else "quiet_end"))

    quiet.changed.connect(on_quiet_changed)

    # ---- 新版本提醒：每天最多问一次 GitHub 最新版本号，有新的就说一次、右键菜单里放"下载新版本" ----
    updates = UpdateChecker(app)
    pet.updates = updates
    updates.found.connect(pet.on_new_version)

    connect_mail(mail, pet, machine, history, quiet)
    connect_assistant(pet, machine)
    watch = connect_watch(app, pet)

    app.aboutToQuit.connect(lambda: pet.close_assistant(wait=True))   # 退出前关掉助手子进程（等它真的退出）
    app.aboutToQuit.connect(keyboard.stop)       # 退出前停止键盘监听
    app.aboutToQuit.connect(ai.shutdown)         # 退出前关掉芸芸按需启动的 Ollama
    app.aboutToQuit.connect(mail.stop)           # 退出前断开实时接收邮件的连接
    app.aboutToQuit.connect(pet.save_position)   # 退出前记下芸芸的位置（万一刚拖完还没来得及保存）
    app.aboutToQuit.connect(watch.close)         # 退出前关掉旁观模式的通道和角落小条
    pet.show()
    keyboard.start()
    mail.start()
    updates.start()

    # 托盘图标（极少数系统环境没有托盘，那就不显示）
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = PetTray(pet, QIcon(sprites.tray))
        tray.show()
        app.aboutToQuit.connect(tray.hide)  # 退出时立刻移除图标，避免托盘里残留"幽灵图标"

    # 首次使用：还没添加邮箱，就打个招呼并打开邮箱设置窗口
    if not has_mail_account():
        def first_run():
            pet.say(i18n.tr("mail_first_run"))
            pet.open_mail_accounts()
        QTimer.singleShot(800, first_run)  # 稍等芸芸先出现在桌面上，再弹窗口
    sys.exit(app.exec())          # 进入事件循环，直到程序退出


def connect_assistant(pet, machine):
    """助手模式和芸芸连起来：助手在想 / 干活 / 等你确认，芸芸就是那个样子（优先级见 StateMachine.set_assistant）。
    面板本身由 pet.open_assistant() 管；这里不 import 助手的代码（芸芸平时不加载它）。"""
    pet.assistant_activity_changed.connect(machine.set_assistant)
    pet.assistant_speaking_changed.connect(lambda speaking: machine.set_talking(speaking, "voice"))   # 出声回答（阶段 7）
    pet.assistant_listening_changed.connect(machine.set_listening)   # 按住快捷键说话（阶段 7）


def connect_watch(app, pet):
    """旁观模式（阶段 8）：开本机通道收你自己的 Claude Code 的 hook（没连接时也开着，几乎不占东西），收到就提醒你。
    和助手模式无关（不 import 助手的代码）。返回一个有 close() 的对象（退出时关掉）。"""
    from yunyun.pet.watch_reminder import WatchReminder
    from yunyun.watch.server import WatchServer
    server = WatchServer(parent=app)
    reminder = WatchReminder(pet)
    server.event_received.connect(reminder.on_event)
    pet.watch_reminder = reminder   # 设置 → 旁观的"发一条测试提醒"用

    class Watch:
        @staticmethod
        def close():
            server.close()
            reminder.close()
    return Watch


def connect_mail(mail, pet, machine, history=None, quiet=None):
    """用信号把邮件服务和芸芸连起来（单独写成函数，测试里也能直接用）。

    history：可选，记录展示过的卡片；quiet：可选，免打扰时段（期间新邮件只计数）。
    """
    batch = {"id": ""}  # 当前这一轮处理的编号（用开始时间），历史记录按"批"分组
    pet.mail_accounts = lambda: mail.accounts   # "刚才的邮件"、历史页标邮箱时要知道现在有哪些邮箱

    def on_pending_changed(total, newly):
        pet.set_mail_count(total)
        if newly and not (quiet is not None and quiet.active):   # 免打扰：只计数，不激动、不去角落
            if pet.is_visible_to_user():
                machine.on_mail()  # 芸芸看得见：激动几秒（不弹文字，还没经过 AI）
            pet.on_new_mail()      # 芸芸被挡住：小红点单独去任务栏时钟上方

    def on_message_ready(address, message, description):
        if not pet.deck_accepting:
            return  # 用户中途收起了卡片堆：这封不放进去，也不算处理过，下次点小红点再看
        if description is None:  # 处理时出了意外：用兜底文字
            description = ai.Description(ai.fallback_bubble(message), "failed")
        if description.status in ("not_configured", "failed"):  # AI 没用上：在卡片堆底部说明原因（隐私规则是用户自己设的，不用说）
            pet.set_mail_note(ai_note(description))
        label = mailbox_label(address, mail.accounts)   # 两个以上邮箱时标出是哪个收的
        pet.add_mail_card(MailCard(
            bubble=description.bubble,
            subject=message.subject,
            sender=message.sender,
            date=message.date,
            attachments=message.attachments,
            mailbox=label,
            mailbox_address=address if label else "",
        ))
        mail.mark_handled(address, message.uid)  # 放进卡片堆了，才算处理过
        if history is not None:
            history.add(HistoryEntry(description.bubble, message.subject, message.sender,
                                     message.date, message.attachments, time.time(), batch["id"], address))

    def on_fetch_started():
        ai.prepare()                     # 本地模型：趁读邮箱的时候提前启动 Ollama、加载模型
        batch["id"] = f"{time.time():.3f}"
        pet.begin_mail_batch()           # 清空卡片堆，接收这一轮的结果
        machine.start_thinking("mail")   # 芸芸进入思考状态
        pet.set_mail_processing(True)    # 小红点变成转圈

    def on_fetch_finished():
        machine.stop_thinking("mail")    # 回到工作 / 待机（助手还在回答的话继续想）
        pet.set_mail_processing(False)   # 小红点恢复成数字（没有剩余邮件就消失）

    def on_pet_clicked():
        # 单击芸芸：没有待处理的新邮件就说一声（还没添加邮箱时不说，免得误会）
        if mail.has_accounts and mail.pending_count == 0:
            pet.tell_no_new_mail()

    mail.fetch_started.connect(on_fetch_started)
    mail.fetch_finished.connect(on_fetch_finished)
    mail.pending_changed.connect(on_pending_changed)
    mail.message_ready.connect(on_message_ready)
    mail.auth_failed.connect(lambda address: pet.say(i18n.tr("mail_auth_expired", address)))
    pet.clicked.connect(on_pet_clicked)
    pet.mail_badge_clicked.connect(mail.fetch_pending)        # 点小红点 → 才去读邮件
    pet.dismiss_mail_requested.connect(mail.dismiss_all)      # 拖走小红点 → 这批不处理
    pet.mail_accounts_changed.connect(mail.reload_accounts)   # 增删邮箱 → 马上重新检查


def ai_note(description):
    """AI 没用上时，卡片堆底部那行小字：说清楚原因，用户才知道该做什么。"""
    if description.status == "not_configured":
        return i18n.tr("ai_not_configured")
    kind = description.error.kind if description.error is not None else None
    if kind in ("not_running", "model_missing"):   # 本地模型：Ollama 没打开 / 模型没下载
        return i18n.tr(f"ai_local_{kind}")
    return i18n.tr("ai_failed")


def has_mail_account():
    try:
        return bool(accounts.load_accounts())
    except (ConfigError, MailError):
        return False  # 配置文件坏了，也当作还没设置，让用户在窗口里重新添加


if __name__ == "__main__":
    main()
