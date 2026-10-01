"""芸芸的程序入口：创建各个模块，并用信号把它们连起来。

运行方式（在项目根目录）：
    .\\.venv\\Scripts\\python -m yunyun.main
"""
import sys

from PySide6.QtWidgets import QApplication

from yunyun import i18n
from yunyun.activity.keyboard import KeyboardMonitor
from yunyun.pet.sprites import load_sprites
from yunyun.pet.state import StateMachine
from yunyun.pet.window import PetWindow


def main():
    app = QApplication(sys.argv)  # 每个 Qt 程序有且只有一个 QApplication
    app.setOrganizationName("Yunyun")  # QSettings 用这两个名字决定设置存在哪里
    app.setApplicationName("Yunyun")
    i18n.load_language()               # 恢复上次选择的气泡语言

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
    sys.exit(app.exec())          # 进入事件循环，直到程序退出


if __name__ == "__main__":
    main()
