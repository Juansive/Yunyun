"""邮箱设置向导：测试登录，成功后把授权码保存到 Windows 凭据管理器，并生成 config.yaml。

请在你自己的终端里运行（在项目根目录）：
    .\\.venv\\Scripts\\python -m tools.setup_mail

授权码输入时屏幕上不会显示，输完直接按回车。授权码不要告诉任何人。
"""
import getpass
import sys

import yaml

from yunyun.config import CONFIG_PATH, ConfigError, load_config
from yunyun.mail import credentials
from yunyun.mail.imap_client import PROVIDERS, MailAccount, MailError, check_login

DEFAULT_NAMES = {"qq": "QQ邮箱", "163": "163邮箱", "gmail": "Gmail"}


def main():
    print("===== 芸芸 · 邮箱设置 =====")

    provider = input(f"邮箱类型（{' / '.join(PROVIDERS)}，直接回车 = qq）：").strip().lower() or "qq"
    if provider not in PROVIDERS:
        print(f"不支持的邮箱类型：{provider}")
        return 1

    address = input("邮箱地址：").strip()
    if "@" not in address:
        print("邮箱地址格式不对。")
        return 1

    # getpass：输入时不回显，防止被旁边的人或屏幕录制看到
    password = getpass.getpass("授权码（输入时不会显示，输完按回车）：").strip().replace(" ", "")
    if not password:
        print("没有输入授权码。")
        return 1

    account = MailAccount(name=DEFAULT_NAMES[provider], provider=provider, address=address)
    print("正在测试登录……")
    try:
        check_login(account, password)
    except MailError as e:
        print(f"[失败] {e}")
        print("授权码没有保存，请检查后重新运行本程序。")
        return 1

    credentials.set_password(address, password)
    print("[成功] 登录成功，授权码已保存到 Windows 凭据管理器。")

    save_account_to_config(account)
    print("\n下一步，读取最新的未读邮件：")
    print("    .\\.venv\\Scripts\\python -m yunyun.mail")
    return 0


def save_account_to_config(account):
    """把账号写进 config.yaml（只写地址等信息，不写授权码）。"""
    entry = {"name": account.name, "provider": account.provider, "address": account.address}

    if not CONFIG_PATH.exists():
        data = {"mail": {"accounts": [entry]}}
        header = (
            "# 芸芸的本地配置（不会提交到 Git），模板见 config.example.yaml。\n"
            "# 授权码不在这里，保存在 Windows 凭据管理器中。\n\n"
        )
        # allow_unicode：中文原样写入；sort_keys=False：保持我们写的顺序
        text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
        CONFIG_PATH.write_text(header + text, encoding="utf-8")
        print(f"已生成配置文件：{CONFIG_PATH}")
        return

    try:
        config = load_config()
    except ConfigError as e:
        print(e)
        return
    accounts = (config.get("mail") or {}).get("accounts") or []
    if any(str(a.get("address", "")).lower() == account.address.lower() for a in accounts):
        print(f"配置文件里已经有 {account.address}，不用改动。")
    else:
        # 为了不弄丢你在 config.yaml 里写的注释，这里不自动改写，只提示手动添加
        print(f"请在 {CONFIG_PATH} 的 mail → accounts 下手动添加：")
        print(yaml.safe_dump([entry], allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    sys.exit(main())
