"""Stage `ingest`: union + full-row dedupe, typed dates, member parsing."""

import json
from datetime import date
from types import SimpleNamespace

import duckdb

from src import ingest
from src.logs import sql_path
from tests.conftest import write_members_csv, write_tx_csv

D = date


def test_union_drops_rows_repeated_across_files_and_parses_dates(tmp_path):
    shared = ("u1", 30, 149, D(2017, 1, 31), D(2017, 3, 2), 0)
    v1 = write_tx_csv(tmp_path / "transactions.csv", [shared, ("u2", 7, 0, D(2016, 7, 1), D(2016, 7, 8), 0)])
    v2 = write_tx_csv(tmp_path / "transactions_v2.csv", [shared, ("u3", 30, 149, "2017xx01", D(2017, 3, 31), 0)])
    out = tmp_path / "transactions.parquet"
    con = duckdb.connect()

    summary = ingest.ingest_transactions(con, [v1, v2], out)

    assert summary == {
        "transaction_rows_read": 4,
        "transaction_rows": 3,
        "transaction_duplicates_removed": 1,
        "transaction_date_unparsed": 1,
        "expire_date_unparsed": 0,
    }
    types = dict(
        (r[0], r[1]) for r in con.execute(f"describe select * from read_parquet({sql_path(out)})").fetchall()
    )
    assert types["transaction_date"] == "DATE" and types["membership_expire_date"] == "DATE"


def test_members_registration_date_parses_or_becomes_null(tmp_path):
    csv = write_members_csv(
        tmp_path / "members_v3.csv",
        [("u1", 1, 30, "male", 7, D(2015, 9, 11)), ("u2", 13, 0, None, 9, "notadate"), ("u3", 5, 22, "female", 4, None)],
    )
    out = tmp_path / "members.parquet"
    con = duckdb.connect()

    summary = ingest.ingest_members(con, csv, out)

    assert summary == {"member_rows": 3, "member_users": 3, "registration_date_unparsed": 2}
    assert con.execute(
        f"select registration_init_time from read_parquet({sql_path(out)}) where msno = 'u1'"
    ).fetchone() == (D(2015, 9, 11),)


def test_run_writes_every_output_it_reports(tmp_path):
    raw, cache = tmp_path / "raw", tmp_path / "cache"
    raw.mkdir(), cache.mkdir()
    write_tx_csv(raw / "transactions.csv", [("u1", 7, 0, D(2016, 7, 1), D(2016, 7, 8), 0)])
    write_tx_csv(raw / "transactions_v2.csv", [("u1", 30, 149, D(2016, 7, 8), D(2016, 8, 7), 0)])
    write_members_csv(raw / "members_v3.csv", [("u1", 1, 30, "male", 7, D(2015, 9, 11))])

    outputs = ingest.run(SimpleNamespace(raw_dir=raw, cache_dir=cache))

    assert all((cache / o).exists() for o in outputs)
    assert json.loads((cache / "ingest_summary.json").read_text())["transaction_rows"] == 2
