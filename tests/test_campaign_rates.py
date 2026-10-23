"""Stage `campaign_rates`: g-computation removes a pure audience-mix difference."""

import pandas as pd
import pytest

from src import campaign_rates


def campaign_rows(campaign, n_a, conv_a, n_b, conv_b):
    rows = []
    for group, n, k in (("a", n_a, conv_a), ("b", n_b, conv_b)):
        rows += [{"campaign": campaign, "registered_via": group, "converted": i < k} for i in range(n)]
    return rows


@pytest.fixture
def df():
    # Group a converts at 20%, group b at 5%, in every campaign. X skews to a,
    # Y to b, so raw rates differ only because of who each campaign reached.
    rows = (
        campaign_rows("2016-06", 800, 160, 200, 10)
        + campaign_rows("2016-07", 200, 40, 800, 40)
        + campaign_rows("2016-08", 5, 1, 5, 0)  # below the floor
    )
    return pd.DataFrame(rows).assign(city="1", age_band="25-34", gender="male", reg_lag_band="0-60")


def test_standardized_rates_equal_when_campaigns_differ_only_by_mix(df):
    table, models = campaign_rates.fit_campaign_rates(df, min_trials=100, min_level_trials=50)

    t = table.set_index("campaign")
    assert t.loc["2016-06", "raw_rate"] == pytest.approx(0.17)
    assert t.loc["2016-07", "raw_rate"] == pytest.approx(0.08)
    assert t.loc["2016-06", "std_rate"] == pytest.approx(0.125, abs=1e-3)
    assert t.loc["2016-07", "std_rate"] == pytest.approx(0.125, abs=1e-3)
    assert models["raw_gap"] == pytest.approx(0.09)
    assert models["std_gap"] == pytest.approx(0.0, abs=1e-3)
    assert models["best_campaign"] == "2016-06" and models["worst_campaign"] == "2016-07"
    assert models["mix_model"]["converged"]
    # Campaign-only model sees the raw gap; the mix model sees none.
    assert models["campaign_only_model"]["campaign_odds_ratios"]["2016-07"] < 0.5
    assert models["mix_model"]["campaign_odds_ratios"]["2016-07"] == pytest.approx(1.0, abs=0.02)


def test_campaigns_below_the_floor_are_listed_not_modeled(df):
    table, models = campaign_rates.fit_campaign_rates(df, min_trials=100, min_level_trials=50)

    small = table.set_index("campaign").loc["2016-08"]
    assert not small["modeled"] and pd.isna(small["std_rate"]) and small["n"] == 10
    assert models["campaigns_not_modeled"] == {"2016-08": 10}
    assert models["campaigns_modeled"] == ["2016-06", "2016-07"]


def test_wilson_interval_brackets_the_rate():
    lo, hi = campaign_rates.wilson(17, 100)
    assert lo < 0.17 < hi and 0.10 < lo and hi < 0.26


def test_unconverged_fit_stops_the_stage():
    class Fit:
        mle_retvals = {"converged": False, "iterations": 200}

    with pytest.raises(RuntimeError, match="mix logit did not converge"):
        campaign_rates.require_converged(Fit(), "mix")


def test_rare_mix_levels_are_dropped_and_reported_so_the_fit_converges(df):
    # One trialist on a signup channel nobody else used, who never converted:
    # left in, its coefficient runs off to minus infinity.
    lone = pd.DataFrame([{"campaign": "2016-06", "registered_via": "z", "converted": False}]).assign(
        city="1", age_band="25-34", gender="male", reg_lag_band="0-60"
    )
    table, models = campaign_rates.fit_campaign_rates(pd.concat([df, lone]), min_trials=100, min_level_trials=50)

    assert models["rare_level_trials_dropped"] == {"registered_via": {"z": 1}}
    assert models["mix_model"]["converged"] and models["population_n"] == 2000
