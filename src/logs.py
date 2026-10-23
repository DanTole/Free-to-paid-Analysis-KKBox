"""Stage `logs`: one scan of the listening logs, then three small tables.

Reads (from upstream stages, in cache_dir):
    transactions.parquet  msno, actual_amount_paid, is_cancel,
                          transaction_date DATE, membership_expire_date DATE
    trials.parquet        msno, trial_start DATE

Writes (in cache_dir):
    user_logs.parquet           every log row, typed. total_secs is NULL when the
                                raw value is outside [MIN, MAX]_VALID_TOTAL_SECS
                                (eng review D7); bad_secs marks those rows.
    trial_window_logs.parquet   one row per (msno, trial_start, date) for trial
                                days 0..BEHAVIOR_WINDOW_DAYS-1, summed per user-day.
                                user_has_bad_secs flags users with any nulled row
                                in their window.
    daily_log_users.parquet     date, n_users (distinct users with a log row).
    member_presence.parquet     date, n_active, n_with_log for a fixed sample of
                                paying members (eng review D9).
    logs_summary.json           row counts, including nulled-seconds rows.
"""

import json
from pathlib import Path

import duckdb

from src import definitions as d

SONG_COLUMNS = ("num_25", "num_50", "num_75", "num_985", "num_100", "num_unq")

REQUIRED_COLUMNS = {
    "transactions.parquet": ("msno", "actual_amount_paid", "is_cancel", "transaction_date", "membership_expire_date"),
    "trials.parquet": ("msno", "trial_start"),
}


def sql_path(path: Path) -> str:
    """Path as a quoted SQL string literal."""
    return "'" + path.as_posix().replace("'", "''") + "'"


def check_inputs(con: duckdb.DuckDBPyConnection, cache_dir: Path) -> None:
    for name, required in REQUIRED_COLUMNS.items():
        path = cache_dir / name
        if not path.exists():
            raise FileNotFoundError(f"logs stage needs {path} from an upstream stage")
        have = {row[0] for row in con.execute(f"describe select * from read_parquet({sql_path(path)})").fetchall()}
        missing = [c for c in required if c not in have]
        if missing:
            raise ValueError(f"{name} is missing column(s) {missing}")


def convert_logs(
    con: duckdb.DuckDBPyConnection, csv_paths: list[Path], out: Path, min_secs: float, max_secs: float
) -> dict:
    """Scan the raw log CSVs once into Parquet, nulling impossible seconds (D7)."""
    files = "[" + ", ".join(sql_path(p) for p in csv_paths) + "]"
    columns = (
        "{'msno': 'VARCHAR', 'date': 'VARCHAR', 'num_25': 'BIGINT', 'num_50': 'BIGINT', "
        "'num_75': 'BIGINT', 'num_985': 'BIGINT', 'num_100': 'BIGINT', 'num_unq': 'BIGINT', "
        "'total_secs': 'DOUBLE'}"
    )
    con.execute(
        f"""
        copy (
            select
                msno,
                strptime(date, '%Y%m%d')::date as date,
                {", ".join(SONG_COLUMNS)},
                case when total_secs between {float(min_secs)} and {float(max_secs)}
                     then total_secs end as total_secs,
                not coalesce(total_secs between {float(min_secs)} and {float(max_secs)}, false) as bad_secs
            from read_csv({files}, header = true, columns = {columns})
        ) to {sql_path(out)} (format parquet, compression zstd)
        """
    )
    rows, bad, undated = con.execute(
        f"select count(*), count(*) filter (where bad_secs), count(*) filter (where date is null) "
        f"from read_parquet({sql_path(out)})"
    ).fetchone()
    return {"log_rows": rows, "bad_secs_rows": bad, "undated_rows": undated}


def trial_window_logs(
    con: duckdb.DuckDBPyConnection, logs: Path, trials: Path, out: Path, window_days: int
) -> dict:
    """Sum each trial user's log rows per day over trial days 0..window_days-1."""
    con.execute(
        f"""
        copy (
            with t as (select distinct msno, trial_start from read_parquet({sql_path(trials)})),
            days as (
                select
                    t.msno, t.trial_start, l.date,
                    datediff('day', t.trial_start, l.date) as day_index,
                    {", ".join(f"sum(l.{c}) as {c}" for c in SONG_COLUMNS)},
                    sum(l.total_secs) as total_secs,
                    count(*) filter (where l.bad_secs) as bad_secs_rows
                from read_parquet({sql_path(logs)}) l
                join t on l.msno = t.msno
                     and l.date >= t.trial_start
                     and l.date < t.trial_start + {int(window_days)}
                group by all
            )
            select *, bool_or(bad_secs_rows > 0) over (partition by msno, trial_start) as user_has_bad_secs
            from days
            order by msno, trial_start, date
        ) to {sql_path(out)} (format parquet)
        """
    )
    user_days, bad_rows, flagged = con.execute(
        f"select count(*), coalesce(sum(bad_secs_rows), 0), "
        f"count(distinct (msno, trial_start)) filter (where user_has_bad_secs) from read_parquet({sql_path(out)})"
    ).fetchone()
    return {"trial_window_user_days": user_days, "trial_window_bad_secs_rows": bad_rows, "trial_users_flagged": flagged}


def daily_log_users(con: duckdb.DuckDBPyConnection, logs: Path, out: Path) -> None:
    con.execute(
        f"""
        copy (
            select date, count(distinct msno) as n_users
            from read_parquet({sql_path(logs)})
            where date is not null
            group by date order by date
        ) to {sql_path(out)} (format parquet)
        """
    )


def create_member_sample(con: duckdb.DuckDBPyConnection, transactions: Path, size: int, seed: int) -> int:
    """Temp table sample_members: the `size` paying members with the smallest
    md5(seed:msno) (D9). Returns the sample size.

    md5 rather than DuckDB's hash() so the sample does not change between
    DuckDB versions.
    """
    con.execute(
        f"""
        create or replace temp table sample_members as
        select msno from (
            select distinct msno from read_parquet({sql_path(transactions)})
            where actual_amount_paid > 0 and is_cancel = 0
        )
        order by md5('{int(seed)}:' || msno), msno
        limit {int(size)}
        """
    )
    return con.execute("select count(*) from sample_members").fetchone()[0]


def member_presence(con: duckdb.DuckDBPyConnection, logs: Path, transactions: Path, out: Path) -> None:
    """Per day: sampled members with an active paid membership, and how many logged.

    Needs the sample_members table from create_member_sample. Active paid
    membership on day x = some paid, non-cancel transaction with
    transaction_date <= x <= membership_expire_date. Days run over the log range.
    """
    con.execute(
        f"""
        copy (
            with bounds as (
                select min(date) as lo, max(date) as hi from read_parquet({sql_path(logs)})
            ),
            intervals as (
                select tx.msno,
                       greatest(tx.transaction_date, b.lo) as start_day,
                       least(tx.membership_expire_date, b.hi) as end_day
                from read_parquet({sql_path(transactions)}) tx
                join sample_members using (msno)
                cross join bounds b
                where tx.actual_amount_paid > 0 and tx.is_cancel = 0
                  and tx.membership_expire_date >= tx.transaction_date
            ),
            active as (
                select distinct msno, unnest(generate_series(start_day, end_day, interval 1 day))::date as date
                from intervals where start_day <= end_day
            ),
            logged as (
                select distinct l.msno, l.date
                from read_parquet({sql_path(logs)}) l join sample_members using (msno)
            )
            select a.date,
                   count(*) as n_active,
                   count(lg.msno) as n_with_log
            from active a left join logged lg on lg.msno = a.msno and lg.date = a.date
            group by a.date order by a.date
        ) to {sql_path(out)} (format parquet)
        """
    )


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    con = duckdb.connect()
    # Large aggregations spill to disk; keep the spill inside the ignored cache,
    # not DuckDB's default .tmp in the working directory.
    con.execute(f"set temp_directory = {sql_path(cache / 'duckdb_tmp')}")
    check_inputs(con, cache)
    logs = cache / "user_logs.parquet"

    summary = convert_logs(
        con, [ctx.raw_dir / f for f in d.USER_LOGS_FILES], logs, d.MIN_VALID_TOTAL_SECS, d.MAX_VALID_TOTAL_SECS
    )
    summary |= trial_window_logs(con, logs, cache / "trials.parquet", cache / "trial_window_logs.parquet", d.BEHAVIOR_WINDOW_DAYS)
    daily_log_users(con, logs, cache / "daily_log_users.parquet")
    summary["member_sample_size"] = create_member_sample(
        con, cache / "transactions.parquet", d.MEMBER_SAMPLE_SIZE, d.MEMBER_SAMPLE_SEED
    )
    member_presence(con, logs, cache / "transactions.parquet", cache / "member_presence.parquet")

    (cache / "logs_summary.json").write_text(json.dumps(summary, indent=2))
    return [
        "user_logs.parquet",
        "trial_window_logs.parquet",
        "daily_log_users.parquet",
        "member_presence.parquet",
        "logs_summary.json",
    ]
