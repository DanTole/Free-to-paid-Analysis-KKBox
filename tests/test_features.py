"""Stage `features`: per-trial behavior from the trial-window logs."""

from datetime import date

import duckdb
import pandas as pd
import pytest

from src import features

START = date(2016, 7, 1)


def write(tmp_path):
    pd.DataFrame(
        [
            ("active", START, "2016-07", True),
            ("silent", START, "2016-07", False),
            ("gapped", START, "2016-07", False),
        ],
        columns=["msno", "trial_start", "campaign", "converted"],
    ).to_parquet(tmp_path / "trials.parquet")
    # One row per user-day, as stage `logs` writes it.
    pd.DataFrame(
        [
            # msno, trial_start, date, day_index, num_25, num_50, num_75, num_985, num_100, num_unq, total_secs, bad_secs_rows
            ("active", START, date(2016, 7, 2), 1, 2, 1, 0, 1, 6, 8, 1200.0, 0),
            ("active", START, date(2016, 7, 4), 3, 0, 0, 0, 0, 10, 9, None, 1),
            ("gapped", START, date(2016, 7, 1), 0, 1, 0, 0, 0, 1, 2, 60.0, 0),
        ],
        columns=[
            "msno", "trial_start", "date", "day_index", "num_25", "num_50", "num_75",
            "num_985", "num_100", "num_unq", "total_secs", "bad_secs_rows",
        ],
    ).to_parquet(tmp_path / "trial_window_logs.parquet")
    pd.DataFrame(
        [("active", START, False), ("silent", START, False), ("gapped", START, True)],
        columns=["msno", "trial_start", "touches_gap_day"],
    ).to_parquet(tmp_path / "trial_completeness.parquet")


def test_behavior_features_per_trial(tmp_path):
    write(tmp_path)
    out = tmp_path / "features.parquet"

    summary = features.build_features(
        duckdb.connect(),
        tmp_path / "trials.parquet",
        tmp_path / "trial_window_logs.parquet",
        tmp_path / "trial_completeness.parquet",
        out,
    )

    got = pd.read_parquet(out).set_index("msno")
    a = got.loc["active"]
    assert a["days_active"] == 2 and a["plays"] == 20
    assert a["listen_minutes"] == pytest.approx(20.0)  # the nulled day adds no minutes
    assert a["completion_rate"] == pytest.approx(16 / 20)
    assert a["early_skip_share"] == pytest.approx(2 / 20)
    assert a["daily_unique_songs"] == 17 and a["first_listen_day"] == 1
    assert a["has_bad_secs"] and a["in_common"]

    s = got.loc["silent"]  # no rows in the window: zero listening, rates undefined
    assert s["days_active"] == 0 and s["plays"] == 0 and s["listen_minutes"] == 0
    assert pd.isna(s["completion_rate"]) and pd.isna(s["first_listen_day"])
    assert not s["has_bad_secs"] and s["in_common"]

    assert not got.loc["gapped", "in_common"]
    assert summary == {"trials": 3, "in_common": 2, "no_listening_in_window": 1, "has_bad_secs": 1}

    bands = ["days_active_band", "minutes_band", "completion_band", "skip_band", "unique_band", "first_day_band"]
    # active: 2 days, 20 min (10-50), completion .80 (.75-.90), skip .10 (edge -> .10-.30), 17 songs (edge -> 17-55)
    assert got.loc["active", bands].tolist() == ["2", "2:10-50", "4:0.75-0.9", "2:0.1-0.3", "3:17-55", "d1"]
    # silent: days 0, band 1 everywhere else, first day d0
    assert got.loc["silent", bands].tolist() == ["0", "1:<10", "1:<0.1", "1:<0.1", "1:<4", "d0"]


def test_band_edges_are_lower_inclusive():
    import duckdb as db

    con = db.connect()
    case = features.edge_band("x", (10, 50, 200, 500))
    got = [con.execute(f"select {case} from (select ?::double as x)", [v]).fetchone()[0] for v in (None, 9.99, 10, 49.9, 50, 499, 500)]
    assert got == ["1:<10", "1:<10", "2:10-50", "2:10-50", "3:50-200", "4:200-500", "5:500+"]
