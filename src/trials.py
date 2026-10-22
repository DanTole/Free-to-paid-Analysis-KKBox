"""Stage `trials`: the new-user free-trial population and its conversion label.

Reads transactions.parquet and members.parquet (stage `ingest`). Funnel, each
step counted in trial_funnel.json:

    trial_rows        payment_plan_days 7, actual_amount_paid 0, not cancelled
    trials            distinct (msno, trial_start); same-day duplicates keep the
                      latest expiry and are counted
    first_transaction the trial is the user's first transaction in the files
    after_burn_in     trial_start >= BURN_IN_START
    expiry_in_range   expiry in [start + MIN, start + MAX] days
    eligible          expiry + CONVERSION_WINDOW_DAYS <= FOLLOWUP_END
    has_member_row    the user has a row in members.parquet (REQUIRE_MEMBER_ROW)
    full              minus in-trial payers

Trial end = expiry (membership_expire_date of the trial row).
In-trial payer: a paid, non-cancel transaction in [trial_start, trial_end).
Converted: a paid, non-cancel transaction in [trial_end, trial_end + window].

Writes:
    trials.parquet           Full population: msno, trial_start, trial_end,
                             campaign (YYYY-MM of trial_start), converted.
    trial_campaigns.parquet  per campaign: n_eligible, n_no_member_row,
                             n_in_trial_payers, n_full, n_converted.
    trial_funnel.json        the step counts above.
"""

import json
from datetime import date
from pathlib import Path

import duckdb

from src import definitions as d
from src.logs import sql_path

FUNNEL_STEPS = (
    "trial_rows",
    "trials",
    "first_transaction",
    "after_burn_in",
    "expiry_in_range",
    "eligible",
    "has_member_row",
    "full",
)


def build_trials(
    con: duckdb.DuckDBPyConnection,
    transactions: Path,
    members: Path,
    out_trials: Path,
    out_campaigns: Path,
    *,
    plan_days: int,
    amount_paid: int,
    expire_min_days: int,
    expire_max_days: int,
    burn_in_start: date,
    window_days: int,
    followup_end: date,
    require_member_row: bool,
) -> dict:
    tx = f"read_parquet({sql_path(transactions)})"
    con.execute(
        f"""
        create or replace temp table candidates as
        with trial_rows as (
            select msno, transaction_date as trial_start, membership_expire_date as expiry
            from {tx}
            where payment_plan_days = {int(plan_days)}
              and actual_amount_paid = {int(amount_paid)}
              and is_cancel = 0
        ),
        trials as (
            select msno, trial_start, max(expiry) as trial_end,
                   count(distinct expiry) > 1 as conflicting_expiry
            from trial_rows group by msno, trial_start
        ),
        first_tx as (select msno, min(transaction_date) as first_date from {tx} group by msno),
        paid as (
            select msno, transaction_date as paid_date from {tx}
            where actual_amount_paid > 0 and is_cancel = 0
        ),
        labels as (
            select t.msno, t.trial_start,
                   bool_or(p.paid_date >= t.trial_start and p.paid_date < t.trial_end) as in_trial_payer,
                   bool_or(p.paid_date >= t.trial_end
                           and p.paid_date <= t.trial_end + {int(window_days)}) as converted
            from trials t join paid p using (msno)
            group by all
        )
        select t.*,
               t.trial_start = f.first_date as first_transaction,
               t.trial_start >= date '{burn_in_start.isoformat()}' as after_burn_in,
               datediff('day', t.trial_start, t.trial_end)
                   between {int(expire_min_days)} and {int(expire_max_days)} as expiry_in_range,
               t.trial_end + {int(window_days)} <= date '{followup_end.isoformat()}' as within_followup,
               coalesce(l.in_trial_payer, false) as in_trial_payer,
               coalesce(l.converted, false) as converted,
               m.msno is not null as has_member_row,
               strftime(t.trial_start, '%Y-%m') as campaign
        from trials t
        join first_tx f using (msno)
        left join labels l using (msno, trial_start)
        left join (select distinct msno from read_parquet({sql_path(members)})) m using (msno)
        """
    )
    con.execute(
        f"""
        create or replace temp view steps as
        select *,
               first_transaction as s_first,
               first_transaction and after_burn_in as s_burn,
               first_transaction and after_burn_in and coalesce(expiry_in_range, false) as s_expiry,
               first_transaction and after_burn_in and coalesce(expiry_in_range, false)
                   and coalesce(within_followup, false) as s_eligible,
               first_transaction and after_burn_in and coalesce(expiry_in_range, false)
                   and coalesce(within_followup, false)
                   and (has_member_row or not {bool(require_member_row)}) as s_member
        from candidates
        """
    )
    con.execute(
        f"""
        copy (
            select msno, trial_start, trial_end, campaign, converted
            from steps where s_member and not in_trial_payer
            order by trial_start, msno
        ) to {sql_path(out_trials)} (format parquet)
        """
    )
    con.execute(
        f"""
        copy (
            select campaign,
                   count(*) as n_eligible,
                   count(*) filter (where not s_member) as n_no_member_row,
                   count(*) filter (where s_member and in_trial_payer) as n_in_trial_payers,
                   count(*) filter (where s_member and not in_trial_payer) as n_full,
                   count(*) filter (where s_member and not in_trial_payer and converted) as n_converted
            from steps where s_eligible
            group by campaign order by campaign
        ) to {sql_path(out_campaigns)} (format parquet)
        """
    )
    trial_rows = con.execute(
        f"select count(*) from {tx} where payment_plan_days = {int(plan_days)} "
        f"and actual_amount_paid = {int(amount_paid)} and is_cancel = 0"
    ).fetchone()[0]
    counts = con.execute(
        """
        select count(*),
               count(*) filter (where s_first),
               count(*) filter (where s_burn),
               count(*) filter (where s_expiry),
               count(*) filter (where s_eligible),
               count(*) filter (where s_member),
               count(*) filter (where s_member and not in_trial_payer),
               count(*) filter (where conflicting_expiry),
               count(*) filter (where s_member and in_trial_payer)
        from steps
        """
    ).fetchone()
    funnel = dict(zip(FUNNEL_STEPS, (trial_rows, *counts[:7])))
    funnel["same_day_trials_with_conflicting_expiry"] = counts[7]
    funnel["in_trial_payers_excluded"] = counts[8]
    return funnel


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    funnel = build_trials(
        duckdb.connect(),
        cache / "transactions.parquet",
        cache / "members.parquet",
        cache / "trials.parquet",
        cache / "trial_campaigns.parquet",
        plan_days=d.TRIAL_PLAN_DAYS,
        amount_paid=d.TRIAL_AMOUNT_PAID,
        expire_min_days=d.TRIAL_EXPIRE_MIN_OFFSET_DAYS,
        expire_max_days=d.TRIAL_EXPIRE_MAX_OFFSET_DAYS,
        burn_in_start=d.BURN_IN_START,
        window_days=d.CONVERSION_WINDOW_DAYS,
        followup_end=d.FOLLOWUP_END,
        require_member_row=d.REQUIRE_MEMBER_ROW,
    )
    (cache / "trial_funnel.json").write_text(json.dumps(funnel, indent=2))
    return ["trials.parquet", "trial_campaigns.parquet", "trial_funnel.json"]
