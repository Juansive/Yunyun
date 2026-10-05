"""评测"一句话提醒"写得准不准：用一组容易出错的假邮件，跑一遍芸芸现在的提示词，把结果列出来。

只用下面编好的假邮件，不会读你的真实邮箱。默认用你在芸芸里设置的 AI（本地模型会按需启动 Ollama）。

运行（在项目根目录）：
    .\\.venv\\Scripts\\python tools\\eval_prompt.py              # 每封跑 2 次，看稳不稳定
    .\\.venv\\Scripts\\python tools\\eval_prompt.py --runs 3
    .\\.venv\\Scripts\\python tools\\eval_prompt.py --model qwen3:4b   # 同一个服务商，换个模型比一比

每封邮件下面的"应该是"是人写的参考答案（说清楚"谁、什么事"，不添油加醋），用来对照模型有没有说错、有没有编造。
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 直接运行这个脚本时，让下面能 import yunyun

from yunyun.ai import settings as ai_settings   # noqa: E402
from yunyun.ai.errors import AIError            # noqa: E402
from yunyun.ai.settings import AISettings       # noqa: E402
from yunyun.ai.summarizer import summarize      # noqa: E402
from yunyun.mail.message import MailMessage     # noqa: E402


def mail(subject, name, address, body, attachments=()):
    return MailMessage(uid=0, subject=subject, sender_name=name, sender_address=address, date=None,
                       body=body, attachments=list(attachments))


CASES = [   # (邮件, 应该是)
    (mail("【京东】双11预售开启！iPhone 立减800", "京东", "jd@newsletter.jd.com",
          "亲爱的会员：双11预售火热开启！iPhone 16 立减 800 元，家电满 3000 减 300，还有 100 元无门槛券等你来领。"
          "活动时间 10月20日-11月11日。退订请回复 TD。"),
     "京东发来双 11 促销广告（不是你买了什么）"),
    (mail("Re: 论文第三章修改", "王老师", "wang@univ.edu.cn",
          "收到。第三章我看了，实验部分还要补充和基线方法的对比，下周一前发我。\n\n"
          "在 2026年10月3日 周五 10:02，你 写道：\n> 老师您好，论文第三章已修改，请您查看。"),
     "王老师让你下周一前补充第三章的对比实验"),
    (mail("Your Steam purchase receipt", "Steam", "noreply@steampowered.com",
          "Thank you for your recent purchase on Steam. Hollow Knight: Silksong — Total: ¥ 98.00. "
          "This email confirms your purchase. You can view your purchase history in your account."),
     "Steam 发来了《空洞骑士：丝之歌》的购买收据"),
    (mail("招商银行信用卡10月账单", "招商银行", "creditcard@cmbchina.com",
          "尊敬的客户：您的10月账单已出，本期应还金额 3,256.00 元，最后还款日 2026年10月25日。请按时还款，避免影响信用。"),
     "招商银行信用卡 10 月账单出了，25 日前还款（不写金额）"),
    (mail("", "李明", "liming@company.com", "明天下午的周会改到 3 点了，地点还是 5 楼会议室。"),
     "李明说明天下午的周会改到 3 点"),
    (mail("QQ安全中心：帐号在新设备登录", "QQ安全中心", "safe@tencent.com",
          "您的QQ帐号于 10月4日 21:30 在一台新设备（Windows 电脑）上登录。如非本人操作，请立即修改密码。"),
     "QQ 安全中心提醒：账号在新设备上登录了（不是\"被盗了\"）"),
    (mail("您的包裹已到菜鸟驿站", "菜鸟", "notice@cainiao.com",
          "您的快递（中通 7300123456789）已到达【学校东门菜鸟驿站】，取件码 6-3-2048，请在 3 天内取件。"),
     "快递到学校东门菜鸟驿站了（不写取件码）"),
    (mail("Medium Daily Digest", "Medium", "noreply@medium.com",
          "Stories for you: 'Why Rust is the future of systems programming', 'I quit my job to build an AI startup', "
          "'10 Python tricks you didn't know'. Read more on Medium."),
     "Medium 推送了每日文章精选（订阅邮件）"),
    (mail("关于国庆假期后宿舍安全检查的通知", "学生工作处", "xsc@univ.edu.cn",
          "各位同学：为保障宿舍安全，学校将于10月9日至10月11日开展宿舍安全检查，重点检查违规电器。请同学们提前自查。"),
     "学生处通知：10 月 9 日到 11 日检查宿舍安全"),
    (mail("【字节跳动】面试邀请", "字节跳动招聘", "hr@bytedance.com",
          "您好！感谢您投递算法工程师（实习）岗位。诚邀您参加 10月9日 14:00 的线上技术面试，面试链接将在前一天发送。"),
     "字节跳动邀请你 10 月 9 日下午 2 点线上面试"),
    (mail("合同扫描件", "刘经理", "liu@partner.com", "", ["租赁合同_签字版.pdf"]),
     "刘经理发来了签字版租赁合同的扫描件"),
    (mail("Undelivered Mail Returned to Sender", "Mail Delivery System", "MAILER-DAEMON@qq.com",
          "This is the mail system. I'm sorry to have to inform you that your message could not be delivered "
          "to one or more recipients: <zhangsan@exmaple.com>: Host not found."),
     "你发出的一封邮件被退回了（地址有问题）"),
    (mail("【某银行】登录验证码", "某银行", "noreply@bank.example",
          "您正在登录网上银行，验证码为 384920，5 分钟内有效。如非本人操作，请忽略本邮件。"),
     "银行发来登录验证码（不写验证码）"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=2, help="每封跑几次")
    parser.add_argument("--model", help="换个模型（同一个服务商）")
    parser.add_argument("--lang", default="zh")
    args = parser.parse_args()

    settings = ai_settings.load_settings()
    if settings is None:
        sys.exit("还没在芸芸里设置 AI。")
    if args.model:
        settings = AISettings(settings.provider, args.model, settings.api_key)
    print(f"== {settings.provider} / {settings.model}，每封 {args.runs} 次\n")

    start = time.time()
    for message, expected in CASES:
        print(f"【{message.subject or '（无标题）'}】{message.sender_name}")
        print(f"   应该是：{expected}")
        for _ in range(args.runs):
            try:
                print(f"   芸芸：{summarize(message, args.lang, settings)}")
            except AIError as e:
                print(f"   出错：{e.kind} {e.detail}")
        print()
    print(f"共用时 {time.time() - start:.0f} 秒")


if __name__ == "__main__":
    main()
