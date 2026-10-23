"""Stage `model`: split the best-vs-worst campaign gap into who / what / unexplained.

Reads trials, mix and features (stages `trials`, `mix`, `features`).

Population: Common trials (window touches no gap day) in campaigns with at
least MIN_CAMPAIGN_TRIALS Common trials; trials in any audience or behavior
level with fewer than MIN_MIX_LEVEL_TRIALS trials are dropped and reported.

Model: logit, converted ~ campaign + audience (who) + behavior (what), all
categorical. Best and worst campaigns are picked once on raw conversion.

Value function (design doc, Method), on the probability scale, with the worst
campaign's own effect kept in every counterfactual. Because every term is
categorical, a trialist's log-odds is campaign + audience part + behavior part,
so a counterfactual only swaps parts:
    v(who, what)  each worst-campaign trialist takes a random best-campaign
                  trialist's audience and behavior
    v(who)        takes a best-campaign donor's audience, plus the behavior of
                  a random worst-campaign trialist in the donor's matching cell
                  (behavior given audience stays the worst campaign's)
    v(what)       keeps their own audience and takes the behavior of a random
                  best-campaign trialist in their own matching cell
Matching cell = registered_via (top MATCH_TOP_REGISTERED_VIA, else "other") x
age band; an empty cell falls back to registered_via alone, then to anyone.
Shapley over the two blocks; unexplained = gap - v(who, what).

Also: audience-adjusted conversion by days active (the activation curve).

Writes model_decomposition.json, model_coefficients.parquet,
model_activation.parquet, model_activation.png.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from src import definitions as d
from src import campaign_rates

MIX = campaign_rates.MIX_COLUMNS
BEHAVIOR = (
    "days_active_band",
    "minutes_band",
    "completion_band",
    "skip_band",
    "unique_band",
    "first_day_band",
)
FORMULA = "converted ~ C(campaign) + " + " + ".join(f"C({c})" for c in MIX + BEHAVIOR)
ACTIVATION_FORMULA = "converted ~ C(campaign) + " + " + ".join(f"C({c})" for c in MIX) + " + C(days_active_band)"


def load_population(cache: Path, min_trials: int, min_level_trials: int) -> tuple[pd.DataFrame, dict]:
    feats = pd.read_parquet(cache / "features.parquet")
    mix = pd.read_parquet(cache / "mix.parquet")
    df = feats.merge(mix, on=["msno", "trial_start"], how="left", validate="one_to_one")
    df = df[df["in_common"]].assign(converted=lambda x: x["converted"].astype(int))
    sizes = df.groupby("campaign").size()
    modeled = sorted(sizes[sizes >= min_trials].index)
    data = df[df["campaign"].isin(modeled)]
    keep = pd.Series(True, index=data.index)
    dropped = {}
    for col in MIX + BEHAVIOR:
        counts = data[col].value_counts()
        rare = counts[counts < min_level_trials]
        if len(rare):
            dropped[col] = {str(k): int(v) for k, v in rare.items()}
            keep &= ~data[col].isin(rare.index)
    data = data[keep].reset_index(drop=True)
    info = {
        "common_trials": int(len(df)),
        "modeled_trials": int(len(data)),
        "campaigns_modeled": modeled,
        "campaigns_not_modeled": {c: int(n) for c, n in sizes[sizes < min_trials].items()},
        "rare_level_trials_dropped": dropped,
    }
    return data, info


def fit(frame: pd.DataFrame, formula: str = FORMULA):
    res = smf.logit(formula, data=frame).fit(disp=0, maxiter=200)
    campaign_rates.require_converged(res, "decomposition")
    return res


def best_worst(frame: pd.DataFrame) -> tuple[str, str, float]:
    rates = frame.groupby("campaign")["converted"].mean()
    best, worst = rates.idxmax(), rates.idxmin()
    return best, worst, float(rates[best] - rates[worst])


def block_part(res, frame: pd.DataFrame, cols) -> np.ndarray:
    """Each row's summed coefficients for `cols` (reference levels count 0)."""
    total = np.zeros(len(frame))
    for col in cols:
        prefix = f"C({col})[T."
        eff = {k[len(prefix):-1]: v for k, v in res.params.items() if k.startswith(prefix)}
        total += frame[col].map(eff).fillna(0.0).to_numpy()
    return total


def matching_keys(frame: pd.DataFrame, top_via: int) -> tuple[np.ndarray, np.ndarray]:
    top = frame["registered_via"].value_counts().index[:top_via]
    via = frame["registered_via"].where(frame["registered_via"].isin(top), "other")
    return (via + "|" + frame["age_band"]).to_numpy(), via.to_numpy()


def draw_matched(rng, target_cell, target_via, pool, pool_cell, pool_via) -> tuple[np.ndarray, dict]:
    """For each target, a random pool row in the same matching cell; if the cell
    is empty in the pool, same registered_via; if that is empty too, anyone.
    Returns pool row positions and how many draws used each level."""
    out = np.empty(len(target_cell), dtype=int)
    used = {"cell": 0, "registered_via": 0, "anyone": 0}
    for key in np.unique(target_cell):
        where = np.flatnonzero(target_cell == key)
        candidates = pool[pool_cell == key]
        level = "cell"
        if len(candidates) == 0:
            candidates = pool[pool_via == target_via[where[0]]]
            level = "registered_via"
        if len(candidates) == 0:
            candidates = pool
            level = "anyone"
        out[where] = rng.choice(candidates, size=len(where), replace=True)
        used[level] += len(where)
    return out, used


def decompose(res, frame: pd.DataFrame, best: str, worst: str, rng, top_via: int) -> dict:
    sig = lambda x: 1 / (1 + np.exp(-x))
    eta = res.fittedvalues.to_numpy()
    if len(eta) != len(frame):
        raise ValueError(f"model dropped rows ({len(eta)} fitted vs {len(frame)}); check for missing values")
    who = block_part(res, frame, MIX)
    what = block_part(res, frame, BEHAVIOR)
    cell, via = matching_keys(frame, top_via)
    campaign = frame["campaign"].to_numpy()
    W = np.flatnonzero(campaign == worst)
    B = np.flatnonzero(campaign == best)

    donor = rng.choice(B, size=len(W), replace=True)
    same_cell_worst, used_who = draw_matched(rng, cell[donor], via[donor], W, cell[W], via[W])
    same_cell_best, used_what = draw_matched(rng, cell[W], via[W], B, cell[B], via[B])

    base = sig(eta[W]).mean()
    v_both = sig(eta[W] + who[donor] - who[W] + what[donor] - what[W]).mean() - base
    v_who = sig(eta[W] + who[donor] - who[W] + what[same_cell_worst] - what[W]).mean() - base
    v_what = sig(eta[W] + what[same_cell_best] - what[W]).mean() - base

    rates = frame.groupby("campaign")["converted"].mean()
    gap = float(rates[best] - rates[worst])
    phi_who = 0.5 * (v_who + v_both - v_what)
    phi_what = 0.5 * (v_what + v_both - v_who)
    n_draws = len(W)
    return {
        "best": best,
        "worst": worst,
        "best_rate": float(rates[best]),
        "worst_rate": float(rates[worst]),
        "gap": gap,
        "v_who_what": float(v_both),
        "v_who": float(v_who),
        "v_what": float(v_what),
        "phi_who": float(phi_who),
        "phi_what": float(phi_what),
        "unexplained": float(gap - v_both),
        "share_who": float(phi_who / gap),
        "share_what": float(phi_what / gap),
        "share_unexplained": float((gap - v_both) / gap),
        "matching_fallback_share": {
            "who_draws": {k: v / n_draws for k, v in used_who.items()},
            "what_draws": {k: v / n_draws for k, v in used_what.items()},
        },
    }


def activation(frame: pd.DataFrame) -> pd.DataFrame:
    """Raw and audience-adjusted conversion by days active (g-computation)."""
    res = fit(frame, ACTIVATION_FORMULA)
    rows = []
    for level, group in frame.groupby("days_active_band"):
        rows.append(
            {
                "days_active": level,
                "n": len(group),
                "raw_rate": group["converted"].mean(),
                "adjusted_rate": float(res.predict(frame.assign(days_active_band=level)).mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("days_active").reset_index(drop=True)


def coefficients(res) -> pd.DataFrame:
    ci = res.conf_int()
    return pd.DataFrame(
        {"term": res.params.index, "coef": res.params.values, "lo": ci[0].values, "hi": ci[1].values,
         "odds_ratio": np.exp(res.params.values)}
    )


def draw_activation(act: pd.DataFrame, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(6.5, 3.4))
    x = act["days_active"].astype(int)
    ax.plot(x, act["raw_rate"] * 100, "o-", color="#9aa5b1", label="Raw")
    ax.plot(x, act["adjusted_rate"] * 100, "D-", color="#3b6ea8", label="Audience-adjusted")
    for xi, yi in zip(x, act["adjusted_rate"] * 100):
        ax.annotate(f"{yi:.1f}%", (xi, yi), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    ax.set_xlabel("Days with any listening, trial days 0-4")
    ax.set_ylabel("Converted within 30 days (%)")
    ax.set_title("Draft: conversion by trial-week activity", fontsize=10)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    data, info = load_population(cache, d.MIN_CAMPAIGN_TRIALS, d.MIN_MIX_LEVEL_TRIALS)
    res = fit(data)
    best, worst, _ = best_worst(data)
    result = decompose(res, data, best, worst, np.random.default_rng(d.DECOMPOSITION_SEED), d.MATCH_TOP_REGISTERED_VIA)
    result |= info | {"pseudo_r2": float(res.prsquared), "n_params": int(len(res.params))}
    (cache / "model_decomposition.json").write_text(json.dumps(result, indent=2))
    coefficients(res).to_parquet(cache / "model_coefficients.parquet")
    act = activation(data)
    act.to_parquet(cache / "model_activation.parquet")
    draw_activation(act, cache / "model_activation.png")
    return ["model_decomposition.json", "model_coefficients.parquet", "model_activation.parquet", "model_activation.png"]
