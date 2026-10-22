"""Stage `completeness`: find days with missing log data, drop trials that touch them.

Reads (in cache_dir): daily_log_users.parquet and member_presence.parquet (stage
`logs`), trials.parquet (stage `trials`: msno, trial_start DATE).

A day is a gap day when either check fails:
    (a) no user at all has a log row that day;
    (b) the share of sampled paying members who logged (n_with_log / n_active)
        falls more than GAP_RELATIVE_DROP (relative) below its reference: the
        median share on the same weekday over the GAP_REFERENCE_WEEKS weeks
        before and after, not counting the day itself.

Days are checked over every date in the log range and in any trial window, so
a window running past the end of the logs counts as touching a gap.

Writes:
    gap_days.parquet             one row per calendar day with both checks.
    trial_completeness.parquet   msno, trial_start, touches_gap_day.
    completeness_summary.json    counts of gap days and affected trials.
"""

import json
from pathlib import Path

import duckdb

from src import definitions as d
from src.logs import sql_path


def gap_days(
    con: duckdb.DuckDBPyConnection,
    daily_users: Path,
    presence: Path,
    trials: Path,
    out: Path,
    window_days: int,
    reference_weeks: int,
    relative_drop: float,
) -> None:
    span = 7 * int(reference_weeks)
    con.execute(
        f"""
        copy (
            with bounds as (
                select least((select min(date) from read_parquet({sql_path(daily_users)})),
                             (select min(trial_start) from read_parquet({sql_path(trials)}))) as lo,
                       greatest((select max(date) from read_parquet({sql_path(daily_users)})),
                                (select max(trial_start) + {int(window_days) - 1} from read_parquet({sql_path(trials)}))) as hi
            ),
            calendar as (
                select unnest(generate_series(lo, hi, interval 1 day))::date as date from bounds
            ),
            days as (
                select c.date,
                       coalesce(u.n_users, 0) as n_users,
                       coalesce(p.n_active, 0) as n_active,
                       coalesce(p.n_with_log, 0) as n_with_log,
                       case when p.n_active > 0 then p.n_with_log / p.n_active end as share
                from calendar c
                left join read_parquet({sql_path(daily_users)}) u using (date)
                left join read_parquet({sql_path(presence)}) p using (date)
            ),
            referenced as (
                select *,
                       median(share) over (
                           partition by dayofweek(date) order by date
                           range between interval {span} days preceding and interval {span} days following
                           exclude current row
                       ) as reference
                from days
            )
            select *,
                   n_users = 0
                   or coalesce(share < (1 - {float(relative_drop)}) * reference, false) as is_gap
            from referenced
            order by date
        ) to {sql_path(out)} (format parquet)
        """
    )


def trial_flags(con: duckdb.DuckDBPyConnection, gaps: Path, trials: Path, out: Path, window_days: int) -> None:
    con.execute(
        f"""
        copy (
            select t.msno, t.trial_start,
                   exists (
                       select 1 from read_parquet({sql_path(gaps)}) g
                       where g.is_gap and g.date >= t.trial_start
                         and g.date < t.trial_start + {int(window_days)}
                   ) as touches_gap_day
            from (select distinct msno, trial_start from read_parquet({sql_path(trials)})) t
            order by t.msno, t.trial_start
        ) to {sql_path(out)} (format parquet)
        """
    )


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    con = duckdb.connect()
    gaps = cache / "gap_days.parquet"
    flags = cache / "trial_completeness.parquet"
    gap_days(
        con,
        cache / "daily_log_users.parquet",
        cache / "member_presence.parquet",
        cache / "trials.parquet",
        gaps,
        d.BEHAVIOR_WINDOW_DAYS,
        d.GAP_REFERENCE_WEEKS,
        d.GAP_RELATIVE_DROP,
    )
    trial_flags(con, gaps, cache / "trials.parquet", flags, d.BEHAVIOR_WINDOW_DAYS)

    n_days, n_gap = con.execute(f"select count(*), count(*) filter (where is_gap) from read_parquet({sql_path(gaps)})").fetchone()
    n_trials, n_touch = con.execute(
        f"select count(*), count(*) filter (where touches_gap_day) from read_parquet({sql_path(flags)})"
    ).fetchone()
    summary = {"calendar_days": n_days, "gap_days": n_gap, "trials": n_trials, "trials_touching_gap_day": n_touch}
    (cache / "completeness_summary.json").write_text(json.dumps(summary, indent=2))
    return ["gap_days.parquet", "trial_completeness.parquet", "completeness_summary.json"]
