"""邮件 agent 的提醒建议（E6）：卡片上怎么写，用户点了以后去加 / 改 / 取消提醒。

建议只是建议：邮件 agent 自己从不动提醒，用户在卡片上点了"加提醒 / 改 / 取消"，这里才写进 notify/reminders.py 的文件，
再让芸芸本体的闹钟（pet/reminder_clock.py）重新排。
"""
import logging

from yunyun import i18n
from yunyun.notify import reminders
from yunyun.pet.card_deck import CardSuggestion
from yunyun.pet.reminders_page import when_text

log = logging.getLogger(__name__)


def card_suggestions(suggestions, existing, now=None):
    """[mail_agent.Suggestion] → 卡片上的 [CardSuggestion]。existing：现在的提醒；改 / 取消的那个已经不在了就不显示。"""
    by_id = {r.id: r for r in existing}
    result = []
    for s in suggestions:
        if s.op == "add":
            repeat = f" · {i18n.tr('reminders_repeat_' + s.repeat)}" if s.repeat else ""
            result.append(CardSuggestion(i18n.tr("card_suggest_add", when_text(s.when, now), s.text) + repeat,
                                         i18n.tr("card_suggest_add_button"), s))
            continue
        old = by_id.get(s.target)
        if old is None:
            continue
        if s.op == "move":
            label = i18n.tr("card_suggest_move", old.text, when_text(reminders.due_time(old), now), when_text(s.when, now))
            result.append(CardSuggestion(label, i18n.tr("card_suggest_move_button"), s))
        else:
            result.append(CardSuggestion(i18n.tr("card_suggest_cancel", old.text), i18n.tr("card_suggest_cancel_button"), s))
    return result


def apply(card_suggestion, now=None):
    """用户点了"加提醒 / 改 / 取消"：真的去做，把结果写回 card_suggestion（state / message）。返回做成了没有。"""
    s = card_suggestion.action
    try:
        if s.op == "add":
            reminders.add(s.text, s.when, s.repeat, now=now)
        elif s.op == "move":
            reminders.move(s.target, s.when, now=now)
        elif not reminders.cancel(s.target):
            raise reminders.ReminderError("gone")
    except reminders.ReminderError as e:
        log.info("提醒建议没做成（%s）", s.op)   # 不记内容
        card_suggestion.state, card_suggestion.message = "failed", i18n.tr(_failure_key(str(e)))
        return False
    card_suggestion.state, card_suggestion.message = "done", i18n.tr(f"card_suggest_{s.op}_done")
    return True


def _failure_key(reason):
    """ReminderError 的英文说明（写给模型看的）→ 给用户看的那句话。"""
    if "passed" in reason:
        return "card_suggest_failed_past"
    if "too many" in reason.lower():
        return "card_suggest_failed_full"
    if "There is no reminder" in reason or reason == "gone":
        return "card_suggest_failed_gone"
    return "card_suggest_failed"
