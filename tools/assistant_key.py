"""把助手模式用的 Claude API Key 存进 Windows 凭据管理器（开发用，计划里的 0.3）。

Key 只在你自己的终端里输入，屏幕上不显示，也不经过聊天；阶段 1 的设置页会直接读到同一份。
想查看或删除：控制面板 → 凭据管理器 → Windows 凭据 → 找 "yunyun-assistant"。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python tools\\assistant_key.py            # 粘贴 Key 并保存
    .\\.venv\\Scripts\\python tools\\assistant_key.py --check    # 看看存了没有（不显示 Key 本身）
    .\\.venv\\Scripts\\python tools\\assistant_key.py --delete   # 删掉
"""
import argparse
import getpass
import sys

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "yunyun-assistant"   # 阶段 1 的 assistant/settings.py 也用这个名字
PROVIDER = "anthropic"         # 每个服务商存一个 Key；阶段 3 会有 deepseek、zhipu……


def describe(key):
    """只说"存了一个多长的 Key"，不把 Key 显示出来。"""
    return f"已保存（{len(key)} 个字符，以 {key[:7]} 开头）"


def main():
    parser = argparse.ArgumentParser(description="保存助手模式用的 Claude API Key")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="看看存了没有")
    group.add_argument("--delete", action="store_true", help="删掉已保存的 Key")
    args = parser.parse_args()

    if args.check:
        key = keyring.get_password(SERVICE, PROVIDER)
        print(describe(key) if key else "还没有保存 Key。")
        return
    if args.delete:
        try:
            keyring.delete_password(SERVICE, PROVIDER)
            print("已删除。")
        except PasswordDeleteError:
            print("本来就没有保存 Key。")
        return

    key = getpass.getpass("粘贴 Claude API Key 后按回车（屏幕上不会显示）：").strip()
    if not key:
        sys.exit("没有输入，什么都没保存。")
    if not key.startswith("sk-ant-"):
        sys.exit("这看起来不像 Claude 的 API Key（应该以 sk-ant- 开头），没有保存。")
    keyring.set_password(SERVICE, PROVIDER, key)
    print(describe(key))


if __name__ == "__main__":
    main()
