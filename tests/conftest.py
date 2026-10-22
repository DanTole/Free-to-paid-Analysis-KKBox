"""Tiny synthetic inputs shaped like the real cache and raw files."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

LOG_HEADER = "msno,date,num_25,num_50,num_75,num_985,num_100,num_unq,total_secs"


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
