"""芸芸的程序入口：创建各个模块，并用信号把它们连起来。

运行方式（在项目根目录）：
    .\\.venv\\Scripts\\python -m yunyun.main
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from yunyun import i18n
from yunyun.activity.keyboard import KeyboardMonitor
from yunyun.ai import fake as ai
from yunyun.config import ConfigError
from yunyun.mail import accounts
from yunyun.mail.imap_client import MailError
from yunyun.mail.service import MailService
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import StateMachine
from yunyun.pet.tray import PetTray
from yunyun.pet.window import PetWindow


def main():
    app = QApplication(sys.argv)  # 每个 Qt 程序有且只有一个 QApplication
    app.setOrganizationName("Yunyun")  # QSettings 用这两个名字决定设置存在哪里
    app.setApplicationName("Yunyun")
    i18n.load_language()               # 恢复上次选择的语言

    sprites = load_sprites()      # 必须在 QApplication 之后（QPixmap 需要它）
    machine = StateMachine()
    pet = PetWindow(sprites)
    keyboard = KeyboardMonitor()

    # 用信号把模块连起来：各模块之间互不 import，以后换界面层时只换 PetWindow
    keyboard.key_pressed.connect(machine.on_key)   # 敲键盘 → 状态机
    machine.state_changed.connect(pet.set_state)   # 状态变化 → 换画面
    machine.typed.connect(pet.tap)                 # 工作中敲一下 → 芸芸也敲一下

    # ---- 邮件：后台定时检查 → 小红点 ----
    mail = MailService()

    def on_pending_changed(total, newly):
        pet.set_mail_count(total)
        if newly:
            if pet.is_visible_to_user():
                machine.on_mail()  # 芸芸看得见：激动几秒（不弹文字，还没经过 AI）
            pet.on_new_mail()      # 芸芸被挡住：小红点单独去任务栏时钟上方

    def on_message_ready(address, message):
        # 临时（第 3 步会换成"思考状态 + 卡片堆"）：用假的 AI 拼一句话，弹气泡
        pet.say(ai.summarize(message)["bubble"])
        mail.mark_handled(address, message.uid)

    def on_pet_clicked():
        # 单击芸芸：没有待处理的新邮件就说一声（还没添加邮箱时不说，免得误会）
        if mail.has_accounts and mail.pending_count == 0:
            pet.tell_no_new_mail()

    pet.clicked.connect(on_pet_clicked)
    mail.pending_changed.connect(on_pending_changed)
    mail.message_ready.connect(on_message_ready)
    mail.auth_failed.connect(lambda address: pet.say(i18n.tr("mail_auth_expired", address)))
    pet.mail_badge_clicked.connect(mail.fetch_pending)        # 点小红点 → 才去读邮件
    pet.dismiss_mail_requested.connect(mail.dismiss_all)      # 拖走小红点 → 这批不处理
    pet.mail_accounts_changed.connect(mail.reload_accounts)   # 增删邮箱 → 马上重新检查

    app.aboutToQuit.connect(keyboard.stop)  # 退出前停止键盘监听
    pet.show()
    keyboard.start()
    mail.start()

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


def has_mail_account():
    try:
        return bool(accounts.load_accounts())
    except (ConfigError, MailError):
        return False  # 配置文件坏了，也当作还没设置，让用户在窗口里重新添加


if __name__ == "__main__":
    main()
