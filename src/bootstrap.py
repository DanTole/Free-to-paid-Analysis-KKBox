"""Stage `bootstrap`: 95% intervals for the who / what / unexplained split.

BOOTSTRAP_REPLICATES replicates. Each resamples trialists with replacement
within each campaign, refits the model, and reruns the decomposition with the
best and worst campaigns held at the ones stage `model` picked (design doc,
Uncertainty). Matching-cell draws use the replicate's own random stream.

Writes bootstrap_replicates.parquet (one row per replicate) and
bootstrap_summary.json (2.5% / 97.5% percentiles of every quantity).
"""

import gc
import json
import time

import numpy as np
import pandas as pd

from src import definitions as d
from src import model

QUANTITIES = ("gap", "v_who_what", "v_who", "v_what", "phi_who", "phi_what", "unexplained",
              "share_who", "share_what", "share_unexplained")


def run_replicates(data: pd.DataFrame, best: str, worst: str, replicates: int, seed: int, top_via: int,
                   log=print) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    groups = [data.index[data["campaign"] == c].to_numpy() for c in sorted(data["campaign"].unique())]
    rows = []
    t0 = time.time()
    for b in range(replicates):
        idx = np.concatenate([rng.choice(g, size=len(g), replace=True) for g in groups])
        sample = data.loc[idx].reset_index(drop=True)
        res = model.fit(sample)
        out = model.decompose(res, sample, best, worst, rng, top_via)
        traits = model.trait_split(res, sample, best, worst, rng)
        rows.append({"replicate": b, **{q: out[q] for q in QUANTITIES}, **{f"trait:{g}": v for g, v in traits.items()}})
        del res, sample
        gc.collect()  # each fit holds a large design matrix in reference cycles
        if (b + 1) % 25 == 0:
            log(f"[bootstrap] {b + 1}/{replicates} replicates, {(time.time() - t0) / 60:.1f} min")
    return pd.DataFrame(rows)


def run(ctx) -> list[str]:
    cache = ctx.cache_dir
    point = json.loads((cache / "model_decomposition.json").read_text())
    data, _ = model.load_population(cache, d.MIN_CAMPAIGN_TRIALS, d.MIN_MIX_LEVEL_TRIALS)
    reps = run_replicates(data, point["best"], point["worst"], d.BOOTSTRAP_REPLICATES, d.BOOTSTRAP_SEED,
                          d.MATCH_TOP_REGISTERED_VIA)
    reps.to_parquet(cache / "bootstrap_replicates.parquet")
    summary = {
        "replicates": len(reps),
        "best": point["best"],
        "worst": point["worst"],
        "ci95": {q: [float(reps[q].quantile(0.025)), float(reps[q].quantile(0.975))]
                 for q in [*QUANTITIES, *(c for c in reps.columns if c.startswith("trait:"))]},
    }
    (cache / "bootstrap_summary.json").write_text(json.dumps(summary, indent=2))
    return ["bootstrap_replicates.parquet", "bootstrap_summary.json"]
