"""Stage `completeness`: gap-day rule and trials that touch a gap day."""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import duckdb
import pandas as pd

from src import completeness
from src.logs import sql_path
from tests.conftest import days, write_trials

START = date(2016, 1, 1)  # a Friday
ZERO_DAY = date(2016, 2, 10)  # no log users at all
DROP_32 = date(2016, 2, 11)  # share 0.34 vs 0.50 reference: 32% drop -> gap
DROP_28 = date(2016, 2, 12)  # share 0.36 vs 0.50 reference: 28% drop -> not a gap
LAST_LOG_DAY = date(2016, 3, 31)


def write_inputs(tmp_path):
    calendar = days(START, (LAST_LOG_DAY - START).days + 1)
    daily = pd.DataFrame({"date": [x for x in calendar if x != ZERO_DAY], "n_users": 1000})
    presence = pd.DataFrame({"date": calendar, "n_active": 100, "n_with_log": 50})
    presence.loc[presence.date == DROP_32, "n_with_log"] = 34
    presence.loc[presence.date == DROP_28, "n_with_log"] = 36
    daily.to_parquet(tmp_path / "daily_log_users.parquet")
    presence.to_parquet(tmp_path / "member_presence.parquet")
    write_trials(
        tmp_path / "trials.parquet",
        [
            ("clean", date(2016, 1, 20)),
            ("zero", ZERO_DAY - timedelta(days=6)),  # day 6 is the zero day
            ("drop32", DROP_32),
            ("drop28", DROP_28),  # window Feb 12-18 holds only the 28% day
            ("past_end", LAST_LOG_DAY - timedelta(days=2)),  # window runs past the logs
        ],
    )


def test_gap_days_and_the_trials_that_touch_them(tmp_path):
    write_inputs(tmp_path)

    completeness.run(SimpleNamespace(raw_dir=tmp_path, cache_dir=tmp_path))

    con = duckdb.connect()
    gaps = {
        r[0]
        for r in con.execute(
            f"select date from read_parquet({sql_path(tmp_path / 'gap_days.parquet')}) where is_gap"
        ).fetchall()
    }
    assert ZERO_DAY in gaps and DROP_32 in gaps and DROP_28 not in gaps
    assert {LAST_LOG_DAY + timedelta(days=1), LAST_LOG_DAY + timedelta(days=4)} <= gaps
    assert max(gaps) == LAST_LOG_DAY + timedelta(days=4)  # calendar ends at the last trial day

    touched = dict(
        con.execute(
            f"select msno, touches_gap_day from read_parquet({sql_path(tmp_path / 'trial_completeness.parquet')})"
        ).fetchall()
    )
    assert touched == {"clean": False, "zero": True, "drop32": True, "drop28": False, "past_end": True}

    summary = json.loads((tmp_path / "completeness_summary.json").read_text())
    assert summary["trials"] == 5 and summary["trials_touching_gap_day"] == 3


def test_reference_ignores_the_day_itself_and_other_weekdays(tmp_path):
    write_inputs(tmp_path)
    con = duckdb.connect()
    out = tmp_path / "gap_days.parquet"

    completeness.gap_days(
        con,
        tmp_path / "daily_log_users.parquet",
        tmp_path / "member_presence.parquet",
        tmp_path / "trials.parquet",
        out,
        window_days=7,
        reference_weeks=8,
        relative_drop=0.30,
    )

    share, reference = con.execute(
        f"select share, reference from read_parquet({sql_path(out)}) where date = ?", [DROP_32]
    ).fetchone()
    assert share == 0.34 and reference == 0.5
