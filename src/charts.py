"""Stage `charts`: every number and chart the write-up publishes.

Reads the cached outputs of `trials`, `logs`, `completeness`, `features`,
`campaign_rates`, `model` and `bootstrap`. Writes, under cache_dir/publish/:

    published.json      every number the page shows (the page renders only this)
    split.png           who / what / unexplained shares with 95% intervals
    campaigns.png       raw vs audience-adjusted conversion per campaign
    activation.png      conversion by days with any listening, raw vs adjusted
    silence.png         days with any listening, best vs worst campaign
    funnel.png          from all free trials to the trials analyzed
    audience.png        signup channel and city mix, best vs worst campaign
    traits.png          which audience traits carry the "who" effect
    listening.png       how much and how listeners listened, worst vs best

Colors: blue and orange mean who and what; best and worst campaigns get their
own pair, so no color means two things.

Publishing rule (design doc, Success Criteria): every published aggregate has at
least MIN_PUBLISHED_CELL trialists per cell. A smaller cell stops the stage.
"""

import json
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import definitions as d
from src import model

BLUE, ORANGE, GREY, DARK, RULE = "#3b6ea8", "#d1793b", "#9aa5b1", "#2b3138", "#5b6670"
BEST, WORST = "#33475b", "#c2504a"
# Signup channels under this share of both campaigns are drawn as one "other" group.
SMALL_GROUP_SHARE = 0.05


def month(campaign: str) -> str:
    """'2016-06' -> 'Jun 2016'."""
    return datetime.strptime(campaign, "%Y-%m").strftime("%b %Y")


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

    via_counts = pair.groupby(["campaign", "registered_via"]).size().unstack(0, fill_value=0)
    require_min_cells(via_counts.stack(), min_n, "signup-channel table")
    tiny = via_counts.index[(via_counts / via_counts.sum()).max(axis=1) < SMALL_GROUP_SHARE]
    merged = lambda col: col.where(~col.isin(tiny), "other")
    via_share = pair.assign(v=merged(pair["registered_via"])).groupby(["campaign", "v"]).size().unstack(0, fill_value=0)
    via_share = via_share / via_share.sum()
    via_rate = data.assign(v=merged(data["registered_via"])).groupby("v")["converted"].mean()
    worst_n = int((data["campaign"] == worst).sum())

    medians = {}
    for col, key in (("listen_minutes", "minutes"), ("daily_unique_songs", "songs"),
                     ("completion_rate", "completion"), ("early_skip_share", "early_skips")):
        medians[key] = {c: float(listeners.get_group(c)[col].median()) for c in (best, worst)}

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
        "channels": sorted(
            ({"channel": str(v), "share_best": float(via_share.loc[v, best]),
              "share_worst": float(via_share.loc[v, worst]), "conversion": float(via_rate[v])}
             for v in via_share.index),
            key=lambda r: r["conversion"],
        ),
        "trait_share": [
            {"trait": g, "share": float(v), "ci": ci[f"trait:{g}"]} for g, v in point["trait_share"].items()
        ],
        "next_best_pair": point["next_best_pair"],
        "listening_medians": medians,
        "funnel": [
            ["Free-trial transactions", funnel["trial_rows"]],
            ["First transaction of a new user", funnel["first_transaction"]],
            ["Trial length 5-31 days, from 2015-04", funnel["expiry_in_range"]],
            ["Full 30-day follow-up, member profile", funnel["has_member_row"]],
            ["Didn't pay during the trial", funnel["full"]],
            [f"In the {len(info['campaigns_modeled'])} largest campaigns", info["modeled_trials"]],
        ],
        "worst_trials": worst_n,
        "gap_in_subscribers": round(worst_n * point["gap"]),
        "min_published_cell": min_n,
        "min_campaign_trials": d.MIN_CAMPAIGN_TRIALS,
    }


def _finish(fig, ax, path: Path) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def draw_split(n: dict, path: Path) -> None:
    parts = [("who", "Who each campaign reached", BLUE), ("what", "What trialists did, first five days", ORANGE),
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
        label = f"{s['share']:.0%}".replace("-", "−")
        if key == "unexplained" and lo <= 0 <= hi:
            label += "   range crosses 0, so read as ≈ 0"
        ax.text(x_text, yi, label, va="center", fontsize=9, color=DARK)
    ax.axvline(0, color=RULE, lw=0.8)
    ax.set_yticks(y, [p[1] for p in parts])
    ax.set_xlim(-30, 110)
    ax.set_xlabel("Share of the best-vs-worst gap (%), with 95% range")
    ax.grid(axis="x", alpha=0.25)
    _finish(fig, ax, path)


def draw_campaigns(n: dict, path: Path) -> None:
    rows = n["campaigns"][::-1]
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7.2, 3.5))
    for yi, r in zip(y, rows):
        ax.plot([r["raw"] * 100, r["adjusted"] * 100], [yi, yi], color="#d5dae0", lw=2, zorder=1)
    ax.scatter([r["raw"] * 100 for r in rows], y, color=GREY, s=40, label="Actual", zorder=2)
    ax.scatter([r["adjusted"] * 100 for r in rows], y, color=BLUE, marker="D", s=36,
               label="With the average mix of people", zorder=3)
    tag = {n["best"]: "  best", n["worst"]: "  worst"}
    ax.set_yticks(y, [f"{month(r['campaign'])}{tag.get(r['campaign'], '')}  (n={r['n']:,})" for r in rows])
    ax.set_xlabel("Converted to paid within 30 days of trial end (%)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=8, frameon=False)
    ax.grid(axis="x", alpha=0.25)
    _finish(fig, ax, path)


def draw_activation(n: dict, path: Path) -> None:
    rows = n["activation"]
    x = [r["days"] for r in rows]
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.plot(x, [r["raw"] * 100 for r in rows], "o-", color=GREY, label="Actual")
    ax.plot(x, [r["adjusted"] * 100 for r in rows], "D-", color=BLUE, label="Same mix of people at every point")
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
    for offset, campaign, color in ((-0.2, n["best"], BEST), (0.2, n["worst"], WORST)):
        ax.bar(x + offset, [n["days_share"][campaign][k] * 100 for k in levels], width=0.4, color=color,
               label=f"{'Best' if campaign == n['best'] else 'Worst'} campaign ({month(campaign)})")
    ax.set_xticks(x, levels)
    ax.set_xlabel("Days with any listening, in the first five trial days")
    ax.set_ylabel("Share of trialists (%)")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.25)
    _finish(fig, ax, path)


def draw_funnel(n: dict, path: Path) -> None:
    steps = n["funnel"]
    y = np.arange(len(steps))[::-1]
    top = steps[0][1]
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    colors = [GREY] * (len(steps) - 1) + [BLUE]
    ax.barh(y, [c for _, c in steps], color=colors, height=0.62)
    for yi, (_, count) in zip(y, steps):
        ax.text(count + top * 0.01, yi, f"{count:,}", va="center", fontsize=8.5, color=DARK)
    ax.set_yticks(y, [label for label, _ in steps])
    ax.set_xlim(0, top * 1.18)
    ax.set_xticks([])
    ax.spines[["bottom"]].set_visible(False)
    _finish(fig, ax, path)


def draw_audience(n: dict, path: Path) -> None:
    rows = sorted(n["channels"], key=lambda r: r["share_worst"] - r["share_best"])
    city = n["audience"]["city"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.2, 2.8), gridspec_kw={"width_ratios": [1.3, 1]})
    y = np.arange(len(rows))
    ax1.barh(y + 0.2, [r["share_best"] * 100 for r in rows], height=0.4, color=BEST,
             label=f"Best campaign ({month(n['best'])})")
    ax1.barh(y - 0.2, [r["share_worst"] * 100 for r in rows], height=0.4, color=WORST,
             label=f"Worst campaign ({month(n['worst'])})")
    names = [f"Channel {r['channel']}" if r["channel"] != "other" else "Other channels" for r in rows]
    ax1.set_yticks(y, [f"{name}\nconverts {r['conversion']:.1%}" for name, r in zip(names, rows)], fontsize=8)
    ax1.set_xlabel("Share of trialists (%)", fontsize=8.5)
    ax1.set_title("Signup channel (anonymized code)", fontsize=9.5)
    fig.legend(*ax1.get_legend_handles_labels(), loc="upper center", ncol=2, fontsize=8, frameon=False)
    fig.subplots_adjust(top=0.8)
    ax1.grid(axis="x", alpha=0.25)
    ax1.spines[["top", "right"]].set_visible(False)

    labels = [f"City {city['level']}\nconverts {city['conversion']:.1%}",
              f"Other cities\nconvert {city['conversion_other']:.1%}"]
    best = [city["share_best"] * 100, (1 - city["share_best"]) * 100]
    worst = [city["share_worst"] * 100, (1 - city["share_worst"]) * 100]
    y2 = np.arange(2)[::-1]
    ax2.barh(y2 + 0.2, best, height=0.4, color=BEST)
    ax2.barh(y2 - 0.2, worst, height=0.4, color=WORST)
    ax2.set_yticks(y2, labels, fontsize=8)
    ax2.set_xlabel("Share of trialists (%)", fontsize=8.5)
    ax2.set_title("City (anonymized code)", fontsize=9.5)
    ax2.grid(axis="x", alpha=0.25)
    ax2.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def draw_listening(n: dict, path: Path) -> None:
    labels = {"minutes": "Listening minutes", "songs": "Unique songs", "completion": "Share of songs finished",
              "early_skips": "Share skipped early"}
    keys = list(labels)[::-1]
    ratio = [n["listening_medians"][k][n["worst"]] / n["listening_medians"][k][n["best"]] * 100 for k in keys]
    fig, ax = plt.subplots(figsize=(7.2, 2.4))
    ax.barh([labels[k] for k in keys], ratio, color=WORST, height=0.6)
    for yi, r in enumerate(ratio):
        ax.text(r - 2, yi, f"{r:.0f}%", va="center", ha="right", fontsize=8.5, color="white", fontweight="bold")
    ax.axvline(100, color=RULE, lw=1, ls="--")
    ax.text(100, len(keys) - 0.45, " best campaign = 100%", fontsize=8, color=RULE, va="bottom")
    ax.set_xlim(0, 125)
    ax.set_xlabel("Worst campaign's listeners: median, as % of the best campaign's listeners")
    ax.grid(axis="x", alpha=0.25)
    _finish(fig, ax, path)


def draw_traits(n: dict, path: Path) -> None:
    rows = sorted(n["trait_share"], key=lambda r: r["share"])
    y = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7.2, 2.5))
    shares = [r["share"] * 100 for r in rows]
    ax.barh(y, shares, color=BLUE, height=0.6)
    ax.errorbar(shares, y, xerr=[[(r["share"] - r["ci"][0]) * 100 for r in rows],
                                 [(r["ci"][1] - r["share"]) * 100 for r in rows]],
                fmt="none", ecolor=RULE, capsize=3, lw=1)
    for yi, r in zip(y, rows):
        label = f"{r['share']:.0%}".replace("-", "−")
        if r["share"] < 0:  # left of a negative bar, so the label never sits on it
            ax.text(min(r["share"], r["ci"][0]) * 100 - 2, yi, label, va="center", ha="right", fontsize=9, color=DARK)
        else:
            ax.text(max(r["share"], r["ci"][1]) * 100 + 2, yi, label, va="center", fontsize=9, color=DARK)
    ax.axvline(0, color=RULE, lw=0.8)
    ax.set_yticks(y, [r["trait"] for r in rows])
    lo = min(r["ci"][0] for r in rows) * 100
    hi = max(r["ci"][1] for r in rows) * 100
    ax.set_xlim(min(lo - 12, 0), hi + 10)  # room for the labels at both ends
    ax.set_xlabel("Share of the \"who\" effect (%), with 95% range")
    ax.grid(axis="x", alpha=0.25)
    _finish(fig, ax, path)


CHARTS = {"split.png": draw_split, "campaigns.png": draw_campaigns, "activation.png": draw_activation,
          "silence.png": draw_silence, "funnel.png": draw_funnel, "audience.png": draw_audience,
          "listening.png": draw_listening, "traits.png": draw_traits}


def run(ctx) -> list[str]:
    out = ctx.cache_dir / "publish"
    out.mkdir(exist_ok=True)
    numbers = build_numbers(ctx.cache_dir, d.MIN_PUBLISHED_CELL)
    (out / "published.json").write_text(json.dumps(numbers, indent=2))
    for name, draw in CHARTS.items():
        draw(numbers, out / name)
    return [f"publish/{name}" for name in ("published.json", *CHARTS)]
