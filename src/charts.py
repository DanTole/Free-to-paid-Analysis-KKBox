"""Stage `charts`: every number and chart the write-up publishes.

Reads the cached outputs of `trials`, `logs`, `completeness`, `features`,
`campaign_rates`, `model` and `bootstrap`. Writes, under cache_dir/publish/:

    published.json      every number the page shows (the page renders only this)
    split.png           who / what / unexplained shares with 95% intervals
    campaigns.png       raw vs audience-adjusted conversion per campaign
    activation.png      conversion by days with any listening, raw vs adjusted
    silence.png         days with any listening, best vs worst campaign

Publishing rule (design doc, Success Criteria): every published aggregate has at
least MIN_PUBLISHED_CELL trialists per cell. A smaller cell stops the stage.
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import definitions as d
from src import model

BLUE, ORANGE, GREY, DARK, RULE = "#3b6ea8", "#d1793b", "#9aa5b1", "#2b3138", "#5b6670"


def require_min_cells(counts, min_n: int, what: str) -> None:
    """Stop if any published cell has fewer than min_n trialists."""
    small = {str(k): int(v) for k, v in dict(counts).items() if v < min_n}
    if small:
        raise ValueError(f"{what}: cells below {min_n} trialists would be published: {small}")


def drag_level(data: pd.DataFrame, col: str, best: str, worst: str, min_n: int) -> dict:
    """The level of `col` most over-represented in the worst campaign vs the best."""
    pair = data[data["campaign"].isin([best, worst])]
    counts = pair.groupby(["campaign", col]).size().unstack(0, fill_value=0)
    share = counts / counts.sum()
    level = (share[worst] - share[best]).idxmax()
    require_min_cells(counts.loc[level], min_n, f"{col}={level}")
    return {
        "level": str(level),
        "share_best": float(share.loc[level, best]),
        "share_worst": float(share.loc[level, worst]),
        "conversion": float(data.loc[data[col] == level, "converted"].mean()),
        "conversion_other": float(data.loc[data[col] != level, "converted"].mean()),
    }


def build_numbers(cache: Path, min_n: int) -> dict:
    point = json.loads((cache / "model_decomposition.json").read_text())
    boot = json.loads((cache / "bootstrap_summary.json").read_text())
    funnel = json.loads((cache / "trial_funnel.json").read_text())
    logs = json.loads((cache / "logs_summary.json").read_text())
    gaps = json.loads((cache / "completeness_summary.json").read_text())
    rates = pd.read_parquet(cache / "campaign_rates.parquet")
    act = pd.read_parquet(cache / "model_activation.parquet")
    data, info = model.load_population(cache, d.MIN_CAMPAIGN_TRIALS, d.MIN_MIX_LEVEL_TRIALS)
    best, worst = point["best"], point["worst"]

    modeled = rates[rates["modeled"]]
    require_min_cells(modeled.set_index("campaign")["n"], min_n, "campaign table")
    require_min_cells(act.set_index("days_active")["n"], min_n, "activation table")

    pair = data[data["campaign"].isin([best, worst])]
    days_counts = pair.groupby(["campaign", "days_active_band"]).size().unstack(0, fill_value=0)
    require_min_cells(days_counts.stack(), min_n, "days-active table")
    days_share = days_counts / days_counts.sum()
    listeners = pair[pair["days_active"] > 0].groupby("campaign")

    ci = boot["ci95"]
    return {
        "best": best,
        "worst": worst,
        "best_rate": point["best_rate"],
        "worst_rate": point["worst_rate"],
        "gap": point["gap"],
        "split": {
            part: {"points": point[pts], "share": point[f"share_{part}"], "ci": ci[f"share_{part}"]}
            for part, pts in (("who", "phi_who"), ("what", "phi_what"), ("unexplained", "unexplained"))
        },
        "swaps": {"who_only": point["v_who"], "what_only": point["v_what"], "both": point["v_who_what"]},
        "population": {
            "log_rows": logs["log_rows"],
            "trial_rows": funnel["trial_rows"],
            "full_trials": funnel["full"],
            "no_member_row_excluded": funnel["eligible"] - funnel["has_member_row"],
            "in_trial_payers_excluded": funnel["in_trial_payers_excluded"],
            "modeled_trials": info["modeled_trials"],
            "campaigns_modeled": info["campaigns_modeled"],
            "gap_days": gaps["gap_days"],
            "bootstrap_replicates": boot["replicates"],
        },
        "campaigns": [
            {"campaign": r.campaign, "n": int(r.n), "raw": float(r.raw_rate), "adjusted": float(r.std_rate)}
            for r in modeled.itertuples()
        ],
        "activation": [
            {"days": int(r.days_active), "n": int(r.n), "raw": float(r.raw_rate), "adjusted": float(r.adjusted_rate)}
            for r in act.itertuples()
        ],
        "behavior": {
            "silent_best": float(days_share.loc["0", best]),
            "silent_worst": float(days_share.loc["0", worst]),
            "all_days_best": float(days_share.loc["5", best]),
            "all_days_worst": float(days_share.loc["5", worst]),
            "median_minutes_best": float(listeners.get_group(best)["listen_minutes"].median()),
            "median_minutes_worst": float(listeners.get_group(worst)["listen_minutes"].median()),
            "median_completion_best": float(listeners.get_group(best)["completion_rate"].median()),
            "median_completion_worst": float(listeners.get_group(worst)["completion_rate"].median()),
        },
        "days_share": {c: {k: float(v) for k, v in days_share[c].items()} for c in (best, worst)},
        "audience": {
            "registered_via": drag_level(data, "registered_via", best, worst, min_n),
            "city": drag_level(data, "city", best, worst, min_n),
        },
        "min_published_cell": min_n,
        "min_campaign_trials": d.MIN_CAMPAIGN_TRIALS,
    }


def _finish(fig, ax, path: Path) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def draw_split(n: dict, path: Path) -> None:
    parts = [("who", "Who each campaign reached", BLUE), ("what", "What trialists did, days 0-4", ORANGE),
             ("unexplained", "The campaign itself", GREY)]
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    y = np.arange(len(parts))[::-1]
    for yi, (key, label, color) in zip(y, parts):
        s = n["split"][key]
        lo, hi = s["ci"]
        ax.barh(yi, s["share"] * 100, color=color, height=0.62)
        ax.errorbar(s["share"] * 100, yi, xerr=[[(s["share"] - lo) * 100], [(hi - s["share"]) * 100]],
                    color=RULE, capsize=3, lw=1)
        x_text = max(s["share"], hi) * 100 + 2
        ax.text(x_text, yi, f"{s['share']:.0%}", va="center", fontsize=9, color=DARK)
    ax.axvline(0, color=RULE, lw=0.8)
    ax.set_yticks(y, [p[1] for p in parts])
    ax.set_xlim(-30, 110)
    ax.set_xlabel(f"Share of the {n['best']} vs {n['worst']} conversion gap (%), with 95% interval")
    ax.grid(axis="x", alpha=0.25)
    _finish(fig, ax, path)


def draw_campaigns(n: dict, path: Path) -> None:
    rows = n["campaigns"][::-1]
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    for yi, r in zip(y, rows):
        ax.plot([r["raw"] * 100, r["adjusted"] * 100], [yi, yi], color="#d5dae0", lw=2, zorder=1)
    ax.scatter([r["raw"] * 100 for r in rows], y, color=GREY, s=40, label="Raw", zorder=2)
    ax.scatter([r["adjusted"] * 100 for r in rows], y, color=BLUE, marker="D", s=36,
               label="Same audience (adjusted)", zorder=3)
    ax.set_yticks(y, [f"{r['campaign']}  (n={r['n']:,})" for r in rows])
    ax.set_xlabel("Converted to paid within 30 days of trial end (%)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=8, frameon=False)
    ax.grid(axis="x", alpha=0.25)
    _finish(fig, ax, path)


def draw_activation(n: dict, path: Path) -> None:
    rows = n["activation"]
    x = [r["days"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.plot(x, [r["raw"] * 100 for r in rows], "o-", color=GREY, label="Raw")
    ax.plot(x, [r["adjusted"] * 100 for r in rows], "D-", color=BLUE, label="Same audience (adjusted)")
    for r in rows:
        # Below the blue line where the raw line runs clearly above it; above it elsewhere.
        below = r["raw"] - r["adjusted"] > 0.005
        ax.annotate(f"{r['adjusted']:.1%}", (r["days"], r["adjusted"] * 100), textcoords="offset points",
                    xytext=(0, -15 if below else 8), ha="center", fontsize=8, color=DARK)
    ax.set_xticks(x)
    ax.set_xlabel("Days with any listening, in the first five trial days")
    ax.set_ylabel("Converted within 30 days (%)")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.25)
    _finish(fig, ax, path)


def draw_silence(n: dict, path: Path) -> None:
    levels = sorted(n["days_share"][n["best"]], key=int)
    x = np.arange(len(levels))
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    for offset, campaign, color in ((-0.2, n["best"], BLUE), (0.2, n["worst"], ORANGE)):
        ax.bar(x + offset, [n["days_share"][campaign][k] * 100 for k in levels], width=0.4, color=color,
               label=f"{campaign} ({'best' if campaign == n['best'] else 'worst'})")
    ax.set_xticks(x, levels)
    ax.set_xlabel("Days with any listening, in the first five trial days")
    ax.set_ylabel("Share of trialists (%)")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.25)
    _finish(fig, ax, path)


CHARTS = {"split.png": draw_split, "campaigns.png": draw_campaigns, "activation.png": draw_activation,
          "silence.png": draw_silence}


def run(ctx) -> list[str]:
    out = ctx.cache_dir / "publish"
    out.mkdir(exist_ok=True)
    numbers = build_numbers(ctx.cache_dir, d.MIN_PUBLISHED_CELL)
    (out / "published.json").write_text(json.dumps(numbers, indent=2))
    for name, draw in CHARTS.items():
        draw(numbers, out / name)
    return [f"publish/{name}" for name in ("published.json", *CHARTS)]
