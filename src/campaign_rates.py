"""Stage `campaign_rates`: raw vs audience-adjusted conversion per campaign.

Reads trials.parquet (stage `trials`) and mix.parquet (stage `mix`).

Population: the Full trials in campaigns with at least MIN_CAMPAIGN_TRIALS
trials. Smaller campaigns are listed with their counts but not modeled (tiny
cells make campaign effects unstable). Trials in a mix level with fewer than
MIN_MIX_LEVEL_TRIALS trials are dropped and reported, for the same reason.

Model: logistic regression, converted ~ campaign + registered_via + city +
age_band + gender + reg_lag_band. Mix-standardized conversion of campaign c is
the g-computation average: every modeled trialist is given campaign c,
predicted, and averaged, so each campaign is shown at the pooled audience mix.
A campaign-only model is fit as a cross-check of the campaign odds ratios.

Writes:
    campaign_rates.parquet    campaign, n, n_converted, raw_rate (+ Wilson 95%
                              interval), std_rate, modeled.
    campaign_rates_models.json  both models' campaign odds ratios, convergence,
                              pseudo R^2, best/worst campaigns and their gaps.
    campaign_rates.png        draft chart: raw vs standardized per campaign.
"""

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import statsmodels.formula.api as smf

from src import definitions as d

MIX_COLUMNS = ("registered_via", "city", "age_band", "gender", "reg_lag_band")
MIX_TERMS = " + ".join(f"C({c})" for c in MIX_COLUMNS)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return centre - half, centre + half


def require_converged(result, name: str) -> None:
    """Stop the stage rather than publish numbers from an unconverged fit."""
    if not result.mle_retvals.get("converged", False):
        raise RuntimeError(
            f"{name} logit did not converge ({result.mle_retvals.get('iterations')} iterations); "
            "look for a mix level with too few trials or one that never converts"
        )


def campaign_odds_ratios(result) -> dict[str, float]:
    prefix = "C(campaign)[T."
    return {k[len(prefix):-1]: math.exp(v) for k, v in result.params.items() if k.startswith(prefix)}


def drop_rare_levels(data: pd.DataFrame, min_level_trials: int) -> tuple[pd.DataFrame, dict]:
    """Drop trials in any mix level with fewer than min_level_trials trials."""
    keep = pd.Series(True, index=data.index)
    dropped = {}
    for col in MIX_COLUMNS:
        counts = data[col].value_counts()
        rare = counts[counts < min_level_trials]
        if len(rare):
            dropped[col] = {str(level): int(n) for level, n in rare.items()}
            keep &= ~data[col].isin(rare.index)
    return data[keep], dropped


def fit_campaign_rates(df: pd.DataFrame, min_trials: int, min_level_trials: int) -> tuple[pd.DataFrame, dict]:
    """df: one row per Full trial with campaign, converted and the mix columns."""
    df = df.assign(converted=df["converted"].astype(int))
    sizes = df.groupby("campaign").size()
    modeled = sorted(sizes[sizes >= min_trials].index)
    data, rare_dropped = drop_rare_levels(df[df["campaign"].isin(modeled)], min_level_trials)

    mix_fit = smf.logit(f"converted ~ C(campaign) + {MIX_TERMS}", data=data).fit(disp=0, maxiter=200)
    raw_fit = smf.logit("converted ~ C(campaign)", data=data).fit(disp=0, maxiter=200)
    require_converged(mix_fit, "mix")
    require_converged(raw_fit, "campaign-only")
    std ={c: float(mix_fit.predict(data.assign(campaign=c)).mean()) for c in modeled}

    rows = []
    for campaign, group in df.groupby("campaign"):
        n, k = len(group), int(group["converted"].sum())
        lo, hi = wilson(k, n)
        rows.append(
            {
                "campaign": campaign,
                "n": n,
                "n_converted": k,
                "raw_rate": k / n,
                "raw_lo": lo,
                "raw_hi": hi,
                "std_rate": std.get(campaign),
                "modeled": campaign in std,
            }
        )
    table = pd.DataFrame(rows).sort_values("campaign").reset_index(drop=True)

    m = table[table["modeled"]]
    best, worst = m.loc[m["raw_rate"].idxmax()], m.loc[m["raw_rate"].idxmin()]
    models = {
        "population_n": int(len(data)),
        "campaigns_modeled": modeled,
        "campaigns_not_modeled": {c: int(n) for c, n in sizes[sizes < min_trials].items()},
        "rare_level_trials_dropped": rare_dropped,
        "reference_campaign": modeled[0],
        "mix_model": {
            "converged": bool(mix_fit.mle_retvals["converged"]),
            "pseudo_r2": float(mix_fit.prsquared),
            "n_params": int(len(mix_fit.params)),
            "campaign_odds_ratios": campaign_odds_ratios(mix_fit),
        },
        "campaign_only_model": {
            "converged": bool(raw_fit.mle_retvals["converged"]),
            "campaign_odds_ratios": campaign_odds_ratios(raw_fit),
        },
        "best_campaign": best["campaign"],
        "worst_campaign": worst["campaign"],
        "raw_gap": float(best["raw_rate"] - worst["raw_rate"]),
        "std_gap": float(best["std_rate"] - worst["std_rate"]),
    }
    return table, models


def draw_campaign_chart(table: pd.DataFrame, path: Path) -> None:
    m = table[table["modeled"]].iloc[::-1]
    y = range(len(m))
    fig, ax = plt.subplots(figsize=(7, 0.45 * len(m) + 1.4))
    ax.hlines(y, m["raw_lo"] * 100, m["raw_hi"] * 100, color="#9aa5b1", lw=2)
    ax.plot(m["raw_rate"] * 100, y, "o", color="#3b6ea8", label="Raw")
    ax.plot(m["std_rate"] * 100, y, "D", color="#d1793b", label="At pooled audience mix")
    ax.set_yticks(list(y), [f"{c}  (n={n:,})" for c, n in zip(m["campaign"], m["n"])])
    ax.set_xlabel("Converted to paid within 30 days of trial end (%)")
    ax.set_title("Trial conversion by campaign, raw vs mix-adjusted", fontsize=10)
    ax.grid(axis="x", alpha=0.3)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    trials = pd.read_parquet(cache / "trials.parquet")
    mix = pd.read_parquet(cache / "mix.parquet")
    df = trials.merge(mix, on=["msno", "trial_start"], how="left", validate="one_to_one")
    table, models = fit_campaign_rates(df, d.MIN_CAMPAIGN_TRIALS, d.MIN_MIX_LEVEL_TRIALS)
    table.to_parquet(cache / "campaign_rates.parquet")
    (cache / "campaign_rates_models.json").write_text(json.dumps(models, indent=2))
    draw_campaign_chart(table, cache / "campaign_rates.png")
    return ["campaign_rates.parquet", "campaign_rates_models.json", "campaign_rates.png"]
