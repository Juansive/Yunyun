"""邮箱账号的增删查。

- 账号信息（名字、类型、地址）存在 config.yaml；
- 授权码存在 Windows 凭据管理器（见 credentials.py），绝不写进文件。

config.yaml 由程序自动维护，用户不需要手动编辑。
"""
from dataclasses import asdict

from yunyun import config as cfg
from yunyun.mail import credentials
from yunyun.mail.imap_client import accounts_from_config


def load_accounts():
    """读取已添加的邮箱账号；还没有配置文件时返回空列表。"""
    return accounts_from_config(cfg.load_config_or_empty())


def save_account(account, password):
    """添加或更新一个账号（同一个邮箱地址只保留一份）。请先用 check_login() 确认能登录。"""
    credentials.set_password(account.address, password)
    others = [a for a in load_accounts() if not _same(a.address, account.address)]
    _write_accounts(others + [account])


def remove_account(address):
    """删除账号，连同保存的授权码一起删掉。"""
    credentials.delete_password(address)
    _write_accounts([a for a in load_accounts() if not _same(a.address, address)])


def _same(a, b):
    return a.strip().lower() == b.strip().lower()  # 邮箱地址不区分大小写


def _write_accounts(accounts):
    config = cfg.load_config_or_empty()
    mail = config.get("mail") or {}
    mail["accounts"] = [asdict(a) for a in accounts]  # asdict：把 dataclass 变成普通字典
    config["mail"] = mail
    cfg.save_config(config)  # 只改 mail 部分，其他部分（比如 ai）原样保留
