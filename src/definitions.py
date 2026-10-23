"""Locked analysis rules for the Who vs What trial-conversion study.

Every date, window, threshold and count the pipeline uses lives here, once.
Source: docs/designs/trial-conversion-who-vs-what.md (Definitions, Method,
Success Criteria) plus eng-review decisions D7 and D9.

Each stage in src/pipeline.py lists the names it reads. Changing any of those
values rebuilds that stage and everything downstream of it (eng review D5).
Paths are not analysis rules, so they are never part of a stage fingerprint.
"""

from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
CACHE_DIR = ROOT / "data" / "cache"

# --- Raw inputs (names under RAW_DIR) ----------------------------------------
TRANSACTIONS_FILES = ("transactions.csv", "transactions_v2.csv")
MEMBERS_FILE = "members_v3.csv"
USER_LOGS_FILES = ("user_logs.csv", "user_logs_v2.csv")

# --- Trial definition --------------------------------------------------------
# Trial = payment_plan_days == 7, actual_amount_paid == 0, is_cancel == 0.
TRIAL_PLAN_DAYS = 7
TRIAL_AMOUNT_PAID = 0
# Trial end = the trial row's membership_expire_date. Trials expiring outside
# [start+5, start+31] are excluded. Changed 2026-10-22 (user-approved) from a
# fixed start+7 with expiry in [start+6, start+8], which dropped 47% of
# first-transaction trials, including every 2016-06..09 trial (median expiry
# start+5) [measured: read-only probe of transactions.csv].
TRIAL_EXPIRE_MIN_OFFSET_DAYS = 5
TRIAL_EXPIRE_MAX_OFFSET_DAYS = 31

# --- New-user trial filters --------------------------------------------------
# New user = the trial is the user's first transaction in the files, and it
# starts after a 3-month burn-in (transactions.csv starts 2015-01), so earlier
# activity is not invisible to the first-transaction test. The design's
# "registered <= 60 days before the trial" filter was dropped 2026-10-22
# (user-approved): it kept only 11% of first-transaction trials, because most
# trialists registered a free account months or years earlier [measured].
BURN_IN_START = date(2015, 4, 1)
# Trialists with no row in members_v3.csv are excluded (user-approved
# 2026-10-22). 47,425 of 377,481 eligible trialists had none, and 11 of them
# converted (0.02%, near zero in every campaign) [measured], which suggests the
# members file drops accounts that never paid: an outcome-driven signal, not
# who the campaign reached.
REQUIRE_MEMBER_ROW = True

# --- Conversion and follow-up ------------------------------------------------
# Converted = paid (actual_amount_paid > 0), non-cancel transaction dated in
# [trial end, trial end + 30]. A paid transaction in [start, trial end) makes an
# in-trial payer, excluded from every headline population.
CONVERSION_WINDOW_DAYS = 30
# Eligible = trial end + 30 <= FOLLOWUP_END. transactions_v2.csv covers only
# 48,667 of 527,323 trial users [measured 2026-10-22], so per the locked rule
# follow-up ends at the last date of transactions.csv [measured: 2017-02-28].
FOLLOWUP_END = date(2017, 2, 28)

# --- Behavior window and log validity ----------------------------------------
# Behavior = trial days 0..4 for every user: the shortest kept trial lasts 5
# days, so a 5-day window never reaches past any trial's end (2026-10-22).
BEHAVIOR_WINDOW_DAYS = 5
# Behavior bands among trialists who listened (user-approved 2026-10-23).
# Each feature becomes 5 bands; band 1 is "below the first edge". Edges are
# round numbers near the listener quintiles [measured]. Silent trialists (no
# log row in the window) go in band 1 of every feature: days active = 0 already
# carries silence, and a separate "none" level would duplicate it exactly.
LISTEN_MINUTES_EDGES = (10, 50, 200, 500)
COMPLETION_RATE_EDGES = (0.10, 0.45, 0.75, 0.90)
EARLY_SKIP_SHARE_EDGES = (0.10, 0.30, 0.50, 0.80)
UNIQUE_SONGS_EDGES = (4, 17, 55, 120)
# D7: a log row with total_secs outside [0, 86400] keeps its song counts but its
# seconds become missing; the user is flagged.
MIN_VALID_TOTAL_SECS = 0
MAX_VALID_TOTAL_SECS = 86_400

# --- Mix (who) ---------------------------------------------------------------
TOP_CITIES = 10
# Models drop trials in any mix level with fewer trials than this, and
# reports them. A 1-trial signup channel and 6 trials registered after their
# trial started never converted, which stopped the model from converging.
MIN_MIX_LEVEL_TRIALS = 100
# bd <= 0, > 80 or missing -> "unknown".
MIN_VALID_AGE = 1
MAX_VALID_AGE = 80
# Age bands as upper bounds: <=24, 25-34, 35+ (plus "unknown").
AGE_BAND_UPPER_BOUNDS = (24, 34)
# Registration-to-trial lag bands, lower bounds in days: 0-60, 61-365,
# 366-1095 (1-3 y), 1096+ (>3 y), plus "missing" (no member row, no
# registration date, or registration after the trial started). User-approved
# 2026-10-22 together with dropping the 60-day filter.
REG_LAG_BAND_LOWER_BOUNDS_DAYS = (0, 61, 366, 1096)

# --- Log completeness -------------------------------------------------------
# Gap day: zero distinct log users, or the paying-member active share falls more
# than 30% (relative) below the median of the same weekday over +/-8 weeks.
GAP_REFERENCE_WEEKS = 8
GAP_RELATIVE_DROP = 0.30
# D9: check (b) runs on a fixed sample of paying members, chosen by a seeded
# hash of msno.
MEMBER_SAMPLE_SIZE = 100_000
MEMBER_SAMPLE_SEED = 20171103

# --- Decomposition -----------------------------------------------------------
# Best/worst campaigns are picked among campaigns with at least this many trials,
# counted in the population used for the pick (Common for B, Full for A).
MIN_CAMPAIGN_TRIALS = 2_000
# Matching cell = registered_via (top 5 + other) x age band.
MATCH_TOP_REGISTERED_VIA = 5
DECOMPOSITION_SEED = 20171104

# --- Uncertainty -------------------------------------------------------------
BOOTSTRAP_REPLICATES = 200
BOOTSTRAP_SEED = 20171105

# --- Publishing --------------------------------------------------------------
# Every published aggregate has n >= 20 per cell; smaller cells are suppressed
# or merged.
MIN_PUBLISHED_CELL = 20
# msno shape: 44-character base64 string ending in "=". The lookarounds keep it
# from matching the tail of a longer base64 blob, such as an embedded image.
MSNO_PATTERN = r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{43}=(?![A-Za-z0-9+/=])"
# Link to the public repository on the write-up page; empty until it exists
# (public vs private depends on the Kaggle data-use check).
PUBLIC_REPO_URL = ""
# Where the write-up is published (GitHub Pages serves /docs).
DOCS_DIR = ROOT / "docs"
