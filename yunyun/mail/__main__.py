"""命令行读取最新的未读邮件（M2 验收用）。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python -m yunyun.mail        # 每个邮箱显示最新 5 封未读
    .\\.venv\\Scripts\\python -m yunyun.mail 10     # 显示 10 封

一个包里有 __main__.py 时，就可以用 python -m 包名 来运行它。
"""
import sys

from yunyun.config import ConfigError, load_config
from yunyun.mail.imap_client import MailError, accounts_from_config, fetch_unread

PREVIEW_CHARS = 120  # 正文预览显示多少个字


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    try:
        accounts = accounts_from_config(load_config())
    except (ConfigError, MailError) as e:
        print(e)
        return 1

    for account in accounts:
        print(f"===== {account.name}（{account.address}）=====")
        try:
            messages = fetch_unread(account, limit)
        except MailError as e:
            print(f"[失败] {e}")
            continue
        if not messages:
            print("没有未读邮件。")
            continue
        for i, m in enumerate(messages, 1):
            print_message(i, m)
        print()
    return 0


def print_message(index, m):
    print(f"\n[{index}] {m.subject or '(无标题)'}")
    print(f"    发件人：{m.sender}")
    # astimezone() 不带参数：换算成本机时区再显示
    print(f"    时  间：{m.date.astimezone():%Y-%m-%d %H:%M}" if m.date else "    时  间：未知")
    if m.attachments:
        print(f"    附  件：{'、'.join(name or '(未命名)' for name in m.attachments)}")
    preview = " ".join(m.body.split())  # 把换行和多余空格都压成一个空格，方便预览
    if len(preview) > PREVIEW_CHARS:
        preview = preview[:PREVIEW_CHARS] + "…"
    print(f"    正  文：{preview or '(空)'}")


if __name__ == "__main__":
    sys.exit(main())
