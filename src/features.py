"""Stage `features`: what each trialist did during trial days 0..BEHAVIOR_WINDOW_DAYS-1.

Reads (in cache_dir): trials.parquet (stage `trials`), trial_window_logs.parquet
(stage `logs`, already limited to the behavior window and summed per user-day),
trial_completeness.parquet (stage `completeness`).

Writes features.parquet, one row per Full trial:

    days_active         days with any log row (0..window)
    plays               num_25 + num_50 + num_75 + num_985 + num_100
    listen_minutes      valid total_secs / 60 (impossible values were nulled
                        upstream, eng review D7)
    completion_rate     num_100 / plays; NULL when plays == 0
    early_skip_share    num_25 / plays; NULL when plays == 0
    daily_unique_songs  num_unq summed over days (a song heard on two days
                        counts twice; the logs only give per-day uniques)
    first_listen_day    first day_index with a log row; NULL when none
    has_bad_secs        any nulled total_secs row in the window
    in_common           the trial's window touches no gap day (Common population)

A trialist with no log rows in the window has days_active = plays = 0: no
listening, not missing data, because gap days are already handled by in_common.

Model-ready bands (user-approved 2026-10-23), all strings that sort in band order:
    days_active_band    "0".."5"
    minutes_band, completion_band, skip_band, unique_band
                        "1:<a", "2:a-b", ... "5:d+" from the edges in definitions;
                        silent trialists get band 1 (days_active_band "0" carries
                        silence)
    first_day_band      "d0".."d4"; silent trialists get "d0"

Also writes features_summary.json.
"""

import json
from pathlib import Path

import duckdb

from src import definitions as d
from src.logs import sql_path


def edge_band(expr: str, edges: tuple[float, ...]) -> str:
    """SQL CASE: expr below edges[0] -> '1:<e0', then '2:e0-e1', ..., last '5:e3+'.
    NULL (no listening) -> band 1."""
    fmt = lambda e: f"{e:g}"
    whens = [f"when {expr} is null or {expr} < {edges[0]} then '1:<{fmt(edges[0])}'"]
    for i, (lo, hi) in enumerate(zip(edges, edges[1:]), start=2):
        whens.append(f"when {expr} < {hi} then '{i}:{fmt(lo)}-{fmt(hi)}'")
    whens.append(f"else '{len(edges) + 1}:{fmt(edges[-1])}+'")
    return "case " + " ".join(whens) + " end"


def build_features(
    con: duckdb.DuckDBPyConnection,
    trials: Path,
    window_logs: Path,
    completeness: Path,
    out: Path,
    *,
    minutes_edges: tuple[float, ...] = d.LISTEN_MINUTES_EDGES,
    completion_edges: tuple[float, ...] = d.COMPLETION_RATE_EDGES,
    skip_edges: tuple[float, ...] = d.EARLY_SKIP_SHARE_EDGES,
    unique_edges: tuple[float, ...] = d.UNIQUE_SONGS_EDGES,
) -> dict:
    con.execute(
        f"""
        copy (
            with w as (
                select msno, trial_start,
                       count(*) as days_active,
                       sum(num_25 + num_50 + num_75 + num_985 + num_100) as plays,
                       sum(num_25) as num_25,
                       sum(num_100) as num_100,
                       coalesce(sum(total_secs), 0) / 60 as listen_minutes,
                       sum(num_unq) as daily_unique_songs,
                       min(day_index) as first_listen_day,
                       bool_or(bad_secs_rows > 0) as has_bad_secs
                from read_parquet({sql_path(window_logs)})
                group by msno, trial_start
            ),
            raw as (
                select t.msno, t.trial_start, t.campaign, t.converted,
                       coalesce(w.days_active, 0) as days_active,
                       coalesce(w.plays, 0) as plays,
                       coalesce(w.listen_minutes, 0) as listen_minutes,
                       case when w.plays > 0 then w.num_100 / w.plays end as completion_rate,
                       case when w.plays > 0 then w.num_25 / w.plays end as early_skip_share,
                       coalesce(w.daily_unique_songs, 0) as daily_unique_songs,
                       w.first_listen_day,
                       coalesce(w.has_bad_secs, false) as has_bad_secs,
                       not c.touches_gap_day as in_common
                from read_parquet({sql_path(trials)}) t
                left join w using (msno, trial_start)
                join read_parquet({sql_path(completeness)}) c using (msno, trial_start)
            )
            select *,
                   days_active::varchar as days_active_band,
                   case when days_active = 0 then '1:<{minutes_edges[0]:g}'
                        else {edge_band("listen_minutes", minutes_edges)} end as minutes_band,
                   {edge_band("completion_rate", completion_edges)} as completion_band,
                   {edge_band("early_skip_share", skip_edges)} as skip_band,
                   case when days_active = 0 then '1:<{unique_edges[0]:g}'
                        else {edge_band("daily_unique_songs", unique_edges)} end as unique_band,
                   'd' || coalesce(first_listen_day, 0)::varchar as first_day_band
            from raw
            order by trial_start, msno
        ) to {sql_path(out)} (format parquet)
        """
    )
    n, common, no_listen, bad = con.execute(
        f"""
        select count(*), count(*) filter (where in_common),
               count(*) filter (where days_active = 0), count(*) filter (where has_bad_secs)
        from read_parquet({sql_path(out)})
        """
    ).fetchone()
    return {"trials": n, "in_common": common, "no_listening_in_window": no_listen, "has_bad_secs": bad}


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    summary = build_features(
        duckdb.connect(),
        cache / "trials.parquet",
        cache / "trial_window_logs.parquet",
        cache / "trial_completeness.parquet",
        cache / "features.parquet",
    )
    (cache / "features_summary.json").write_text(json.dumps(summary, indent=2))
    return ["features.parquet", "features_summary.json"]
