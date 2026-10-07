"""助手的记账本（assistant/ledger.py）。conftest 已经把 LEDGER_PATH 换到临时目录。"""
from datetime import date

from yunyun.assistant import ledger


def test_empty_ledger():
    assert ledger.summary() == ledger.Spending(0.0, 0.0, 0.0)


def test_today_month_and_total():
    ledger.record(0.012, day=date(2026, 10, 7))
    ledger.record(0.003, day=date(2026, 10, 7))
    ledger.record(0.5, day=date(2026, 10, 1))
    ledger.record(1.0, day=date(2026, 9, 30))       # 上个月
    spent = ledger.summary(today=date(2026, 10, 7))
    assert round(spent.today, 6) == 0.015
    assert round(spent.month, 6) == 0.515
    assert round(spent.total, 6) == 1.515


def test_nothing_to_record():
    for cost in (None, 0, 0.0, -0.1):
        ledger.record(cost)
    assert not ledger.LEDGER_PATH.exists()


def test_only_numbers_are_saved():
    ledger.record(0.02, day=date(2026, 10, 7))
    assert ledger.LEDGER_PATH.read_text(encoding="utf-8").replace(" ", "").replace("\n", "") == \
        '{"days":{"2026-10-07":0.02}}'


def test_broken_file_counts_as_empty():
    ledger.LEDGER_PATH.write_text("这不是 JSON", encoding="utf-8")
    assert ledger.summary() == ledger.Spending()
    ledger.record(0.01, day=date(2026, 10, 7))       # 还能接着记
    assert ledger.summary(today=date(2026, 10, 7)).today == 0.01


def test_money_format():
    assert ledger.money(0) == "$0.000"
    assert ledger.money(0.0123) == "$0.012"
    assert ledger.money(1.256) == "$1.26"
