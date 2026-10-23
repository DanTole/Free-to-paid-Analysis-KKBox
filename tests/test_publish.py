"""Stages `charts` and `page`: the publishing floor and what the page says."""

import re
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
                       "modeled_trials": 321506, "gap_days": 0, "bootstrap_replicates": 200,
                       "campaigns_modeled": ["2015-06", "2016-06", "2016-10"]},
        "campaigns": [{"campaign": "2015-06", "n": 102409, "raw": 0.0389, "adjusted": 0.0243},
                      {"campaign": "2016-06", "n": 8852, "raw": 0.0801, "adjusted": 0.0616},
                      {"campaign": "2016-10", "n": 56282, "raw": 0.0325, "adjusted": 0.0511}],
        "activation": [{"days": 0, "n": 125972, "raw": 0.0104, "adjusted": 0.0116},
                       {"days": 3, "n": 25587, "raw": 0.0725, "adjusted": 0.0635},
                       {"days": 5, "n": 24546, "raw": 0.1918, "adjusted": 0.1449}],
        "behavior": {"silent_best": 0.285, "silent_worst": 0.433, "all_days_best": 0.138, "all_days_worst": 0.063},
        "audience": {
            "registered_via": {"level": "4", "share_best": 0.217, "share_worst": 0.705, "conversion": 0.014,
                               "conversion_other": 0.06},
            "city": {"level": "1", "share_best": 0.528, "share_worst": 0.795, "conversion": 0.017,
                     "conversion_other": 0.07},
        },
        "trait_share": [
            {"trait": "Signup channel", "share": 0.563, "ci": [0.45, 0.68]},
            {"trait": "City", "share": 0.514, "ci": [0.40, 0.63]},
            {"trait": "Age and gender", "share": -0.143, "ci": [-0.2, -0.08]},
            {"trait": "Time since registration", "share": 0.066, "ci": [0.02, 0.11]},
        ],
        "next_best_pair": {"best": "2016-07", "worst": "2016-10", "best_rate": 0.0656, "gap": 0.0331,
                           "share_who": 0.811, "share_what": 0.236, "share_unexplained": -0.047},
        "listening_medians": {"minutes": {"2016-06": 106.3, "2016-10": 52.7},
                              "songs": {"2016-06": 33.0, "2016-10": 17.0},
                              "completion": {"2016-06": 0.47, "2016-10": 0.45},
                              "early_skips": {"2016-06": 0.29, "2016-10": 0.29}},
        "worst_trials": 56282, "gap_in_subscribers": 2680,
        "min_published_cell": 20, "min_campaign_trials": 2000,
    }


def html(**kw):
    return page.render(numbers(**kw), today=date(2026, 10, 23))


def test_publishing_floor_stops_small_cells():
    charts.require_min_cells({"a": 20, "b": 5000}, 20, "ok table")
    with pytest.raises(ValueError, match="cells below 20"):
        charts.require_min_cells({"a": 19, "b": 5000}, 20, "small table")


def test_page_follows_the_storyline():
    h = html()
    sections = ["The problem", "The method", "The data", "Global findings", "Findings broken down", "Conclusion"]
    positions = [h.index(f"<small>{s}</small>") for s in sections]
    assert positions == sorted(positions)
    # "Who" and "What" are peers under "Findings broken down".
    assert h.index("<h3>Who:") < h.index("<h3>What:") < h.index("<small>Conclusion</small>")


def test_problem_section_defines_campaign_and_frames_suno_early():
    h = html()
    problem = h[h.index("<small>The problem</small>"):h.index("<small>The method</small>")]
    assert "2.5× better" in problem and "8.0%" in problem and "3.2%" in problem
    assert "~2,680" in problem and "out of 56,282 trialists" in problem
    assert 'a "campaign" is one month of trial sign-ups' in problem
    assert "Suno faces the same question" in problem


def test_global_findings_read_the_split_and_the_campaign_term():
    h = html()
    assert "explains 83% of the gap (74–95%)" in h and "explains 27% (23–32%)" in h
    assert "slightly more than the whole gap (111%)" in h
    assert "Its range (−26% to +2%) includes zero" in h
    assert "one after the other" not in h
    # 4.8 points, not 4.76, so it matches the 8.0% and 3.2% cards.
    assert "4.8-point gap" in h


def test_campaign_chart_reading_names_the_mix_and_the_exception():
    h = html()
    assert "average mix of all trialists" in h
    assert "June 2015 is the exception" in h and "only 2.4%" in h
    assert "same pattern holds across all" not in h


def test_activation_reading_is_not_causal():
    h = html()
    assert "the climb comes from listening itself" not in h
    assert "can't rule out that people who already meant to pay listen more" in h
    assert "from 3 days up, the grey line sits above the blue" in h


def test_funnel_reading_names_every_filter():
    h = html()
    for step in ("first trial", "odd trial lengths", "member profile", "paid before the trial ended",
                 "3 biggest sign-up months"):
        assert step in h, step


def test_trait_split_and_extreme_pair_are_reported():
    h = html()
    assert "Signup channel (56%) and city (51%)" in h and "(−14%)" in h
    assert "Against the next-best month (July 2016), the split is similar: 81% who" in h


def test_listening_reading_does_not_overreach():
    h = html()
    assert "there's no sign they liked the music less" in h
    assert "made anything within 24 hours" in h


def test_campaign_term_shows_its_estimate_when_the_interval_excludes_zero():
    h = html(unexplained_ci=(-0.20, -0.05))
    assert "doesn't cross zero" in h and "includes zero" not in h


def test_jargon_stays_in_the_technical_details():
    story = html().split("<details>")[0]
    for term in ("logistic", "Shapley", "bootstrap", "counterfactual", "reshuffled"):
        assert term not in story, term


def test_every_chart_the_page_uses_is_produced_by_the_charts_stage():
    assert set(re.findall(r'src="img/([^"]+)"', html())) == set(charts.CHARTS)


def test_no_footer_notes_and_no_external_resources():
    h = html()
    assert "<footer>" not in h and "not claims about Suno" not in h and "<a href" not in h
    assert "http" not in h


def test_best_and_worst_have_their_own_colors():
    assert len({charts.BEST, charts.WORST, charts.BLUE, charts.ORANGE}) == 4
