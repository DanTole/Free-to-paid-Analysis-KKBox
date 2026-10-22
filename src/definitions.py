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
# Trial end = trial start + 7 days (fixed rule, not membership_expire_date).
TRIAL_LENGTH_DAYS = 7
# membership_expire_date is validation only: outside [start+6, start+8] -> excluded.
TRIAL_EXPIRE_MIN_OFFSET_DAYS = 6
TRIAL_EXPIRE_MAX_OFFSET_DAYS = 8

# --- New-user trial filters --------------------------------------------------
# 3-month burn-in after transactions.csv starts (2015-01), so earlier activity
# is not invisible to the first-transaction test.
BURN_IN_START = date(2015, 4, 1)
# registration_init_time at most this many days before trial start (or missing).
MAX_REGISTRATION_LAG_DAYS = 60

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
# Behavior = trial days 0..6, i.e. [start, trial end - 1].
BEHAVIOR_WINDOW_DAYS = 7
# D7: a log row with total_secs outside [0, 86400] keeps its song counts but its
# seconds become missing; the user is flagged.
MIN_VALID_TOTAL_SECS = 0
MAX_VALID_TOTAL_SECS = 86_400

# --- Mix (who) ---------------------------------------------------------------
TOP_CITIES = 10
# bd <= 0, > 80 or missing -> "unknown".
MIN_VALID_AGE = 1
MAX_VALID_AGE = 80
# Age bands as upper bounds: <=24, 25-34, 35+ (plus "unknown").
AGE_BAND_UPPER_BOUNDS = (24, 34)
# Registration-to-trial lag bands, lower bounds in days: 0-6, 7-29, 30-60
# (plus "missing"). The design doc names the variable but not its bands; these
# were chosen when this file was written and can be changed here.
REG_LAG_BAND_LOWER_BOUNDS_DAYS = (0, 7, 30)

# --- Log completeness (Day 2) ------------------------------------------------
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
# msno shape: 44-character base64 string ending in "=".
MSNO_PATTERN = r"[A-Za-z0-9+/]{43}="
