"""把一封邮件变成芸芸说的一句话，例如"王老师让你下周一前补充第三章的对比实验"。

流程：清理正文（去掉引用的历史邮件、签名，截短）→ 拼提示词 → 调用大模型 → 从回答里取出 JSON。
调用失败时，由 fallback_bubble() 退回"发件人 + 标题"。

提示词的思路（2026-10-05 用 tools/eval_prompt.py 对比后改的）：
- 第一条规则是"忠于原文"：小模型（比如本地的 qwen3:1.7b）很容易照抄例子、复述标题、或者把意思说偏；
- 让它说出最有用的一点（要做什么、什么时候），分清广告 / 推送 / 退信是谁的事；
- 例子做成单独的示范对话（EXAMPLES），和常见邮件不同领域，减少照抄；按界面语言给对应的例句；
- 先给邮件分个类（category）再写提醒：帮小模型先想清楚这是什么邮件，以后也能按类别区别对待；
- 温度调低（TEMPERATURE）：越低越老实、越少"发挥"。
"""
import json
import re

from yunyun import i18n
from yunyun.ai.client import AIError, ask

BODY_LIMIT = 1500   # 正文最多发给模型多少个字：判断"来了什么邮件"不需要全文，也更省、更快
BUBBLE_LIMIT = 60   # 模型话太多时，最多保留多少个字（中文、日文）
TEMPERATURE = 0.3   # 越低越忠于原文；提醒只要一句话，不需要创意

LANGUAGE_RULES = {   # 语言: (提示词里的语言名, 长度要求, 最多保留多少个字符——英文同样的意思要长得多)
    "zh": ("简体中文", "30 个汉字以内", BUBBLE_LIMIT),
    "en": ("English", "within 20 words; don't guess anyone's gender (use their name or \"they\")", 150),
    "ja": ("日本語", "40 文字以内", BUBBLE_LIMIT),
}

CATEGORIES = ("work", "school", "personal", "bill", "shopping", "delivery", "ad", "newsletter",
              "security", "code", "system", "other")

SYSTEM_PROMPT = """你是桌面宠物"芸芸"。用户收到了一封新邮件，请你读完后，用一句话转告用户：是谁、什么事。

要求（越靠前越重要）：
1. 忠于原文：只说邮件里明确写了的内容，不猜测、不补充、不夸张；时间、地点、要做的事都不能改变意思。带条件的话（比如"如非本人操作，请修改密码"）要么连条件一起说，要么不说。看不懂或拿不准时，就照着标题说"某某发来了关于……的邮件"。
2. 这些一律不写：验证码、取件码、密码、金额（多少钱）、卡号、订单号、运单号、手机号、链接、邮箱地址。需要时说"打开邮件看"。
3. 说出最有用的信息：如果邮件要用户做什么、有截止时间或地点，要说出来。
4. 分清是谁的事：促销广告、订阅推送要说明是"广告"或"推送"，不要说成"你的……"；退信是"你发出的邮件被退回了"。
5. {length}，必须用{language}。语气亲切自然，像朋友转告，句末可以带"啦""哦""呢"，但不要每句都用"嘿"开头。
6. 只输出 JSON：{{"category": "类别", "bubble": "你要说的话"}}，不要输出任何其他内容。
   类别从这些里选一个：work 工作、school 学校、personal 私人、bill 账单、shopping 购物订单、delivery 快递、ad 广告促销、newsletter 订阅推送、security 安全提醒、code 验证码、system 系统通知、other 其他。"""

# 示范对话：(邮件内容, 类别, {语言: 芸芸该说的话})。故意和最常见的邮件不同领域，免得小模型照抄
EXAMPLES = [
    ("发件人：张主任 <zhang@lab.example>\n标题：下周二组会改为线上\n正文：\n"
     "各位：下周二上午的组会改为线上进行，请提前准备好汇报材料。",   # 例子里别放多余的细节：小模型会把它搬进别的提醒里
     "work",
     {"zh": "张主任说下周二的组会改成线上开，记得准备汇报材料哦",
      "en": "Director Zhang says next Tuesday's group meeting moves online—get your slides ready",
      "ja": "張主任から：来週火曜のゼミはオンラインになったよ、発表資料を準備してね"}),
    ("发件人：美团外卖 <promo@meituan.example>\n标题：周末专享：满40减15\n正文：\n"
     "亲爱的用户，本周末点外卖满 40 减 15，还有免配送费券等你领取。退订回复 TD。",
     "ad",
     {"zh": "美团外卖发来周末满减的促销广告",
      "en": "Meituan sent an ad for weekend delivery discounts",
      "ja": "美団デリバリーから週末割引の広告が届いたよ"}),
    ("发件人：中国移动 <bill@10086.example>\n标题：您的9月话费账单\n正文：\n"
     "尊敬的客户：您 9 月话费 128.50 元，请于 10 月 20 日前缴纳，逾期可能影响通话。",
     "bill",
     {"zh": "中国移动的 9 月话费账单出了，10 月 20 日前要缴费哦",
      "en": "China Mobile's September phone bill is out—pay it by Oct 20",
      "ja": "中国移動から 9 月の電話料金の請求、10 月 20 日までに払ってね"}),
    ("发件人：支付宝 <service@alipay.example>\n标题：支付宝验证码\n正文：\n"
     "您的验证码是 552817，10 分钟内有效，请勿泄露给他人。",
     "code",
     {"zh": "支付宝发来一个验证码，打开邮件就能看到",
      "en": "Alipay sent you a verification code—open the email to see it",
      "ja": "Alipay から認証コードが届いたよ、メールを開いて確認してね"}),
]


def summarize(message, language, settings):
    """调用大模型，返回芸芸要说的一句话。失败时抛出 AIError。"""
    answer = ask(settings.info, settings.api_key, settings.model, build_messages(message, language),
                 temperature=TEMPERATURE)
    return parse_bubble(answer, limit=LANGUAGE_RULES.get(language, LANGUAGE_RULES["zh"])[2])


def build_messages(message, language):
    """发给模型的对话：系统提示词（芸芸的人设和规则）+ 几段示范对话 + 这封邮件。"""
    if language not in LANGUAGE_RULES:
        language = "zh"
    lang_name, length, _ = LANGUAGE_RULES[language]
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(language=lang_name, length=length)}]
    for mail_text, category, bubbles in EXAMPLES:
        messages.append({"role": "user", "content": mail_text})
        messages.append({"role": "assistant",
                         "content": json.dumps({"category": category, "bubble": bubbles[language]}, ensure_ascii=False)})
    messages.append({"role": "user", "content": build_mail_text(message)})
    return messages


def fallback_bubble(message):
    """AI 用不了时的兜底：发件人 + 标题（有的银行会把验证码直接写在标题里，也要去掉）。"""
    sender = message.sender_name or message.sender_address
    return i18n.tr("ai_fallback", sender, scrub_sensitive(message.subject, drop_clauses=False) or "…")


def build_mail_text(message):
    lines = [
        f"发件人：{message.sender}",
        f"标题：{message.subject or '（无标题）'}",
    ]
    if message.attachments:
        lines.append(f"附件：{'、'.join(name or '（未命名）' for name in message.attachments)}")
    lines.append(f"正文：\n{clean_body(message.body)}")
    return "\n".join(lines)


# 这些行开头的内容，通常是"回复时引用的历史邮件"，后面的都不要
_HISTORY_MARKERS = re.compile(
    r"^\s*(-{2,}\s*(原始邮件|Original Message|Forwarded message)|"
    r"在.{0,40}写道[:：]|On .{0,80}wrote:|发件人[:：]|From:\s)",
    re.IGNORECASE,
)


def clean_body(body, limit=BODY_LIMIT):
    """去掉引用的历史邮件和签名，合并空行，截短。"""
    kept = []
    for line in body.splitlines():
        if _HISTORY_MARKERS.match(line) or line.strip() == "--":  # "-- " 是邮件签名的标准分隔线
            break
        if line.lstrip().startswith(">"):                       # > 开头是引用
            continue
        kept.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_bubble(answer, limit=BUBBLE_LIMIT):
    """从模型的回答里取出那句话。模型有时会多说话、或者用 ```json 包起来，这里都兼容。

    有的模型（自定义接入的 Qwen3、DeepSeek-R1 等）会先输出 <think>……</think> 的思考过程，先去掉；
    思考到一半就被截断（没有 </think>）时，后面也没有答案，按"没给出提醒"处理。
    """
    text = re.sub(r"<think>.*?(?:</think>|$)", "", answer, flags=re.DOTALL | re.IGNORECASE).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)  # 找到第一个 { 到最后一个 }
    if match:
        try:
            bubble = json.loads(match.group(0)).get("bubble", "")
        except (ValueError, AttributeError):
            bubble = ""
    else:
        bubble = text.strip("`\"'「」 \n")  # 没按要求输出 JSON，但回了一句话：也能用
    bubble = scrub_sensitive(" ".join(str(bubble).split()))  # 去掉换行和多余空格，再去掉敏感信息
    if not bubble:
        raise AIError("模型没有给出提醒", kind="bad_response", detail=answer[:300])
    return bubble[:limit] + ("…" if len(bubble) > limit else "")


# ---------- 敏感信息兜底：不管模型听不听话，提醒里都不能出现这些 ----------

# 验证码、取件码这类"码"后面跟着的值（至少 3 位、带数字，免得把 "code review" 当成验证码）
_CODE_VALUE = re.compile(r"(验证码|校验码|动态码|动态密码|取件码|提货码|取货码|密码|口令|\bcode\b|\bPIN\b)"
                         r"\s*(?:是|为|:|：)?\s*(?=[A-Za-z0-9-]{3,})[A-Za-z0-9-]*\d[A-Za-z0-9-]*", re.IGNORECASE)
_MARK = "\x00"   # 删掉"码"的值以后留个记号：这半句话也要整个删掉
_SENSITIVE = re.compile(
    _MARK +                                                        # 上面删掉了值的"码"
    r"|[¥￥$]\s*\d[\d,]*(?:\.\d+)?"                                 # ¥98、$9.99
    r"|\d[\d,]*(?:\.\d+)?\s*(?:元|块钱|美元|美金|RMB|USD|CNY|yuan|dollars?|bucks)"   # 3256元、128.50 元、800 yuan
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d\d(?!\d)"                # 没写单位的金额：3,256.00、98.00
    r"|\d+(?:-\d+){2,}"                                            # 6-3-2048 这类取件码
    r"|\d{5,}"                                                     # 长串数字：卡号、订单号、运单号、手机号、验证码
    r"|https?://\S+|www\.\S+"                                      # 链接
    r"|[\w.+-]+@[\w-]+\.[\w.]+",                                   # 邮箱地址
    re.IGNORECASE)
# 按标点切成"半句话"。英文常用破折号"—"连接两半句，也算；数字中间的逗号（3,256）不算
_CLAUSE = re.compile(r"(?:[^，,；;。！!？?—]|(?<=\d),(?=\d))+(?:[，；;。！!？?—]|,(?!\d))*")


def scrub_sensitive(text, drop_clauses=True):
    """去掉提醒里的敏感信息，返回处理后的文字。

    - drop_clauses=True：含验证码 / 取件码、金额、长串数字、链接、邮箱地址的半句话整个删掉，
      "招商银行发来10月账单，应还款3256元，最后还款日25号" → "招商银行发来10月账单，最后还款日25号"；
      整句删掉比只删数字通顺（"应还款，最后……"读起来很怪）。
      第一句话要留着（它通常说的是"谁、什么事"），只删掉里面的数："某银行发来登录验证码384920" → "某银行发来登录验证码"。
    - drop_clauses=False（用于标题）：只删掉那些数。
    """
    text = _CODE_VALUE.sub(lambda m: m.group(1) + _MARK, text)
    if not drop_clauses:
        return _tidy(_SENSITIVE.sub("", text))
    clauses = _CLAUSE.findall(text) or [text]
    kept = [clauses[0]] + [c for c in clauses[1:] if not _SENSITIVE.search(c)]
    kept[0] = _SENSITIVE.sub("", kept[0])
    return _tidy("".join(kept))


def _tidy(text):
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s*—\s*", "—", text)                               # 破折号两边不留空格
    text = re.sub(r"([，,；;—])\s*(?=[，,；;。！!？?—]|$)", "", text)   # 删掉以后留下的多余逗号、破折号
    return text.strip(" ，,；;—")
