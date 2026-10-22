"""Stage `trials`: every locked funnel rule and conversion boundary."""

from datetime import date, timedelta

import duckdb
import pytest

from src import ingest, trials
from src.logs import sql_path
from tests.conftest import write_members_csv, write_tx_csv

D = date
START = D(2016, 7, 1)
END = D(2016, 7, 8)  # expiry 7 days after START


def trial(msno, start=START, end=END, cancel=0):
    return (msno, 7, 0, start, end, cancel)


def paid(msno, on, cancel=0):
    return (msno, 30, 149, on, on + timedelta(days=30), cancel)


ROWS = [
    trial("conv_on_expiry"), paid("conv_on_expiry", END),
    trial("conv_day30"), paid("conv_day30", END + timedelta(days=30)),
    trial("late_day31"), paid("late_day31", END + timedelta(days=31)),
    trial("intrial"), paid("intrial", END - timedelta(days=1)),
    trial("cancel_paid"), paid("cancel_paid", END + timedelta(days=2), cancel=1),
    trial("off4", end=START + timedelta(days=4)),
    trial("off5", end=START + timedelta(days=5)),
    trial("off31", end=START + timedelta(days=31)),
    trial("off32", end=START + timedelta(days=32)),
    paid("not_first", D(2016, 5, 1)), trial("not_first"),
    trial("burn_in_out", start=D(2015, 3, 31), end=D(2015, 4, 7)),
    trial("burn_in_in", start=D(2015, 4, 1), end=D(2015, 4, 8)),
    trial("elig_edge", start=D(2017, 1, 22), end=D(2017, 1, 29)),  # 01-29 + 30 = 02-28
    trial("elig_out", start=D(2017, 1, 23), end=D(2017, 1, 30)),
    trial("dup_day"), trial("dup_day", end=END + timedelta(days=1)),
    trial("cancelled_trial", cancel=1),
    trial("no_member"),
    ("paid_7day", 7, 99, START, END, 0),
]


@pytest.fixture
def built(tmp_path):
    con = duckdb.connect()
    tx = tmp_path / "transactions.parquet"
    ingest.ingest_transactions(con, [write_tx_csv(tmp_path / "transactions.csv", ROWS)], tx)
    members = tmp_path / "members.parquet"
    member_ids = sorted({r[0] for r in ROWS} - {"no_member"})
    member_rows = [(m, 1, 30, "male", 7, D(2014, 1, 1)) for m in member_ids]
    ingest.ingest_members(con, write_members_csv(tmp_path / "members_v3.csv", member_rows), members)
    funnel = trials.build_trials(
        con,
        tx,
        members,
        tmp_path / "trials.parquet",
        tmp_path / "trial_campaigns.parquet",
        plan_days=7,
        amount_paid=0,
        expire_min_days=5,
        expire_max_days=31,
        burn_in_start=D(2015, 4, 1),
        window_days=30,
        followup_end=D(2017, 2, 28),
        require_member_row=True,
    )
    full = {
        r[0]: r[1:]
        for r in con.execute(
            f"select msno, trial_end, campaign, converted from read_parquet({sql_path(tmp_path / 'trials.parquet')})"
        ).fetchall()
    }
    campaigns = con.execute(
        f"select * from read_parquet({sql_path(tmp_path / 'trial_campaigns.parquet')}) order by campaign"
    ).fetchall()
    return funnel, full, campaigns


def test_full_population_applies_every_rule(built):
    _, full, _ = built
    assert set(full) == {
        "conv_on_expiry", "conv_day30", "late_day31", "cancel_paid",
        "off5", "off31", "burn_in_in", "elig_edge", "dup_day",
    }


def test_conversion_window_runs_from_expiry_day_to_expiry_plus_30(built):
    _, full, _ = built
    converted = {m for m, (_, _, conv) in full.items() if conv}
    assert converted == {"conv_on_expiry", "conv_day30"}  # day 31 and cancelled payments don't count


def test_same_day_trial_rows_keep_latest_expiry_and_campaign_is_start_month(built):
    _, full, _ = built
    assert full["dup_day"][:2] == (END + timedelta(days=1), "2016-07")


def test_funnel_counts_each_step(built):
    funnel, _, _ = built
    assert funnel == {
        "trial_rows": 17,
        "trials": 16,
        "first_transaction": 15,
        "after_burn_in": 14,
        "expiry_in_range": 12,
        "eligible": 11,
        "has_member_row": 10,
        "full": 9,
        "same_day_trials_with_conflicting_expiry": 1,
        "in_trial_payers_excluded": 1,
    }


def test_campaign_table_reports_no_member_and_in_trial_payers_separately(built):
    _, _, campaigns = built
    # campaign, n_eligible, n_no_member_row, n_in_trial_payers, n_full, n_converted
    assert campaigns == [
        ("2015-04", 1, 0, 0, 1, 0),
        ("2016-07", 9, 1, 1, 7, 2),
        ("2017-01", 1, 0, 0, 1, 0),
    ]
