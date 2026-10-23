"""Stage `model`: the who / what split recovers known answers on synthetic data."""

import numpy as np
import pandas as pd
import pytest

from src import model

CONSTANT = {
    "city": "1", "age_band": "25-34", "gender": "male", "reg_lag_band": "0-60",
    "minutes_band": "2:10-50", "completion_band": "3:0.45-0.75", "skip_band": "2:0.1-0.3",
    "unique_band": "2:4-17", "first_day_band": "d0",
}


def campaign(name, n, share_a, share_active, rate):
    """n trialists; share_a on channel 'a' (else 'b'); share_active listened 5 days (else 0).
    rate(channel, days) gives the conversion probability of each cell, applied exactly."""
    rows = []
    for via, p_via in (("a", share_a), ("b", 1 - share_a)):
        for days, p_days in (("5", share_active), ("0", 1 - share_active)):
            k = round(n * p_via * p_days)
            conv = round(k * rate(via, days))
            rows += [{"campaign": name, "registered_via": via, "days_active_band": days,
                      "converted": int(i < conv)} for i in range(k)]
    return rows


def frame(rate):
    # Best campaign skews to channel a AND to active trialists; worst to b and inactive.
    rows = campaign("2016-06", 8000, 0.8, 0.8, rate) + campaign("2016-10", 8000, 0.2, 0.2, rate)
    return pd.DataFrame(rows).assign(**CONSTANT)


def split(rate, seed=0):
    data = frame(rate)
    res = model.fit(data)
    best, worst, _ = model.best_worst(data)
    return model.decompose(res, data, best, worst, np.random.default_rng(seed), top_via=5)


def test_gap_driven_only_by_who():
    out = split(lambda via, days: 0.20 if via == "a" else 0.04)
    assert out["share_who"] == pytest.approx(1.0, abs=0.03)
    assert out["share_what"] == pytest.approx(0.0, abs=0.03)
    assert out["share_unexplained"] == pytest.approx(0.0, abs=0.03)


def test_gap_driven_only_by_what():
    out = split(lambda via, days: 0.20 if days == "5" else 0.04)
    assert out["share_what"] == pytest.approx(1.0, abs=0.03)
    assert out["share_who"] == pytest.approx(0.0, abs=0.03)


def test_shapley_parts_add_up_to_the_explained_gap():
    out = split(lambda via, days: 0.03 + (0.06 if via == "a" else 0) + (0.10 if days == "5" else 0))
    assert out["phi_who"] + out["phi_what"] == pytest.approx(out["v_who_what"], abs=1e-12)
    assert out["unexplained"] == pytest.approx(out["gap"] - out["v_who_what"], abs=1e-12)
    assert out["share_who"] > 0.2 and out["share_what"] > 0.2


def test_matched_draws_fall_back_to_channel_then_anyone():
    rng = np.random.default_rng(1)
    target_cell = np.array(["a|25-34", "a|35+", "c|35+"])
    target_via = np.array(["a", "a", "c"])
    pool = np.array([10, 11, 12])
    pool_cell = np.array(["a|25-34", "a|25-34", "b|<=24"])
    pool_via = np.array(["a", "a", "b"])

    drawn, used = model.draw_matched(rng, target_cell, target_via, pool, pool_cell, pool_via)

    assert drawn[0] in (10, 11)  # same cell
    assert drawn[1] in (10, 11)  # cell a|35+ empty -> same channel a
    assert drawn[2] in (10, 11, 12)  # channel c absent -> anyone
    assert used == {"cell": 1, "registered_via": 1, "anyone": 1}


def test_decompose_refuses_misaligned_rows():
    data = frame(lambda via, days: 0.1)
    res = model.fit(data)
    with pytest.raises(ValueError, match="dropped rows"):
        model.decompose(res, data.iloc[:-1], "2016-06", "2016-10", np.random.default_rng(0), 5)
