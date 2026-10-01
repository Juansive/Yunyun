"""芸芸的程序入口。

运行方式（在项目根目录）：
    .venv\\Scripts\\python -m yunyun.main
"""
import sys

from PySide6.QtWidgets import QApplication

from yunyun.pet.window import PetWindow


def main():
    app = QApplication(sys.argv)  # 每个 Qt 程序有且只有一个 QApplication
    pet = PetWindow()
    pet.show()
    sys.exit(app.exec())          # 进入事件循环，直到程序退出


if __name__ == "__main__":
    main()
