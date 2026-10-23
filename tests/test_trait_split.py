"""model.trait_split: the audience swap is credited to the trait that drives it."""

import numpy as np
import pandas as pd
import pytest

from src import model

BEHAVIOR = {"days_active_band": "5", "minutes_band": "2:10-50", "completion_band": "3:0.45-0.75",
            "skip_band": "2:0.1-0.3", "unique_band": "2:4-17", "first_day_band": "d0"}


def frame():
    """Campaigns differ in channel mix only; conversion depends on channel only."""
    rows = []
    for campaign, share_a in (("2016-06", 0.8), ("2016-10", 0.2)):
        for via, share in (("a", share_a), ("b", 1 - share_a)):
            k = int(4000 * share)
            conv = int(k * (0.20 if via == "a" else 0.04))
            rows += [{"campaign": campaign, "registered_via": via, "converted": int(i < conv)} for i in range(k)]
    data = pd.DataFrame(rows)
    # City varies but is unrelated to conversion and to campaign.
    data["city"] = np.where(np.arange(len(data)) % 2 == 0, "1", "5")
    return data.assign(age_band="25-34", gender="male", reg_lag_band="0-60", **BEHAVIOR)


def test_shares_add_to_one_and_go_to_the_driving_trait():
    data = frame()
    res = model.fit(data)
    shares = model.trait_split(res, data, "2016-06", "2016-10", np.random.default_rng(0))
    assert sum(shares.values()) == pytest.approx(1.0)
    assert shares["Signup channel"] == pytest.approx(1.0, abs=0.05)
    assert abs(shares["City"]) < 0.05
