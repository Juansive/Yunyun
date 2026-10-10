"""隐私规则：哪些邮件不交给 AI（设置 → AI → 隐私）。

两种规则，存在 config.yaml 的 privacy 部分：
- accounts：整个邮箱，比如工作邮箱的邮件都不给；
- senders：发件人，完整地址（boss@company.com）或 @域名（@icbc.com.cn，子域名 mail.icbc.com.cn 也算）。
命中的邮件只显示"发件人 + 标题"，**本地模型也不给**——用户说"不交给 AI"就是一个字都不发。
"""
import re
from dataclasses import dataclass, field

from yunyun import config as cfg

_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+$")   # \w 也认中文：中文域名也能填


@dataclass
class PrivacyRules:
    accounts: list[str] = field(default_factory=list)   # 小写的邮箱地址
    senders: list[str] = field(default_factory=list)    # normalize_sender() 之后的样子

    def is_private(self, account, sender):
        """account：收到这封邮件的邮箱；sender：发件人地址。"""
        if account.strip().lower() in self.accounts:
            return True
        sender = sender.strip().lower()
        domain = sender.rpartition("@")[2]
        for rule in self.senders:
            if rule.startswith("@"):
                if domain == rule[1:] or domain.endswith("." + rule[1:]):
                    return True
            elif sender == rule:
                return True
        return False


def normalize_sender(text):
    """用户填的发件人规则 → 统一的样子；不像地址也不像域名就返回 None。
    " Boss@Company.com " → "boss@company.com"；"icbc.com.cn" / "@ICBC.com.cn" → "@icbc.com.cn"。"""
    local, _, domain = text.strip().lower().rpartition("@")
    if not _DOMAIN.match(domain) or re.search(r"[\s@<>]", local):
        return None
    return f"{local}@{domain}"   # 没有 local 时正好是 "@域名"


def load_rules():
    try:
        privacy = cfg.load_config_or_empty().get("privacy") or {}
    except cfg.ConfigError:
        return PrivacyRules()   # 配置坏了：邮箱也读不出来，不会有邮件要处理
    senders = (normalize_sender(str(s)) for s in privacy.get("senders") or [])
    return PrivacyRules(accounts=[str(a).strip().lower() for a in privacy.get("accounts") or []],
                        senders=[s for s in senders if s])


def save_rules(rules):
    config = cfg.load_config_or_empty()
    config["privacy"] = {"accounts": rules.accounts, "senders": rules.senders}
    cfg.save_config(config)
