"""邮箱授权码的安全存取。

授权码保存在 Windows 凭据管理器里（通过 keyring 库），绝不写进代码、配置文件或 Git。
想查看或删除：控制面板 → 凭据管理器 → Windows 凭据 → 找 "yunyun-mail"。
"""
import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "yunyun-mail"  # 在凭据管理器里显示的名字；同一个邮箱地址只存一份


def get_password(address):
    """读取授权码；没保存过就返回 None。"""
    return keyring.get_password(SERVICE, address)


def set_password(address, password):
    keyring.set_password(SERVICE, address, password)


def delete_password(address):
    try:
        keyring.delete_password(SERVICE, address)
    except PasswordDeleteError:
        pass  # 本来就没有，不用管
