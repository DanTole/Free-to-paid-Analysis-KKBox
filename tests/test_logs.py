"""Stage `logs`: bad-seconds rule (D7), trial window, member sample (D9)."""

import json
from datetime import date
from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from src import logs
from tests.conftest import write_logs_csv, write_transactions, write_trials

D = date


def fetch(con, path, sql_tail=""):
    return con.execute(f"select * from read_parquet({logs.sql_path(path)}) {sql_tail}").fetchall()


@pytest.fixture
def con():
    return duckdb.connect()


def test_impossible_seconds_become_null_but_song_counts_stay(tmp_path, con):
    csv = write_logs_csv(
        tmp_path / "user_logs.csv",
        [
            ("u1", D(2016, 7, 1), 1, 0, 0, 0, 4, 5, 300.5),
            ("u1", D(2016, 7, 2), 2, 1, 0, 0, 3, 6, -100.0),
            ("u1", D(2016, 7, 3), 0, 0, 1, 0, 9, 9, 90000.0),
        ],
    )
    out = tmp_path / "user_logs.parquet"
    summary = logs.convert_logs(con, [csv], out, 0, 86_400)

    rows = fetch(con, out, "order by date")
    assert [(r[1], r[7], r[8], r[9]) for r in rows] == [
        (D(2016, 7, 1), 5, 300.5, False),
        (D(2016, 7, 2), 6, None, True),
        (D(2016, 7, 3), 9, None, True),
    ]
    assert [r[6] for r in rows] == [4, 3, 9]  # num_100 kept on bad rows
    assert summary == {"log_rows": 3, "bad_secs_rows": 2, "undated_rows": 0}


def test_trial_window_keeps_days_0_to_6_sums_user_days_and_flags_bad_users(tmp_path, con):
    start = D(2016, 7, 1)
    csv = write_logs_csv(
        tmp_path / "user_logs.csv",
        [
            ("u1", D(2016, 6, 30), 1, 0, 0, 0, 1, 1, 10.0),  # day -1: out
            ("u1", D(2016, 7, 1), 1, 0, 0, 0, 2, 3, 100.0),  # day 0
            ("u1", D(2016, 7, 1), 0, 1, 0, 0, 1, 2, -5.0),  # day 0 again, bad secs
            ("u1", D(2016, 7, 7), 0, 0, 0, 0, 4, 4, 200.0),  # day 6: last kept
            ("u1", D(2016, 7, 8), 0, 0, 0, 0, 9, 9, 900.0),  # day 7: out
            ("u2", D(2016, 7, 3), 3, 0, 0, 0, 0, 3, 50.0),
            ("u3", D(2016, 7, 3), 3, 0, 0, 0, 0, 3, 50.0),  # not a trial user
        ],
    )
    parquet = tmp_path / "user_logs.parquet"
    logs.convert_logs(con, [csv], parquet, 0, 86_400)
    trials = write_trials(tmp_path / "trials.parquet", [("u1", start), ("u2", start)])
    out = tmp_path / "trial_window_logs.parquet"

    summary = logs.trial_window_logs(con, parquet, trials, out, 7)

    rows = con.execute(
        f"select msno, date, day_index, num_100, num_unq, total_secs, bad_secs_rows, user_has_bad_secs "
        f"from read_parquet({logs.sql_path(out)}) order by msno, date"
    ).fetchall()
    assert rows == [
        ("u1", D(2016, 7, 1), 0, 3, 5, 100.0, 1, True),
        ("u1", D(2016, 7, 7), 6, 4, 4, 200.0, 0, True),
        ("u2", D(2016, 7, 3), 2, 0, 3, 50.0, 0, False),
    ]
    assert summary == {"trial_window_user_days": 3, "trial_window_bad_secs_rows": 1, "trial_users_flagged": 1}


def paying_rows(n):
    return [(f"m{i:03d}", 149, 0, D(2016, 1, 1), D(2016, 1, 31)) for i in range(n)]


def test_member_sample_is_fixed_by_seed_and_only_paying_members(tmp_path, con):
    tx = write_transactions(
        tmp_path / "transactions.parquet",
        paying_rows(50)
        + [
            ("free", 0, 0, D(2016, 1, 1), D(2016, 1, 7)),
            ("cancel", 149, 1, D(2016, 1, 1), D(2016, 1, 31)),
        ],
    )

    def sample(size, seed):
        logs.create_member_sample(con, tx, size, seed)
        return sorted(r[0] for r in con.execute("select msno from sample_members").fetchall())

    assert sample(10, 7) == sample(10, 7)
    assert sample(10, 7) != sample(10, 8)
    everyone = sample(1000, 7)
    assert len(everyone) == 50 and "free" not in everyone and "cancel" not in everyone


def test_member_presence_counts_active_members_and_those_who_logged(tmp_path, con):
    csv = write_logs_csv(
        tmp_path / "user_logs.csv",
        [
            ("other", D(2016, 7, 1), 1, 0, 0, 0, 1, 1, 10.0),
            ("a", D(2016, 7, 2), 1, 0, 0, 0, 1, 1, 10.0),
            ("other", D(2016, 7, 3), 1, 0, 0, 0, 1, 1, 10.0),
        ],
    )
    parquet = tmp_path / "user_logs.parquet"
    logs.convert_logs(con, [csv], parquet, 0, 86_400)
    tx = write_transactions(
        tmp_path / "transactions.parquet",
        [
            ("a", 149, 0, D(2016, 7, 1), D(2016, 7, 3)),
            ("b", 149, 0, D(2016, 7, 2), D(2016, 7, 2)),
            ("b", 149, 0, D(2016, 7, 2), D(2016, 7, 2)),  # duplicate interval counts once
        ],
    )
    logs.create_member_sample(con, tx, 100, 1)
    out = tmp_path / "member_presence.parquet"

    logs.member_presence(con, parquet, tx, out)

    assert fetch(con, out, "order by date") == [
        (D(2016, 7, 1), 1, 0),
        (D(2016, 7, 2), 2, 1),
        (D(2016, 7, 3), 1, 0),
    ]


def test_run_writes_every_output_it_reports(tmp_path):
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    raw.mkdir(), cache.mkdir()
    write_logs_csv(raw / "user_logs.csv", [("a", D(2016, 7, 1), 1, 0, 0, 0, 1, 1, -1.0)])
    write_logs_csv(raw / "user_logs_v2.csv", [("a", D(2016, 7, 2), 1, 0, 0, 0, 1, 1, 60.0)])
    write_transactions(cache / "transactions.parquet", [("a", 149, 0, D(2016, 7, 1), D(2016, 7, 31))])
    write_trials(cache / "trials.parquet", [("a", D(2016, 7, 1))])

    outputs = logs.run(SimpleNamespace(raw_dir=raw, cache_dir=cache))

    assert all((cache / o).exists() for o in outputs)
    summary = json.loads((cache / "logs_summary.json").read_text())
    assert summary["log_rows"] == 2 and summary["bad_secs_rows"] == 1
    assert summary["trial_users_flagged"] == 1 and summary["member_sample_size"] == 1


def test_run_names_the_missing_upstream_column(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    write_transactions(cache / "transactions.parquet", [("a", 149, 0, D(2016, 7, 1), D(2016, 7, 31))])
    pd.DataFrame({"msno": ["a"]}).to_parquet(cache / "trials.parquet")  # no trial_start

    with pytest.raises(ValueError, match="trial_start"):
        logs.run(SimpleNamespace(raw_dir=tmp_path, cache_dir=cache))
