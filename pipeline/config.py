"""Central configuration for the Install Base Reactivation Console v2 pipeline.

Scope: US federal prime contract awards for enterprise storage and the
ADP/IT support equipment around it, FY2016 to present.

THIS IS NOT ANY VENDOR'S INSTALL BASE. It is a public-procurement proxy for
install-base dynamics. See README.md, "Framing".
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"
REPORTS = ROOT / "reports"
APP_DATA = ROOT / "app" / "data"
for _p in (RAW, INTERIM, PROCESSED, REPORTS, APP_DATA):
    _p.mkdir(parents=True, exist_ok=True)

MANIFEST = RAW / "manifest.json"

# ---------------------------------------------------------------- api
API_BASE = "https://api.usaspending.gov/api/v2"
SEARCH_AWARD = f"{API_BASE}/search/spending_by_award/"
SEARCH_COUNT = f"{API_BASE}/search/spending_by_award_count/"
USER_AGENT = "install-base-reactivation-console/2.0 (public-data research; contact via repo)"

# USAspending caps page size at 100 and deep paging at ~10,000 records per query.
PAGE_LIMIT = 100
DEEP_PAGE_CAP = 10_000
SAFE_PARTITION_CEILING = 9_000  # sub-partition by quarter above this
REQUEST_PAUSE_S = 0.6           # politeness delay between successful calls
MAX_RETRIES = 6
BACKOFF_BASE_S = 3.0
MAX_WORKERS = 2                 # concurrent partitions; keep modest, this is a free public API

# USAspending's edge drops connections outright when it decides a client is too
# noisy: every request then fails in ~1s with RemoteDisconnected, from curl too.
# A plain retry loop burns through its attempts inside that block, so the fetcher
# trips a circuit breaker and waits instead.
CIRCUIT_TRIP_FAILURES = 4       # consecutive connection failures before cooling down
CIRCUIT_COOLDOWN_S = 240        # how long to wait before probing again
CIRCUIT_MAX_COOLDOWNS = 12      # give up after this many cooldowns

# Which date the time_period filter applies to.
#   action_date   - any transaction in the window; an award recurs in every FY it
#                   was touched, which inflates the pull ~2.5x
#   date_signed   - the award is counted once, in the FY it was signed
# date_signed gives one row per award and cleaner cohort semantics. The trade-off
# is that contracts signed before FY2016 and still running are not captured; at an
# 11-year window that is immaterial for storage refresh cycles of 3-7 years.
TIME_DATE_TYPE = "date_signed"

# Prime contract award types (A=BPA call, B=purchase order, C=delivery order,
# D=definitive contract). IDVs are fetched separately under IDV_TYPES.
AWARD_TYPE_CODES = ["A", "B", "C", "D"]
IDV_TYPES = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]

# ---------------------------------------------------------------- scope
FY_START, FY_END = 2016, 2026          # federal fiscal years, inclusive
SEARCHABLE_FLOOR = "2007-10-01"        # USAspending search history begins here

# NAICS anchor: the manufacture of computer storage devices.
NAICS_CODES = {
    "334112": "COMPUTER STORAGE DEVICE MANUFACTURING",
}

# PSC families, era-aware.
#
# THE STRUCTURAL BREAK. Federal PSC coding for IT migrated from the numeric
# 70-series and J/D service codes to the alphanumeric 7A-7K / DA-DK "IT and
# Telecom" family. Measured against the award data itself:
#
#   awards in scope   FY2019   FY2020   FY2021   FY2022
#   legacy codes      37,725   36,223    1,355        1
#   modern codes           0        0   17,794   13,921
#
# The changeover is a single step at the FY2020/FY2021 boundary, not a glide.
# Any family built from one era alone would show accounts "going dormant" and
# coverage "collapsing" in 2021 as a pure artefact of recoding. Every family
# below therefore spans both eras, and era is carried on each award so the break
# can be shown rather than smoothed over. See README, "The FY2021 PSC break".
PSC_FAMILIES = {
    "hardware_core": {
        "7025": ("legacy", "INFORMATION TECHNOLOGY INPUT/OUTPUT AND STORAGE DEVICES"),
        "7K20": ("modern", "IT AND TELECOM - STORAGE PRODUCTS (HARDWARE AND PERPETUAL LICENSE SOFTWARE)"),
    },
    # The brief's "PSC 7035 and related 70-series", plus the modern compute and
    # data-centre product codes that replaced them, so the class spans both eras.
    "hardware_adjacent": {
        "7010": ("legacy", "INFORMATION TECHNOLOGY EQUIPMENT SYSTEM CONFIGURATION"),
        "7021": ("legacy", "INFORMATION TECHNOLOGY CENTRAL PROCESSING UNIT (CPU, COMPUTER), DIGITAL"),
        "7022": ("legacy", "INFORMATION TECHNOLOGY CENTRAL PROCESSING UNIT (CPU, COMPUTER), HYBRID"),
        "7035": ("legacy", "INFORMATION TECHNOLOGY SUPPORT EQUIPMENT"),
        "7042": ("legacy", "MINI AND MICRO COMPUTER CONTROL DEVICES"),
        "7050": ("legacy", "INFORMATION TECHNOLOGY COMPONENTS"),
        "7B20": ("modern", "IT AND TELECOM - HIGH PERFORMANCE COMPUTE"),
        "7B21": ("modern", "IT AND TELECOM - COMPUTE: MAINFRAME"),
        "7B22": ("modern", "IT AND TELECOM - COMPUTE: SERVERS"),
        "7C20": ("modern", "IT AND TELECOM - DATA CENTER PRODUCTS"),
    },
    # Support attached to that equipment. Drives the coverage calculation.
    "maintenance": {
        "J070": ("legacy", "MAINT/REPAIR/REBUILD OF EQUIPMENT- ADP EQUIPMENT/SOFTWARE/SUPPLIES/SUPPORT EQUIPMENT"),
        "D320": ("legacy", "IT AND TELECOM- ANNUAL HARDWARE MAINTENANCE SERVICE PLANS"),
        "D310": ("legacy", "IT AND TELECOM- CYBER SECURITY AND DATA BACKUP"),
        "DK01": ("modern", "IT AND TELECOM - STORAGE SUPPORT SERVICES (LABOR)"),
        "DC01": ("modern", "IT AND TELECOM - DATA CENTER SUPPORT SERVICES (LABOR)"),
    },
    # Consumption replacing ownership: the substitution signal for an install base.
    "storage_as_a_service": {
        "DK10": ("modern", "IT AND TELECOM - STORAGE AS A SERVICE"),
        "DC10": ("modern", "IT AND TELECOM - DATA CENTER AS A SERVICE"),
    },
}

PSC_STORAGE_CORE = {k: v[1] for k, v in PSC_FAMILIES["hardware_core"].items()}
PSC_STORAGE_ADJACENT = {k: v[1] for k, v in PSC_FAMILIES["hardware_adjacent"].items()}
PSC_MAINTENANCE = {k: v[1] for k, v in PSC_FAMILIES["maintenance"].items()}
PSC_STORAGE_AS_A_SERVICE = {k: v[1] for k, v in PSC_FAMILIES["storage_as_a_service"].items()}

# Deliberately EXCLUDED, with reasons (see README "Scope decisions"):
PSC_EXCLUDED = {
    "7030": "IT SOFTWARE (legacy) - licences are not hardware install base",
    "7A20/7A21": "IT AND TELECOM - APPLICATION SOFTWARE (modern) - same reason",
    "7045": "IT SUPPLIES - consumables (media, toner, cabling); ~59k mostly sub-$5k "
            "awards that would swamp the install-base signal",
}

PSC_HARDWARE = {**PSC_STORAGE_CORE, **PSC_STORAGE_ADJACENT}
PSC_ALL = {**PSC_HARDWARE, **PSC_MAINTENANCE, **PSC_STORAGE_AS_A_SERVICE}

# Role each PSC plays in the install-base model, and which coding era it belongs to.
PSC_ROLE = {c: role for role, codes in PSC_FAMILIES.items() for c in codes}
PSC_ERA = {c: era for codes in PSC_FAMILIES.values() for c, (era, _d) in codes.items()}

# The fiscal year the coding changed over. Used to split era-sensitive reporting
# rather than to filter anything out.
#
# A SECOND, SUBTLER PROBLEM. The legacy 70-series was one broad "ADP equipment"
# bucket. The modern taxonomy splits that across 7A-7K by function, and we take
# only the storage, compute and data-centre slices. So scope narrows at FY2021 as
# well as changing shape, and award volume drops ~45% across that boundary for
# reasons that are partly definitional, not behavioural. Dormancy is the metric
# most exposed to this, so install_base.py reports it under four different scopes
# instead of publishing one number. See README, "What the dormancy rate does and
# does not tell you".
PSC_ERA_BREAK_FY = 2021

# ---------------------------------------------------------------- fields
# Validated against the API's own Contract Award mapping list (see docs/api_notes.md).
AWARD_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Awarding Agency",
    "Awarding Agency Code",
    "Awarding Sub Agency",
    "Awarding Sub Agency Code",
    "Funding Agency",
    "Funding Sub Agency",
    "Award Amount",
    "Total Outlays",
    "Start Date",
    "End Date",
    "Base Obligation Date",
    "Description",
    "NAICS",
    "PSC",
    "Place of Performance State Code",
    "Contract Award Type",
    "recipient_id",
    "recipient_location_state_code",
    "generated_internal_id",
    "Last Modified Date",
]

# ---------------------------------------------------------------- modelling
# Analysis "today". Frozen so the whole pipeline is reproducible.
AS_OF = _dt.date(2026, 9, 21)

# Time-based validation cutoff: train on awards through TRAIN_CUTOFF, evaluate on
# the HORIZON_DAYS that follow. No random splits anywhere.
TRAIN_CUTOFF = _dt.date(2024, 9, 30)   # end of FY2024
HORIZON_DAYS = 365

DORMANCY_MONTHS = 24         # no new base award in 24+ months  (REAL, not simulated)
COTERM_WINDOW_DAYS = 90      # contracts ending within 90 days of one another
COVERAGE_WINDOW_DAYS = 365   # maintenance award within +/- this of the hardware award
RENEWAL_LOOKAHEAD_DAYS = 365

RANDOM_SEED = 20260921       # every synthetic draw is seeded from this
