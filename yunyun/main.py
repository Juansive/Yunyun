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
from yunyun.config import ConfigError
from yunyun.mail import accounts
from yunyun.mail.imap_client import MailError
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

    # 临时：还没接邮箱，先用"单击芸芸"模拟收到一封新邮件
    def fake_mail():
        machine.on_mail()
        pet.say(i18n.tr("test_mail"))

    pet.clicked.connect(fake_mail)

    app.aboutToQuit.connect(keyboard.stop)  # 退出前停止键盘监听
    pet.show()
    keyboard.start()

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
