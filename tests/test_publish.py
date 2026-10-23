"""Stages `charts` and `page`: the publishing floor and what the page says."""

from datetime import date

import pytest

from src import charts, page


def numbers(unexplained_ci=(-0.26, 0.02)):
    split = {
        "who": {"points": 0.0397, "share": 0.834, "ci": [0.739, 0.947]},
        "what": {"points": 0.0131, "share": 0.274, "ci": [0.230, 0.319]},
        "unexplained": {"points": -0.0052, "share": -0.108, "ci": list(unexplained_ci)},
    }
    return {
        "best": "2016-06", "worst": "2016-10", "best_rate": 0.0801, "worst_rate": 0.0325, "gap": 0.0476,
        "split": split,
        "population": {"log_rows": 410502905, "trial_rows": 582058, "full_trials": 329370,
                       "no_member_row_excluded": 48147, "in_trial_payers_excluded": 8232,
                       "modeled_trials": 321506, "gap_days": 0, "bootstrap_replicates": 200},
        "campaigns": [{"campaign": "2015-06", "n": 102409, "raw": 0.0389, "adjusted": 0.0243},
                      {"campaign": "2016-06", "n": 8852, "raw": 0.0801, "adjusted": 0.0616},
                      {"campaign": "2016-10", "n": 56282, "raw": 0.0325, "adjusted": 0.0511}],
        "activation": [{"days": 0, "n": 125972, "raw": 0.0104, "adjusted": 0.0116},
                       {"days": 5, "n": 24546, "raw": 0.1918, "adjusted": 0.1449}],
        "behavior": {"silent_best": 0.285, "silent_worst": 0.433, "all_days_best": 0.138, "all_days_worst": 0.063,
                     "median_minutes_best": 106.3, "median_minutes_worst": 52.7,
                     "median_completion_best": 0.47, "median_completion_worst": 0.45},
        "audience": {
            "registered_via": {"level": "4", "share_best": 0.217, "share_worst": 0.705, "conversion": 0.014,
                               "conversion_other": 0.06},
            "city": {"level": "1", "share_best": 0.528, "share_worst": 0.795, "conversion": 0.017,
                     "conversion_other": 0.07},
        },
        "min_published_cell": 20, "min_campaign_trials": 2000,
    }


def test_publishing_floor_stops_small_cells():
    charts.require_min_cells({"a": 20, "b": 5000}, 20, "ok table")
    with pytest.raises(ValueError, match="cells below 20"):
        charts.require_min_cells({"a": 19, "b": 5000}, 20, "small table")


def test_page_shows_the_headline_numbers():
    html = page.render(numbers(), today=date(2026, 10, 23))
    assert "83%" in html and "27%" in html and "≈ 0" in html
    assert "8.01%" in html and "3.25%" in html
    assert "The 3 campaigns with at least 2,000 trials" in html
    assert "95% CI −26% to +2%" in html and "95% CI 74–95%" in html
    assert "1.2%" in html and "14.5%" in html  # activation, adjusted
    assert "October 2026" in html
    assert "http" not in html.split("<footer>")[0]  # no external resources above the footer


def test_campaign_term_shows_its_estimate_when_the_interval_excludes_zero():
    html = page.render(numbers(unexplained_ci=(-0.20, -0.05)), today=date(2026, 10, 23))
    assert "≈ 0" not in html and "-11%" in html


def test_repo_link_appears_only_when_set():
    assert "<a href" not in page.render(numbers(), today=date(2026, 10, 23))
    linked = page.render(numbers(), repo_url="https://example.org/repo", today=date(2026, 10, 23))
    assert '<a href="https://example.org/repo">' in linked
