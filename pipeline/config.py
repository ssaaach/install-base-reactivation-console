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

# ==================================================================== offshore
# SECOND DOMAIN: the physical offshore install base of the US Gulf of Mexico,
# from BSEE open data. It is kept strictly separate from federal procurement -
# separate tables, separate ranking, separate console surface - because the two
# have different observability, and an account score mixing an observed purchase
# history with an inferred one means nothing in either world. See README,
# "Two domains".
#
#   federal   observes INPUT spend, and infers the asset
#   offshore  observes the ASSET - named, typed, dated in and dated out - and
#             never observes what was paid for it
DOMAINS = ("federal", "offshore")

BSEE_BASE = "https://www.data.bsee.gov"

# The download names are NOT guessable, and a wrong guess is dangerous: a bad
# filename returns HTTP 200 with a 28 kB HTML error page, which a fetcher that
# checks only the status code will write to disk as data. platstrufixed.zip is
# the real name; platstrucfixed.zip is the plausible one, and it "succeeds".
# fetch_bsee.py verifies the zip magic number on every file for that reason.
#
# Record layouts live at BSEE_BASE + /Main/HtmlPage.aspx?page=<layout>.
BSEE_FILES = {
    "platstrufixed":      ("/Platform/Files/platstrufixed.zip",      "platformStructures"),
    "platmastfixed":      ("/Platform/Files/platmastfixed.zip",      "platformMasters"),
    "platstruremdelimit": ("/Platform/Files/platstruremdelimit.zip", "platformStrucRem"),
}

# Fixed-width layouts, 1-indexed start position and length, transcribed from the
# published record layout pages. Structures and Masters were checked field by
# field against the rows themselves - every date parses, every code vocabulary
# comes out clean - before anything was fitted on them.
#
# Structures Removed is NOT parsed fixed-width and so is not listed here. Its
# published start positions run two characters early from Proposed Removal
# Method onward, which silently produces about a hundred invented methods of
# the form "11EXPLOSIVES GENERIC": the year's last two digits glued to the front
# of the real value. The delimited edition of the same file is clean, so
# offshore.py reads that instead.
BSEE_LAYOUT_STRU = {
    "area_code": (1, 2), "block_number": (3, 6), "complex_id": (9, 8),
    "deck_count": (17, 2), "ew_departure": (19, 1), "install_date": (20, 11),
    "last_revision_date": (31, 11), "major_structure_flag": (42, 1),
    "ns_departure": (43, 1), "removal_date": (44, 11), "slant_slot_count": (55, 3),
    "slot_count": (58, 3), "slot_drill_count": (61, 3),
    "satellite_completion_count": (64, 3), "structure_name": (67, 15),
    "structure_number": (82, 3), "structure_type_code": (85, 5),
    "surface_ew_distance": (90, 6), "surface_ns_distance": (96, 6),
    "underwater_completion_count": (102, 3), "authority_type": (105, 16),
    "authority_number": (121, 8), "authority_status": (129, 20),
}
BSEE_LAYOUT_MAST = {
    "complex_id": (1, 8), "abandon_flag": (9, 1), "distance_to_shore": (13, 4),
    "gas_prod_flag": (19, 1), "mms_company_num": (21, 5), "maj_cmplx_flag": (27, 1),
    "lease_number": (28, 7), "last_rev_date": (35, 11), "water_prod_flag": (50, 1),
    "water_depth": (51, 5), "subdistrict_code": (58, 2), "rig_count": (61, 2),
    "production_flag": (65, 1), "oil_prod_flag": (68, 1), "field_name_code": (70, 8),
    "district_code": (78, 3), "crane_count": (81, 2), "bed_count": (85, 3),
    "area_code": (88, 2), "block_number": (90, 6),
}
# Column order of the delimited Structures Removed file, which carries no header.
BSEE_COLS_REM = [
    "bus_asc_name", "mms_company_num", "application_number", "received_date",
    "final_action_date", "removal_date", "site_clearance_date", "submittal_type",
    "lease_number", "area_code", "block_number", "structure_name",
    "proposed_removal_date", "proposed_removal_method", "district_code",
    "complex_id", "structure_number", "water_depth",
]

# ------------------------------------------------------------- offshore scope
# THE UNIT OF ANALYSIS. Masters is keyed one row per COMPLEX - 6,940 rows, 6,940
# distinct ids, zero duplicates. Structures is one row per STRUCTURE within a
# complex: 6,181 complexes carrying 7,092 structures, 595 of those complexes
# holding more than one, and 21.2% of all structures sitting in such a complex.
# Operator, water depth and lease are therefore COMPLEX-level attributes of a
# STRUCTURE-level event, and joining them on is one-to-many, not one-to-one.
#
# Installation and removal happen to a structure, so the structure is the unit.
# Complex attributes ride along as covariates and are marked as shared, never
# treated as though they were measured independently per structure.
OFFSHORE_UNIT = "structure"

# LEFT TRUNCATION (trap 6). Removals appear in the structures file from 1973,
# but the first years are implausibly thin - one removal in 1973 and six in
# 1974, against a standing base of roughly two thousand. That is record keeping
# starting up, not the Gulf holding still; from 1975 the series is noisy but
# flat. Structures installed before this date are LEFT-TRUNCATED: they enter the
# risk set at the age they had already reached, not at age zero. Treating them
# as born at the threshold compresses the early hazard and flatters every
# survival estimate downstream.
OFFSHORE_OBS_START = _dt.date(1975, 1, 1)

# INSTALL DATES ARE PART-IMPUTED, which is a different problem from truncation
# and is not fixed by it. 69.2% of install dates are exactly 01-JAN, at a rate
# that collapses by decade: 1940s 100%, 1950s 99.4%, 1960s 99.0%, 1970s 97.5%,
# 1980s 98.6%, then 1990s 33.7%, 2000s 2.7%, 2010s 0.9%. For most structures
# older than about 1990 the entry time is known to the YEAR, not the day.
#
#   keep  use them, and declare the measurement error (default)
#   drop  restrict to structures carrying a day-precision install date
# "drop" is published as a sensitivity, never applied silently.
OFFSHORE_IMPUTED_INSTALL = "keep"
OFFSHORE_IMPUTED_INSTALL_MARK = "01-JAN"

# STORMS ARE A COMPETING RISK THAT THIS DATA CANNOT SEPARATE (trap 4).
# Proposed Removal Method says how a structure was severed - EXPLOSIVES GENERIC
# 49.0%, NON-EXPLOSIVES 37.5%, D1 7.2%, SW-4 4.1% - not why it left service, and
# Submittal Type is only INITIAL or MODIFICATION. The timing does not rescue it
# either: removals peak in 2009 (304), 2011 (337) and 2012 (334), not in the
# storm years 2004 (202), 2005 (131) or 2008 (198), because the removal date is
# the regulatory paperwork date and lags the destruction by years.
#
# There is no honest exclusion available here, and inventing one would be worse
# than the trap. The switch exists so the choice is explicit, and defaults to
# "declare": storm losses stay in, and every report says that they are in and
# that they cannot be identified. A later phase that ingests BSEE's destroyed
# structure lists can add "exclude".
OFFSHORE_STORM_POLICY = "declare"

# TWO POPULATIONS (trap 5). Deepwater and shallow-water structures differ in
# lifetime and in removal economics by orders of magnitude, and only 130
# complexes sit beyond this line. Pooling them is the direct analogue of fitting
# one model across both PSC coding eras, so depth is carried as a covariate and
# every headline is also reported by stratum.
OFFSHORE_DEEPWATER_FT = 400

# ABANDON FLAG IS LABEL LEAKAGE (trap 2). It marks a structure already scheduled
# for removal, so predicting removal from it predicts the label from an
# announcement of the label. It is never a feature. It is used only as a
# validation check: a model that cannot rank flagged structures highly is broken.
OFFSHORE_LEAKAGE_FIELDS = ("abandon_flag",)

# OPERATOR IS NOT USED IN PHASE 1 (trap 1). Mms Company Num is the CURRENT
# operator; a structure installed in 1985 may have had four. Attributing a whole
# lifetime to whoever holds it today is survivorship bias aimed straight at the
# ranking. Phase 1 stays at structure level precisely so it never needs to.
OFFSHORE_USE_OPERATOR = False
