# What drives free-trial conversion: who signs up, or what they do?

Free-trial campaigns on a music-streaming service converted trialists to paid at very different rates: **8.01%** for the best campaign and **3.25%** for the worst. This project splits that gap into the part that goes with **who** each campaign reached and the part that goes with **what** trialists did in their first five trial days, using 321,506 trials and 410 million daily listening rows from KKBox's public WSDM Cup 2018 data.

| Part of the best-vs-worst gap | Share | 95% CI |
|---|---:|---|
| Who each campaign reached (signup channel, city, age, gender, time since registration) | **83%** | 71–96% |
| What trialists did in their first five trial days (days active, minutes, songs, completion, skips) | **27%** | 24–32% |
| The campaign itself | **≈ 0** (−11%) | −27% to +5% |

The parts sum to more than 100% before the campaign term because a better audience and better behavior compound on the probability scale; Shapley values split that overlap evenly. The write-up is [`docs/index.html`](docs/index.html).

## Reproduce

**Requirements:** Python 3.13, about 45 GB of free disk space (30 GB of raw logs plus caches) and 16 GB+ of RAM.

1. **Data.** Download the competition files from Kaggle's [WSDM - KKBox's Churn Prediction Challenge](https://www.kaggle.com/c/kkbox-churn-prediction-challenge) and extract them into `data/raw/`:

   ```
   transactions.csv  transactions_v2.csv  members_v3.csv  user_logs.csv  user_logs_v2.csv
   ```

   The raw data is not redistributed here. `user_logs.csv` is about 30 GB extracted, and an interrupted extraction leaves a truncated file that looks fine at a glance. Check that it ends with a newline and has the expected size (see [Data checks](#data-checks)).

2. **Environment.**

   ```
   py -3.13 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

3. **Pipeline.** The first time, build everything in dependency order (about 41 minutes):

   ```
   .venv\Scripts\python -m src.pipeline all
   ```

   After that, each target rebuilds only what's out of date, and `python -m src.pipeline status` shows what's fresh:

   | Target | Builds | Time |
   |---|---|---|
   | `ingest` | transactions + members to Parquet; the trial population | ~10 s |
   | `campaign_rates` | audience traits; raw vs audience-adjusted conversion per campaign | ~35 s |
   | `logs` | one scan of the listening logs; missing-day checks | ~9 min |
   | `model` | behavior features, the who / what decomposition, charts and `docs/index.html` | ~30 s |
   | `bootstrap` | 200 bootstrap refits of the decomposition and the trait split | ~30 min |

   The bootstrap never runs on its own initiative. If `model` needs fresh intervals, it stops and says to run `bootstrap` first.

4. **Tests.** `python -m pytest`

## How it works

- **Rules in one place.** Every date, window and threshold lives in [`src/definitions.py`](src/definitions.py), with a comment on where it came from. Each pipeline stage stamps its output with a hash of the rules and inputs it read. Changing a rule rebuilds that stage and everything downstream. Code changes aren't part of the stamp: after editing a stage, delete its `data/cache/<stage>.meta.json`.
- **Stages** ([`src/pipeline.py`](src/pipeline.py)): `ingest` → `trials` → `mix` → `campaign_rates`; `logs` → `completeness` → `features` → `model` → `bootstrap` → `charts` → `page`.
- **Population** ([`src/trials.py`](src/trials.py)):
  - a first-time, $0, 7-day trial starting on or after 2015-04-01, expiring 5–31 days after it starts, with a member profile;
  - converted = a paid, non-cancelled transaction within 30 days after the trial expires;
  - trialists who paid during the trial are excluded.
- **Model** ([`src/model.py`](src/model.py)): a logistic regression of conversion on campaign, audience traits and banded trial-week behavior. The best-vs-worst gap is split with Shapley values over two counterfactual swaps (audience; behavior from similar trialists), keeping the worst campaign's own effect.
- **Exploratory notebooks** ([`notebooks/`](notebooks/)): which audience traits and which behaviors carry the effect, plus robustness checks.

## Data checks

- **`user_logs.csv`:** 392,106,543 data rows; ends with a newline (checked 2026-10-22 after re-extraction; an earlier copy was truncated at 112,848,597 rows). `user_logs_v2.csv`: 18,396,362 rows.
- **Transaction files:** `transactions.csv` runs to 2017-02-28. `transactions_v2.csv` runs to 2017-03-31 but covers only 48,667 of 527,323 trial users. Follow-up therefore ends at 2017-02-28 for everyone.
- **Missing log days:** none. The closest day was 18% below its weekday norm, against a 30% threshold.
- **Impossible listening times:** 208,686 log rows have a negative `total_secs` or more than 24 hours in a day. Their seconds are set to missing, and their song counts are kept.
- **Missing member profiles:** trialists without one (47,425 eligible) converted at 0.02%, which suggests the member file drops accounts that never paid. They are excluded.

## Privacy

No row-level data is committed: `data/` is ignored, and published tables have at least 20 trialists per cell. [`src/check_no_msno.py`](src/check_no_msno.py) scans every committable file for strings shaped like a KKBox user ID. The pre-commit hook in [`.githooks/`](.githooks/) runs it before each commit. Enable it once with:

```
git config core.hooksPath .githooks
```

---

**Note on the competition labels.** The competition's `is_churn` labels are computed from transactions by `WSDMChurnLabeller.scala`, and `transactions_v2.csv` overlaps the window those labels describe. Models trained on the competition's features can partly recover the label rule from future transactions. This project doesn't use `is_churn` or the train files; it defines conversion from the transactions directly.
