"""Stage `ingest`: raw transaction and member CSVs to typed Parquet.

Writes (in cache_dir):
    transactions.parquet   transactions.csv + transactions_v2.csv, every column,
                           dates as DATE, exact duplicate rows across the two
                           files removed (full-row dedupe).
    members.parquet        members_v3.csv, registration_init_time as DATE
                           (NULL when it does not parse).
    ingest_summary.json    row counts, duplicates removed, unparsed dates.
"""

import json
from pathlib import Path

import duckdb

from src import definitions as d
from src.logs import sql_path

TRANSACTION_COLUMNS = (
    "{'msno': 'VARCHAR', 'payment_method_id': 'INTEGER', 'payment_plan_days': 'INTEGER', "
    "'plan_list_price': 'INTEGER', 'actual_amount_paid': 'INTEGER', 'is_auto_renew': 'INTEGER', "
    "'transaction_date': 'VARCHAR', 'membership_expire_date': 'VARCHAR', 'is_cancel': 'INTEGER'}"
)
MEMBER_COLUMNS = (
    "{'msno': 'VARCHAR', 'city': 'INTEGER', 'bd': 'INTEGER', 'gender': 'VARCHAR', "
    "'registered_via': 'INTEGER', 'registration_init_time': 'VARCHAR'}"
)


def ingest_transactions(con: duckdb.DuckDBPyConnection, csv_paths: list[Path], out: Path) -> dict:
    files = "[" + ", ".join(sql_path(p) for p in csv_paths) + "]"
    raw = con.execute(
        f"select count(*) from read_csv({files}, header = true, columns = {TRANSACTION_COLUMNS})"
    ).fetchone()[0]
    con.execute(
        f"""
        copy (
            select distinct
                msno, payment_method_id, payment_plan_days, plan_list_price,
                actual_amount_paid, is_auto_renew,
                try_strptime(transaction_date, '%Y%m%d')::date as transaction_date,
                try_strptime(membership_expire_date, '%Y%m%d')::date as membership_expire_date,
                is_cancel
            from read_csv({files}, header = true, columns = {TRANSACTION_COLUMNS})
        ) to {sql_path(out)} (format parquet)
        """
    )
    rows, bad_tx, bad_exp = con.execute(
        f"select count(*), count(*) filter (where transaction_date is null), "
        f"count(*) filter (where membership_expire_date is null) from read_parquet({sql_path(out)})"
    ).fetchone()
    return {
        "transaction_rows_read": raw,
        "transaction_rows": rows,
        "transaction_duplicates_removed": raw - rows,
        "transaction_date_unparsed": bad_tx,
        "expire_date_unparsed": bad_exp,
    }


def ingest_members(con: duckdb.DuckDBPyConnection, csv_path: Path, out: Path) -> dict:
    con.execute(
        f"""
        copy (
            select msno, city, bd, gender, registered_via,
                   try_strptime(registration_init_time, '%Y%m%d')::date as registration_init_time
            from read_csv({sql_path(csv_path)}, header = true, columns = {MEMBER_COLUMNS})
        ) to {sql_path(out)} (format parquet)
        """
    )
    rows, users, bad_reg = con.execute(
        f"select count(*), count(distinct msno), count(*) filter (where registration_init_time is null) "
        f"from read_parquet({sql_path(out)})"
    ).fetchone()
    return {"member_rows": rows, "member_users": users, "registration_date_unparsed": bad_reg}


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    con = duckdb.connect()
    summary = ingest_transactions(
        con, [ctx.raw_dir / f for f in d.TRANSACTIONS_FILES], cache / "transactions.parquet"
    )
    summary |= ingest_members(con, ctx.raw_dir / d.MEMBERS_FILE, cache / "members.parquet")
    (cache / "ingest_summary.json").write_text(json.dumps(summary, indent=2))
    return ["transactions.parquet", "members.parquet", "ingest_summary.json"]
