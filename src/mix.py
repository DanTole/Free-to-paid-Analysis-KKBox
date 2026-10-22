"""Stage `mix`: who each trialist is (the "who" block of the decomposition).

Reads trials.parquet (stage `trials`) and members.parquet (stage `ingest`).
Writes mix.parquet, one row per trial:

    registered_via  signup channel, "unknown" when missing
    city            the TOP_CITIES cities with the most trials, else "other"
                    ("unknown" when missing)
    age_band        from bd; outside [MIN_VALID_AGE, MAX_VALID_AGE] or missing
                    -> "unknown"; bands from AGE_BAND_UPPER_BOUNDS
    gender          "male" / "female" / "unknown"
    reg_lag_band    days from registration to trial start, bands from
                    REG_LAG_BAND_LOWER_BOUNDS_DAYS; no registration date or
                    registration after the trial -> "missing"

Users with no member row get "unknown"/"missing" everywhere. Counts go to
mix_summary.json.
"""

import json
from pathlib import Path

import duckdb

from src import definitions as d
from src.logs import sql_path


def band_case(expr: str, edges: tuple[int, ...], *, upper: bool) -> str:
    """SQL CASE mapping `expr` to band labels.

    upper=True: edges are inclusive upper bounds, e.g. (24, 34) -> "<=24",
    "25-34", "35+". upper=False: edges are lower bounds, e.g. (0, 61) ->
    "0-60", "61+". Values outside every band fall through to NULL.
    """
    whens = []
    if upper:
        prev = None
        for edge in edges:
            label = f"<={edge}" if prev is None else f"{prev + 1}-{edge}"
            whens.append(f"when {expr} <= {int(edge)} then '{label}'")
            prev = edge
        whens.append(f"when {expr} > {int(edges[-1])} then '{edges[-1] + 1}+'")
    else:
        for lo, hi in zip(edges, edges[1:]):
            whens.append(f"when {expr} >= {int(lo)} and {expr} < {int(hi)} then '{lo}-{hi - 1}'")
        whens.append(f"when {expr} >= {int(edges[-1])} then '{edges[-1]}+'")
    return "case " + " ".join(whens) + " end"


def build_mix(
    con: duckdb.DuckDBPyConnection,
    trials: Path,
    members: Path,
    out: Path,
    *,
    top_cities: int,
    min_age: int,
    max_age: int,
    age_upper_bounds: tuple[int, ...],
    lag_lower_bounds: tuple[int, ...],
) -> dict:
    age = band_case("m.bd", age_upper_bounds, upper=True)
    lag = band_case("datediff('day', m.registration_init_time, t.trial_start)", lag_lower_bounds, upper=False)
    con.execute(
        f"""
        copy (
            with t as (select msno, trial_start from read_parquet({sql_path(trials)})),
            m as (
                select msno, any_value(city) as city, any_value(bd) as bd, any_value(gender) as gender,
                       any_value(registered_via) as registered_via,
                       any_value(registration_init_time) as registration_init_time
                from read_parquet({sql_path(members)}) group by msno
            ),
            top as (
                select m.city from t join m using (msno) where m.city is not null
                group by m.city order by count(*) desc, m.city limit {int(top_cities)}
            )
            select t.msno, t.trial_start,
                   coalesce(m.registered_via::varchar, 'unknown') as registered_via,
                   case when m.city is null then 'unknown'
                        when m.city in (select city from top) then m.city::varchar
                        else 'other' end as city,
                   case when m.bd between {int(min_age)} and {int(max_age)} then {age}
                        else 'unknown' end as age_band,
                   case when lower(m.gender) in ('male', 'female') then lower(m.gender)
                        else 'unknown' end as gender,
                   coalesce({lag}, 'missing') as reg_lag_band,
                   m.msno is null as no_member_row
            from t left join m using (msno)
            order by t.trial_start, t.msno
        ) to {sql_path(out)} (format parquet)
        """
    )
    rows, no_member, member_dupes = con.execute(
        f"""
        select count(*), count(*) filter (where no_member_row),
               (select count(*) - count(distinct msno) from read_parquet({sql_path(members)}))
        from read_parquet({sql_path(out)})
        """
    ).fetchone()
    return {"trials": rows, "no_member_row": no_member, "duplicate_member_rows": member_dupes}


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    summary = build_mix(
        duckdb.connect(),
        cache / "trials.parquet",
        cache / "members.parquet",
        cache / "mix.parquet",
        top_cities=d.TOP_CITIES,
        min_age=d.MIN_VALID_AGE,
        max_age=d.MAX_VALID_AGE,
        age_upper_bounds=d.AGE_BAND_UPPER_BOUNDS,
        lag_lower_bounds=d.REG_LAG_BAND_LOWER_BOUNDS_DAYS,
    )
    (cache / "mix_summary.json").write_text(json.dumps(summary, indent=2))
    return ["mix.parquet", "mix_summary.json"]
