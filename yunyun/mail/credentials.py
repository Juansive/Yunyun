"""邮箱授权码的安全存取。

授权码保存在 Windows 凭据管理器里（yunyun/vault.py），绝不写进代码、配置文件或 Git。
想查看或删除：控制面板 → 凭据管理器 → Windows 凭据 → 找 "yunyun-mail"。
"""
from yunyun import vault

SERVICE = "yunyun-mail"  # 在凭据管理器里显示的名字；同一个邮箱地址只存一份


def get_password(address):
    """读取授权码；没保存过就返回 None。"""
    return vault.get_password(SERVICE, address)


def set_password(address, password):
    vault.set_password(SERVICE, address, password)


def delete_password(address):
    try:
        vault.delete_password(SERVICE, address)
    except vault.PasswordDeleteError:
        pass  # 本来就没有，不用管
