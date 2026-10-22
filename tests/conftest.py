"""Tiny synthetic inputs shaped like the real cache and raw files."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

LOG_HEADER = "msno,date,num_25,num_50,num_75,num_985,num_100,num_unq,total_secs"
TX_HEADER = (
    "msno,payment_method_id,payment_plan_days,plan_list_price,actual_amount_paid,"
    "is_auto_renew,transaction_date,membership_expire_date,is_cancel"
)
MEMBER_HEADER = "msno,city,bd,gender,registered_via,registration_init_time"


def _ymd(value) -> str:
    return value.strftime("%Y%m%d") if isinstance(value, date) else str(value)


def write_tx_csv(path: Path, rows: list[tuple]) -> Path:
    """rows: (msno, plan_days, actual_amount_paid, transaction_date, expire_date, is_cancel).

    Dates may be a date or a raw string (to test unparsable values).
    """
    lines = [TX_HEADER] + [
        f"{m},41,{days},{max(paid, 149)},{paid},0,{_ymd(td)},{_ymd(ed)},{cancel}"
        for m, days, paid, td, ed, cancel in rows
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def write_members_csv(path: Path, rows: list[tuple]) -> Path:
    """rows: (msno, city, bd, gender, registered_via, registration_date or None or raw string)."""
    lines = [MEMBER_HEADER] + [
        f"{m},{city},{bd},{gender or ''},{via},{'' if reg is None else _ymd(reg)}"
        for m, city, bd, gender, via, reg in rows
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def write_logs_csv(path: Path, rows: list[tuple]) -> Path:
    """rows: (msno, date, num_25, num_50, num_75, num_985, num_100, num_unq, total_secs)."""
    lines = [LOG_HEADER] + [
        ",".join([r[0], r[1].strftime("%Y%m%d"), *(str(v) for v in r[2:])]) for r in rows
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def write_transactions(path: Path, rows: list[tuple]) -> Path:
    """rows: (msno, actual_amount_paid, is_cancel, transaction_date, membership_expire_date)."""
    pd.DataFrame(
        rows, columns=["msno", "actual_amount_paid", "is_cancel", "transaction_date", "membership_expire_date"]
    ).to_parquet(path)
    return path


def write_trials(path: Path, rows: list[tuple]) -> Path:
    """rows: (msno, trial_start)."""
    pd.DataFrame(rows, columns=["msno", "trial_start"]).to_parquet(path)
    return path


def days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]
