# Install Base Reactivation Console v2

A working console over **real public-domain federal procurement data**, built to
show install-base reactivation mechanics end to end: who has gone quiet, whose
hardware has no support attached, what is expiring, and which accounts are worth
a conversation — with the evidence rows that triggered every recommendation.

**Live:** <https://install-base-console.vercel.app>

The built page is `app/dist/index.html` — a single self-contained file, no
server, no build step, no runtime fetch. 4.59 MB raw, **0.57 MB over the wire**.
Deploys as a static site anywhere; `vercel.json` and `netlify.toml` are in the
repo and need no configuration. See [Deploying the console](#deploying-the-console).

---

## If you have five minutes

Read these four things, in this order. They are where the work actually is.

1. **[The FY2021 PSC break](#the-fy2021-psc-break--the-finding-that-shapes-everything-else).**
   Federal IT product codes changed over in a single step between FY2020 and
   FY2021. A code set drawn from one era would have shown accounts going dormant
   and coverage collapsing in 2021 as a pure recoding artifact — every one of
   them a false finding delivered with real data behind it. Everything in scope
   spans both eras because of this.
2. **[What the dormancy rate does and does not tell you](#what-the-dormancy-rate-does-and-does-not-tell-you).**
   67.4% or 38.8%, depending on scope. Published as a spread, not a number.
3. **[The channel rule, and its real error rate](#the-channel-rule-and-its-real-error-rate).**
   The approach the brief specifies does not work — 51.9% accurate against a
   77.4% baseline. A second attempt from subcontracting behaviour fails too. Both
   are reported as failures, with the diagnosis, rather than quietly dropped.
4. **[Three more places the honest answer is the less impressive one](#three-more-places-the-honest-answer-is-the-less-impressive-one).**
   Two reason codes that fire on ~90% of accounts and so rank nothing; a margin
   sensitivity that cannot fail by construction; a headline AUC flattered by easy
   negatives.

Then `python tests/test_invariants.py` — 24 checks that enforce the claims this
README makes.

---

## Framing — read this first

**This dataset is US federal enterprise-storage procurement. It is a *proxy* for
install-base dynamics. It is not any vendor's install base, and it must never be
described as one.**

The "accounts" here are federal awarding offices, not any company's customers.
The "install base" is what those offices bought under storage and IT-equipment
product codes, as reported to USAspending.gov. Nothing in this repository
describes the customers, revenue, retention or support attach of any company.

The same statement appears on every page of the console.

---

## Headline figures

<!-- BEGIN HEADLINE_FIGURES -->
| Measure | Value |
|---|---:|
| Prime award rows downloaded | 277,818 |
| Distinct awards after dedup | 270,924 |
| Accounts (awarding offices) | 3,096 |
| Total obligated in scope | $74.8bn |
| Resolved vendor entities | 11,245 |
| Dormancy rate (24m, REAL) | 67.4% |
| Hardware coverage rate | 59.9% |
| Uncovered hardware awards | 100,836 |
| Uncovered obligations | $15.6bn |
| Contracts expiring within 365d | 4,005 |
| Co-termination clusters | 546 |
| Accounts with an explicit NO_ACTION | 1 |
| Accounts with positive expected value | 550 |
| Channel rule error rate vs SAM flag | 48.1% |
| Same-parent detection precision | 51.5% |
| Same-parent detection recall | 24.3% |
| Awarding offices resolved | 3,096 |
| Survival model AUC / Brier | 0.922 / 0.0979 |
| Rules baseline AUC / Brier | 0.896 / 0.1745 |
| Ranking uses | **survival_model** |
| Backblaze drive-days behind the hazard curve | 27,761,188 |

_Generated 2026-09-22 12:10 UTC by `pipeline/make_docs.py`. As-of date 2026-09-21._
<!-- END HEADLINE_FIGURES -->

---

## Provenance — the rule this project runs on

Every field in the final model carries one of four tags, and the app displays
them:

| Tag | Meaning |
|---|---|
| **REAL** | sourced from a named public dataset, unmodified |
| **DERIVED** | computed from REAL fields only |
| **SYNTHETIC** | generated, because no public equivalent exists |
| **ASSUMPTION** | a stated commercial input, not a measurement |

A reviewer should be able to tell in ten seconds which numbers are real. That is
what the table below is for, and it is generated from `pipeline/provenance.py`
so it cannot drift from the code.

**A SYNTHETIC field is never presented as evidence of real-world behaviour.**
The synthetic tables are excluded from the survival model and from the
expected-value ranking entirely, and a SYNTHETIC-provenance reason code can
never become an account's recommended action.

### Field-level provenance table

<!-- BEGIN PROVENANCE_TABLE -->
| Table | Field | Provenance | Source | Note |
|---|---|---|---|---|
| `contracts` | `contract_award_unique_key` | **REAL** | USAspending.gov /api/v2/download/awards/ | USAspending primary key |
| `contracts` | `award_id_piid` | **REAL** | USAspending.gov /api/v2/download/awards/ | contract PIID as awarded |
| `contracts` | `parent_award_id_piid` | **REAL** | USAspending.gov /api/v2/download/awards/ | parent IDV, where one exists |
| `contracts` | `award_amount` | **REAL** | USAspending.gov /api/v2/download/awards/ | total_obligated_amount, unmodified |
| `contracts` | `current_total_value_of_award` | **REAL** | USAspending.gov /api/v2/download/awards/ |  |
| `contracts` | `potential_total_value_of_award` | **REAL** | USAspending.gov /api/v2/download/awards/ | ceiling value |
| `contracts` | `base_obligation_date` | **REAL** | USAspending.gov /api/v2/download/awards/ | award_base_action_date |
| `contracts` | `start_date` | **REAL** | USAspending.gov /api/v2/download/awards/ | period_of_performance_start_date |
| `contracts` | `end_date` | **REAL** | USAspending.gov /api/v2/download/awards/ | period_of_performance_current_end_date |
| `contracts` | `psc_code / psc_description` | **REAL** | USAspending.gov /api/v2/download/awards/ | product or service code |
| `contracts` | `naics_code / naics_description` | **REAL** | USAspending.gov /api/v2/download/awards/ |  |
| `contracts` | `recipient_uei` | **REAL** | USAspending.gov /api/v2/download/awards/ | populated on 100% of rows in this extract - the bulk endpoint back-fills it where the paged search endpoint does not; see match report |
| `contracts` | `recipient_parent_name / _uei` | **REAL** | USAspending.gov /api/v2/download/awards/ | held back from matching, used only to grade it |
| `contracts` | `awarding_office_code / _name` | **REAL** | USAspending.gov /api/v2/download/awards/ | defines the account grain |
| `contracts` | `awarding_sub_agency_code / _name` | **REAL** | USAspending.gov /api/v2/download/awards/ |  |
| `contracts` | `extent_competed` | **REAL** | USAspending.gov /api/v2/download/awards/ |  |
| `contracts` | `number_of_offers_received` | **REAL** | USAspending.gov /api/v2/download/awards/ |  |
| `contracts` | `manufacturer_of_goods` | **REAL** | USAspending.gov /api/v2/download/awards/ | recipient's own SAM registration flag; the channel rule is scored against it |
| `contracts` | `usaspending_permalink` | **REAL** | USAspending.gov /api/v2/download/awards/ | per-award evidence link |
| `contracts` | `psc_role` | **DERIVED** | config.PSC_ROLE | hardware / maintenance / storage-as-a-service classification of the PSC |
| `contracts` | `channel` | **REAL / DERIVED** | USAspending.gov /api/v2/download/awards/; else rule | REAL where manufacturer_of_goods is populated, otherwise the NAICS+name rule; channel_source records which applied per award |
| `contracts` | `channel_rule` | **DERIVED** | install_base.naics_name_rule | the stated rule, kept separately so its error rate stays measurable |
| `contracts` | `segment` | **DERIVED** | awarding agency code/name | Defense vs Civilian |
| `contracts` | `region` | **DERIVED** | place of performance state | US Census region |
| `contracts` | `days_to_expiry` | **DERIVED** | end_date vs as-of date |  |
| `entities` | `recipient_entity_id` | **DERIVED** | entity_resolution.py | one per UEI; the UEI-less fallback fires on zero rows here because UEI is complete - the match report says so rather than implying otherwise |
| `entities` | `recipient_canonical` | **DERIVED** | entity_resolution.py | most-awarded spelling within the entity |
| `entities` | `recipient_normalised` | **DERIVED** | entity_resolution.normalise |  |
| `entities` | `org_entity_id` | **DERIVED** | awarding_office_code + sub_agency_code | authoritative codes, not name similarity |
| `entities` | `same_parent_candidates` | **DERIVED** | entity_resolution.py | reported, never merged; precision/recall scored against recipient_parent_uei |
| `accounts` | `account_id / account_name` | **DERIVED** | awarding office |  |
| `accounts` | `first_award_date / last_award_date` | **REAL** | USAspending.gov /api/v2/download/awards/ | min/max of real award dates |
| `accounts` | `total_obligated / award_count` | **REAL** | USAspending.gov /api/v2/download/awards/ | sums and counts of real awards |
| `accounts` | `cohort_fy` | **DERIVED** | first award date | fiscal year of first in-scope award |
| `accounts` | `months_since_last_award` | **DERIVED** | last award date vs as-of |  |
| `accounts` | `dormant` | **DERIVED** | absence of awards for 24+ months | the ABSENCE is real and unsimulated; the threshold is assumption A-06. Sensitive to PSC scope because of the FY2021 recoding - reported under four scopes, not one |
| `accounts` | `size_band / peer_group` | **DERIVED** | obligation quintiles |  |
| `accounts` | `direct_share` | **DERIVED** | channel-weighted obligations |  |
| `accounts` | `coverage_rate` | **DERIVED** | hardware vs maintenance awards | window is assumption A-05 |
| `accounts` | `uncovered_assets` | **DERIVED** | coverage join |  |
| `accounts` | `expansion_vs_cohort` | **DERIVED** | awards/year vs cohort median |  |
| `accounts` | `channel_shift` | **DERIVED** | direct share, last 24m vs before |  |
| `accounts` | `whitespace_count` | **DERIVED** | peer-group PSC adoption | peer floor is assumption A-08 |
| `coverage` | `covered / uncovered` | **DERIVED** | hardware-to-maintenance match | separately identifiable maintenance inside the scoped PSC set only |
| `coverage` | `asset_age_years` | **DERIVED** | base action date vs as-of |  |
| `renewals` | `coterm_cluster_id / _size` | **DERIVED** | expiries within 90 days |  |
| `cohorts` | `retention_by_fy / expansion year_n` | **DERIVED** | real award dates | explicitly right-censored; each cell carries its denominator |
| `whitespace` | `peer_adoption_rate` | **DERIVED** | peer-group purchase history |  |
| `hazard` | `afr / hazard_daily by drive age` | **REAL** | Backblaze Drive Stats (quarterly CSV) | ~27.8M drive-days; replaces a hand-picked refresh age |
| `benchmarks` | `gross margin, subscription ARR growth, net dollar retention` | **REAL** | SEC EDGAR - Everpure, Inc. CIK 0001474432 | BENCHMARK ONLY; never joined to account data |
| `telemetry_synthetic` | `capacity_utilisation` | **SYNTHETIC** | generated by pipeline/synthetic_layer.py (seed-fixed) | conditioned on real obligations and recency; not evidence |
| `support_cases_synthetic` | `severity / category / time_to_resolve` | **SYNTHETIC** | generated by pipeline/synthetic_layer.py (seed-fixed) | Poisson rate driven by REAL asset count and REAL Backblaze hazard; not evidence |
| `health_synthetic` | `health_score / health_band` | **SYNTHETIC** | generated by pipeline/synthetic_layer.py (seed-fixed) | composite of synthetic telemetry and cases with REAL coverage and dormancy |
| `model` | `p_purchase_365d` | **DERIVED** | survival.py (Cox PH or rules baseline) | fitted only on REAL/DERIVED features; synthetic fields excluded entirely |
| `model` | `baseline hazard, covariate effects` | **DERIVED** | lifelines CoxPHFitter |  |
| `ranking` | `expected_award_value` | **DERIVED** | median of recent real awards | A-04 |
| `ranking` | `gross margin` | **ASSUMPTION** | pipeline/assumptions.py (assumption register) | A-01, anchored to a public filing |
| `ranking` | `engagement cost` | **ASSUMPTION** | pipeline/assumptions.py (assumption register) | A-02, no public source |
| `ranking` | `horizon` | **ASSUMPTION** | pipeline/assumptions.py (assumption register) | A-03 |
| `ranking` | `expected_value` | **DERIVED + ASSUMPTION** | pipeline/assumptions.py (assumption register) | two of its four terms are assumptions; see the sensitivity analysis |
| `plays` | `reason_code / evidence_ids` | **DERIVED** | plays.py | each code declares its own provenance; a SYNTHETIC code is never a recommended action |
<!-- END PROVENANCE_TABLE -->

---

## Sources

### 1. USAspending.gov — REAL, primary

Prime contract awards, FY2016 to present, filtered to enterprise storage and the
IT equipment around it.

- Endpoint: `POST https://api.usaspending.gov/api/v2/download/awards/`
- Licence: US Government work, public domain (17 U.S.C. §105)
- No API key required; used within the API's terms of service
- Retrieval dates and per-file SHA-256 hashes: `data/raw/manifest.json`

**Why the bulk download endpoint rather than the paged search endpoint.** We
built the paged version first (`--sample` still demonstrates it, printing one raw
response verbatim). At this scope it needs roughly 2,100 POSTs, and
USAspending's edge starts refusing connections outright well before that — every
request then fails in about a second with `RemoteDisconnected`, from `curl` as
readily as from Python. Hammering a free public API until it blocks you is both
rude and unreliable. The download endpoint is the API's own answer to a pull this
size, and it returns **286 columns per prime award instead of 23**. Several of
those extra columns are not a bonus, they are the difference between a workable
model and a compromised one:

| Column | Why it matters |
|---|---|
| `awarding_office_code` / `_name` | lets an account be an awarding **office**, the grain the brief asked for. The search endpoint does not expose office at all |
| `recipient_parent_uei` / `_name` | a real corporate-family rollup, held back from matching and used to **grade** it |
| `recipient_name` vs `recipient_name_raw` | USAspending's own normalised name beside the raw one: a second opinion on our normalisation |
| `manufacturer_of_goods` | the recipient's own SAM registration flag — a **ground-truth label** for the channel rule |
| `period_of_performance_current_end_date` | a renewal calendar built on the real PoP field |
| subawards CSV | prime-to-sub relationships, a real channel signal |

### 2. Backblaze Drive Stats — REAL

Quarterly drive-failure data, used to fit an empirical failure-hazard curve by
drive age, which then sets the refresh-risk threshold.

- Source: <https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data>
- Licence: published by Backblaze for public use with attribution
- Drive age is taken from SMART attribute 9 (power-on hours)
- Output: `data/processed/backblaze_hazard.csv` / `.json`

This replaces a hand-picked threshold ("assets over five years old are at risk")
with a hazard function measured across tens of millions of drive-days. The
observed annualised failure rate climbs from well under 1% in year 0 to several
times that by years 5–7. The refresh-risk age used by the `REFRESH_RISK` play is
the youngest bucket whose AFR reaches twice the observed floor — derived, not
chosen.

*Honest caveat:* the curve is non-monotonic past ~7 years. That is a fleet
composition effect — only certain models survive that long, and Backblaze
migrates fleets — not evidence that old drives get safer. Applying datacentre
drive hazards to federal storage assets is itself an assumption about
similarity, registered as **A-07**.

### 3. SEC EDGAR — REAL, benchmark only

- Filer: **Everpure, Inc.**, CIK `0001474432`, SIC 3572 (Computer Storage Devices)
- Source: SEC EDGAR XBRL company-facts API and the 10-K narrative
- Accessed with a declared User-Agent per SEC's automated-access policy; set
  `SEC_CONTACT` to your own address before running
- Output: `data/processed/sec_benchmarks.json`

Used **only** to anchor the gross-margin assumption (A-01) and to bound the
synthetic layer's plausibility (A-09). Reported gross margin, subscription ARR
growth and subscription net dollar retention are recorded with their filing
accession numbers and retrieval dates.

**These figures are never joined to the account data.** They describe one public
company and say nothing about the federal offices analysed here.

---

## Scope decisions

Included, as four PSC families plus a NAICS anchor:

| Family | Role |
|---|---|
| `hardware_core` | storage hardware — the install base itself |
| `hardware_adjacent` | the brief's "PSC 7035 and related 70-series" ADP/IT equipment, plus the modern compute and data-centre product codes that replaced them |
| `maintenance` | support attached to that equipment — drives the coverage calculation |
| `storage_as_a_service` | consumption replacing ownership — the substitution signal |
| NAICS `334112` | Computer Storage Device Manufacturing; unlike PSC, this code did not change across the period |

Deliberately excluded, with reasons:

- **`7030` / `7A20` / `7A21` — software.** Licences are not hardware install base.
- **`7045` — IT supplies.** Consumables: media, toner, cabling. Roughly 59,000
  mostly sub-$5,000 awards that would swamp the install-base signal.

---

## The FY2021 PSC break — the finding that shapes everything else

Federal PSC coding for IT migrated from the numeric 70-series and `J`/`D` service
codes to the alphanumeric `7A`–`7K` / `DA`–`DK` "IT and Telecom" family. Measured
against the award data itself:

| Awards in scope | FY2019 | FY2020 | FY2021 | FY2022 |
|---|---:|---:|---:|---:|
| legacy codes | 37,725 | 36,223 | 1,355 | **1** |
| modern codes | **0** | **0** | 17,794 | 13,921 |

The changeover is a single step at the FY2020/FY2021 boundary, not a glide.

This is not a curiosity, it is a trap. A code set drawn from one era alone would
show accounts "going dormant" and coverage "collapsing" in 2021 as a pure
artefact of recoding — and every one of those would be a false finding presented
with real data behind it. Every family in scope therefore **spans both eras**,
and each award carries a `psc_era` tag so the break can be shown rather than
smoothed over. The Data Quality page in the console plots it.

---

## What the dormancy rate does and does not tell you

The headline dormancy rate is **67.4%** of accounts with no in-scope award in 24
months. Quoting that number alone would be misleading, so here is the spread:

| Scope | Accounts | Dormant 24m | Dormant 36m | Median months idle |
|---|---:|---:|---:|---:|
| All in-scope codes *(headline)* | 3,096 | 67.4% | 59.8% | 60 |
| Hardware core only | 2,013 | 78.5% | 69.2% | 72 |
| Storage core + maintenance + aaS | 2,313 | 74.5% | 66.1% | 65 |
| **Modern-era codes only** | 1,553 | **38.8%** | 24.5% | **14** |

The reason they differ is the FY2021 recoding above — and a second, subtler
effect. The legacy 70-series was one broad "ADP equipment" bucket. The modern
taxonomy splits it across `7A`–`7K` by function, and we carry forward only the
storage, compute and data-centre slices. So scope *narrows* at FY2021 as well as
changing shape: an office that kept buying, say, output devices after 2021 leaves
our scope and reads as dormant without changing its behaviour. Award volume drops
about 45% across that boundary for reasons that are partly definitional.

**What is real:** the absence of an in-scope award is an observation, not a
simulation. **What is definitional:** the words "in scope". The modern-era-only
figure is the cleanest read of recent behaviour, because every recent award is
coded that way. Neither number is wrong; they answer different questions, and the
console shows all four.

---

## Three more places the honest answer is the less impressive one

**A play that fires on everything ranks nothing.** The hazard-derived refresh age
lands at 3 years, and over an eleven-year award window that makes `REFRESH_RISK`
fire on 94.5% of accounts — and `UNCOVERED` on 87.9%. Both triggers are real, but
a near-universal code carries almost no information for prioritisation. The plays
report therefore records each code's **selectivity** and flags the low-information
ones. The expected-value ranking does not weight reason codes.

**The margin sensitivity cannot fail.** Expected value is `P × V × margin − cost`.
Margin is a positive scalar and cost is a constant, so neither can change the
*order* of accounts — only how many clear zero. The 100% top-50 overlap across the
margin grid is arithmetic, not evidence of robustness, and the report says so. The
choices that genuinely reorder the ranking are tested separately:

| Variant | Top-50 overlap with base |
|---|---:|
| Probability from the rules baseline instead of the survival model | 70% |
| Expected award value as the **mean** of recent awards instead of the median | 62% |

So roughly a third of the top 50 depends on the model choice, and nearly 40% on
how award value is estimated. Those are the numbers worth arguing about.

**The survival model's headline AUC is flattered.** Over all 3,043 test accounts
it reaches 0.922 — but that population includes offices silent since 2017, which
are free negatives. On accounts still plausibly in play the honest figure is
**0.802**. Both are reported; the second is the one to quote.

---

## Entity resolution

Recipient names in USAspending are free text captured by thousands of contracting
officers. The same firm appears with different casing, punctuation, suffixes and
DBA variants. `recipient_uei` is the reliable identifier, and the brief expects it
to be missing on older records — UEI replaced DUNS in April 2022.

**What the data actually showed, and why it is worth saying.** In this extract
`recipient_uei` is populated on **100%** of the 270,924 awards, back to FY2016.
The bulk download back-fills it; the paged search endpoint does not, which is
where the "missing UEI" problem is usually met.

So the UEI-less matcher below fires on **zero** rows here, and the match report
says so rather than implying it did heavy lifting. It stays in the pipeline
because the search-endpoint path in this same repo does produce records with
gaps, and because its refusal logic is what stops a future run merging on a weak
name match. The resolution work that *is* load-bearing here is the rest of it:
collapsing 12,536 name spellings onto 11,245 UEI-backed entities, refusing to
merge distinct UEIs, and grading the same-parent candidates against a held-back
label.

The rules:

1. **UEI is authoritative.** Each distinct UEI is its own entity. Two UEIs are
   **never** merged, because two registrations are two legal entities even under
   one parent. They are reported as same-parent candidates instead.
2. A record with no UEI joins an entity **only on evidence**: exact normalised-name
   equality, or token-set similarity at or above the threshold. Every join is
   logged with its rule and score.
3. If the two best candidates sit within the ambiguity margin of each other, the
   match is **refused** and flagged for review.
4. Nothing merges silently.

Normalisation strips case, punctuation, a leading `THE`, DBA/FKA/AKA suffixes and
**legal-form tokens only** — `LLC`, `INC`, `CORP` and so on. Words like `GROUP`,
`SYSTEMS` and `TECHNOLOGIES` are deliberately kept, because stripping them merges
genuinely distinct firms.

### What makes this checkable

`recipient_parent_uei` is USAspending's own corporate-family rollup. It is never
used to do the matching — that would be circular — but it **is** used afterwards
to grade it:

- **same-parent precision** — of the UEI pairs our rule flagged, how many really
  do share a parent
- **same-parent recall** — of the real multi-UEI families present, how many the
  rule flagged at all
- **normalisation agreement** — how often our normalised name matches
  USAspending's own normalised `recipient_name`

Recall is expected to be low, and that is a property of the rule, stated rather
than hidden: identical normalised names catch `ACME CORP` vs `ACME INC`, not a
parent owning a differently-named subsidiary.

Awarding organisations resolve on **codes**, which are present on essentially
every record — `awarding_office_code` within `awarding_sub_agency_code`. The
free-text names are what vary, and the report lists every office whose name is
spelled more than one way.

Full artefact: [`reports/match_report.md`](reports/match_report.md) and
`reports/match_report.json`.

---

## The install-base model

From resolved records:

- **Accounts** — awarding office within sub-agency, with first award date, cohort
  year, total obligated, award count, region, size band.
- **Contracts** — awards with start, end, amount, PSC, channel, and a permalink
  back to the USAspending record.
- **Channel** — `direct` where the recipient makes goods, `partner` where it
  resells or integrates. See below.
- **Coverage** — for each hardware award, whether a maintenance award exists for
  the same account inside the coverage window. Yields the coverage rate and the
  uncovered-asset list.
- **Renewal calendar** — upcoming period-of-performance end dates, with
  co-termination clusters where an account has several contracts ending within 90
  days.
- **Cohort retention and expansion** — by first-award fiscal year, explicitly
  right-censored: every cell carries its own denominator and cells are omitted
  where the cohort has not been observable that long.
- **Whitespace** — PSC categories the account has never bought that its peer
  group (segment × size quintile) routinely does.
- **Dormancy** — no new award in 24+ months. **This is REAL, not simulated:** the
  absence of an award is an observation. Only the 24-month cut point is an
  assumption (A-06), and the console reports 12/18/24/36 months side by side.

### The channel rule, and its real error rate

The brief asks for a channel classification from recipient name and NAICS, with
its error rate documented. Normally there is no ground truth for this. Here there
is: `manufacturer_of_goods` is the recipient's **own SAM registration flag**, and
it plays no part in the rule's inputs — so scoring the rule against it is a
genuine out-of-rule evaluation, not a hand-wave.

- The **rule** (`channel_rule`) classifies from NAICS sector and explicit name
  tokens, and is kept as its own column so its error rate stays measurable.
- The **production** field (`channel`) uses the REAL SAM flag where it is
  populated and falls back to the rule only where it is not. `channel_source`
  records which applied for every award.

**The result: the rule does not work.** It is 51.9% accurate where always
guessing the majority class ("partner") is 77.4% accurate — so the rule specified
in the brief is *worse than no rule at all*. Direct precision is 18.8%.

The diagnosis is specific and it generalises: **NAICS on a federal award describes
what was bought, not what the recipient is.** 82,901 awards in scope carry NAICS
334111 "Electronic Computer Manufacturing" because a computer was purchased —
but most were placed with resellers and integrators, not manufacturers. Any rule
that reads vendor type out of the purchase's product code inherits that mismatch.

This is why the production `channel` field uses the SAM registration flag instead
(populated on ~100% of awards here), and why the rule is *reported* rather than
used. Full confusion matrix in `reports/channel_rule_report.json`.

#### A second attempt, from behaviour rather than codes — also rejected

If product codes can't tell you what a vendor is, maybe behaviour can. The
subaward graph offers a test: **if a prime passes most of an award's value to a
registered manufacturer, it was intermediating** — that is partner channel read
off what the firm actually did.

It does not hold. On the 962 scorable awards the signal is **27.7% accurate
against a 67.8% majority-class baseline** — so far below the baseline that it
carries information pointing the *opposite* way to the hypothesis.

The explanation is readable once seen: the firms that subcontract to
manufacturers are themselves large manufacturers and integrators with supply
chains. A small reseller has nothing to subcontract, and sits below the FFATA
reporting threshold so never appears in this data at all. **Subcontracting to a
manufacturer marks a big prime, not a middleman.**

Inverting the rule would report ~72% accuracy. We don't, and the report says why:
the direction would have been chosen *after* seeing the labels, on 962 awards,
with no held-out test. That is fitting to the evaluation set, not a validated
rule.

Two independent attempts to derive channel — one from product codes, one from
subcontracting behaviour — both fail. That is the argument for using the
registration flag rather than a clever derivation, and it is a more useful
finding than a rule that appeared to work. See `reports/subaward_report.json` and
the console's **Vendors** page.

**Coverage limit, stated plainly:** subaward reporting is threshold-gated, so
only 1,251 of 270,924 prime awards (**0.46%**) report one. The flow-through
signal could never have been a production classifier; there is nothing to
classify for the other 99.5%. What the graph *does* give is real and useful:
2,243 prime→subcontractor pairs and their values, which is what the Vendors page
is built on.

---

## The synthetic layer

Monthly utilisation telemetry, support case history and a customer health score
have no public federal equivalent. They are generated so the console can show a
complete workflow, and they are:

- **tagged SYNTHETIC everywhere they appear**, including in the UI
- **seed-fixed** and reproducible
- **conditioned on real records** so they move in plausible directions —
  utilisation scales with real obligations and decays with real recency; support
  case rates are Poisson with a rate driven by the account's REAL hardware count
  and REAL asset age through the REAL Backblaze hazard curve
- **excluded from the survival model and the expected-value ranking entirely**

Nothing in those three tables is evidence about the real world, and no finding in
the console rests on them.

---

## Plays

Twelve reason codes. Each declares its provenance, and each carries the evidence
rows that triggered it — real award identifiers, not a score.

| Reason code | Trigger | Provenance |
|---|---|---|
| `COTERM_WINDOW` | 2+ contracts ending within 90 days of each other | REAL |
| `RENEWAL_DUE` | period of performance ends inside the horizon | REAL |
| `UNCOVERED` | hardware award with no attached maintenance award | REAL |
| `LAPSED_SUPPORT` | bought maintenance before, none in 24 months | REAL |
| `REFRESH_RISK` | assets past the hazard-derived age threshold | DERIVED |
| `DORMANT_REACTIVATION` | no new award in 24+ months | REAL |
| `WHITESPACE` | peer-group category the account has never purchased | REAL |
| `CHANNEL_SHIFT` | moved from direct to partner purchasing, or the reverse | REAL |
| `COHORT_LAG` | expansion materially below its own cohort's median | DERIVED |
| `EXPANSION_SIGNAL` | buying rate accelerating against its own history | DERIVED |
| `VENDOR_CONCENTRATION` | ≥90% of obligations with a single vendor | REAL |
| `SUPPORT_RECOVERY` | elevated synthetic case load | **SYNTHETIC** |

Every account resolves to exactly one recommended action or an explicit
`NO_ACTION` with a stated reason. There is no silent middle.

**A SYNTHETIC-provenance code never becomes an account's recommended action.** It
can only appear alongside real ones.

---

## Modelling

### Time-to-next-award survival model

The target is the time from one **purchase episode** to the account's next.

That word matters. The first version of this modelled time between individual
awards and produced a median gap of **1.0 day** — because a contracting office
routinely signs dozens of delivery orders against the same vehicle on a single
day. It was modelling line-item batching, not repurchase. Collapsing to distinct
account-days (270,924 awards → 153,911 episodes) gives an interval that means
"how long until this office comes back to buy".

Accounts that have not bought again are **right-censored** — they contribute "at
least this long", not a zero. That is the reason for a survival model rather than
a binary label over an arbitrary window. Fitted with `lifelines` (`CoxPHFitter`);
the baseline cumulative hazard and every covariate effect are reported.

### Validation is time-based. There are no random splits anywhere.

- **Train** on gaps that *start* on or before the cutoff, censored *at* the
  cutoff, so the fit cannot see a single day beyond it.
- **Test** on accounts whose last episode precedes the cutoff: predict the
  probability of a further purchase within the horizon, conditioned on the gap
  already elapsed, then check what actually happened.

Reported **twice**, because the headline number flatters itself. Over all 3,043
test accounts the model reaches AUC 0.922 — but that population includes offices
silent since 2017, which are free negatives. Restricted to accounts still
plausibly in play (last purchase within 730 days), the honest figures are:

| | Survival model | Rules baseline |
|---|---:|---:|
| All test accounts (n=3,043) | **0.922** | 0.896 |
| Recently active (n=1,229) | **0.802** | 0.697 |

The second row is the decision-relevant one. The model's margin over the baseline
is *wider* there, not narrower, which is the reassuring direction.

Features that would **not** have been observable before the cutoff are listed in
`reports/survival_report.json` under `excluded_unobservable` and are not used —
dormancy as of today, coverage computed over the full history, expansion versus
cohort, anything derived from the as-of date, and every synthetic field.

### Calibration matters more than AUC

The ranking multiplies a predicted probability by a money value. A model that
orders accounts well but predicts the wrong *level* produces the wrong expected
value. So the deliverable is the decile plot of predicted versus observed
purchase rates — `reports/calibration.png` — with expected calibration error as
the single-number summary. **A miscalibrated model cannot support a value
ranking.**

### The honesty rule

The model is compared against a rules baseline — recency alone, which is what a
human would use. **If it does not beat that baseline on both discrimination and
calibration, the report says so and the ranking keeps the baseline.** The verdict
for this run is in the headline figures above and in
`reports/survival_report.json`. That result is worth more than a model that looks
better than it is.

### Expected-value ranking

```
Expected value = P(purchase within horizon)
               × expected award value
               × assumed gross margin
               − assumed engagement cost
```

**Two of those four terms are assumptions**, which is why the sensitivity
analysis is part of the deliverable rather than an appendix:

- `P(purchase)` — DERIVED, from whichever model survival.py selected
- expected award value — DERIVED, the median of the account's recent real awards
  (median, not mean: federal award amounts are heavy-tailed and one large
  instrument would otherwise set the whole account's value)
- gross margin — **ASSUMPTION A-01**, anchored to a public filing
- engagement cost — **ASSUMPTION A-02**, no public source, stated as such

The ranking is recomputed across margins from 30% to 80%, reporting both how
many accounts clear the cost floor and whether the *ordering* itself is stable
(`reports/ev_sensitivity.png`). If the ordering holds while the count moves, the
ranking is usable for prioritisation even where the absolute values are not
trusted.

Expected values are planning figures built on stated assumptions. **They are not
forecasts of revenue and must not be presented as such.**

Full register: [`ASSUMPTIONS.md`](ASSUMPTIONS.md).

---

## Known limits

- **Coverage is a lower bound.** Support bought outside the scoped PSC codes,
  folded into the hardware instrument itself, or delivered under a separate IDV
  reads as uncovered. The metric is *separately identifiable maintenance inside
  the scoped PSC set*, not true service attachment. A stricter same-vendor rate
  is reported alongside it.
- **Awards signed before FY2016 and still running are out of scope.** The pull
  uses `date_type=date_signed` so each award is counted once, in the year it was
  signed. At an eleven-year window this is immaterial for storage refresh cycles
  of three to seven years, but it is a boundary, not an absence.
- **Awarding office is missing on some records.** Those fall back to a
  sub-agency-level pseudo-office and are flagged with `office_missing`.
- **Same-parent recall is low by construction** — see entity resolution above.
- **The channel rule's error rate is measured against a self-reported
  registration flag**, which is itself imperfect. It is a real label, not a
  perfect one.
- **The subaward graph covers 0.46% of awards** and its channel hypothesis was
  rejected. It is kept for the vendor-network view, not as a classifier.
- **The channel rule specified in the brief does not work here**, and is reported
  rather than used. See the section above.
- **`REFRESH_RISK` and `UNCOVERED` fire on most accounts** and so cannot
  prioritise. Their selectivity is reported.
- **The modern PSC families are narrower than the legacy ones they replace**, so
  year-over-year comparisons across FY2021 mix a real trend with a definitional
  one. Dormancy is reported under four scopes for this reason.

---

## Tests — the claims above, enforced

Every promise this README makes is checked by `tests/test_invariants.py`, so a
promise that stops being true fails a test rather than quietly becoming a lie.

```bash
python tests/test_invariants.py     # standalone, no pytest needed
pytest tests/ -q                    # if you have pytest
python pipeline/run_all.py --test   # pipeline, then the checks
```

24 checks, covering:

- **no synthetic field reaches the model or the ranking** — asserted against
  `survival.FEATURES` and the reported expected-value terms, not just claimed
- **a SYNTHETIC reason code never becomes a recommended action**
- **every account resolves to one action or an explicit NO_ACTION with a reason**
- **two distinct UEIs are never merged** into one entity
- **validation is time-based**: the evaluation window starts after the cutoff,
  training is censored at it, and the unobservable-feature list is non-empty
- **the model is only used if it beat the baseline** — the honesty rule, enforced
- **every PSC family spans both coding eras**, so the FY2021 recoding can't fake
  dormancy
- **commercial numbers come only from the assumption register**
- **the ranking discloses that margin cannot reorder it**
- **a failing rule is described as failing** — the channel report must carry a
  baseline and a verdict, and a below-baseline signal must explain itself
- **dormancy is published under several scopes**, never as one number
- the app bundle carries the framing statement, contains no bare `NaN`, and the
  page stays under the 16MB limit

## Running it

Requires Python 3.11+.

```bash
pip install -r requirements.txt

# everything, in order; re-runs reuse the disk cache
python pipeline/run_all.py

# useful variants
python pipeline/run_all.py --skip-fetch     # never touch the network
python pipeline/run_all.py --from 4         # skip the fetch steps
python pipeline/run_all.py --only 11 12     # just the model and the ranking
python pipeline/run_all.py --test          # pipeline, then the invariant checks
python pipeline/fetch_usaspending.py --probe          # show the plan, download nothing
python pipeline/fetch_usaspending.py --sample 7K20 2023  # one raw response, verbatim
python pipeline/make_docs.py                # regenerate this file's tables
```

The raw cache is written to `data/raw/` with a manifest recording each file's
request body, row and column counts, SHA-256 and retrieval time. Re-running is
free; `--force-fetch` re-downloads.

## Deploying the console

The console is **one self-contained HTML file** — `app/dist/index.html`. No
server, no build step, no runtime fetch: the data bundle is inlined into a
`<script type="application/json">` block at build time by `pipeline/build_app.py`.
It is 4.59 MB raw and **0.56 MB over the wire** (88% smaller; any host gzips it),
so it deploys as an ordinary static site anywhere.

The repo ships config for the two easiest options. Both point at `app/dist` and
run no build command.

### Vercel — currently deployed here

<https://install-base-console.vercel.app>

(The original `install-base-reactivation-console.vercel.app` still resolves, so
older links do not break.)

Static serve, no build step, ~3s deploys. The response carries the headers set
in `vercel.json` (`X-Content-Type-Options`, `Referrer-Policy`, and a
revalidating cache policy on the entry point) and Vercel applies brotli, so the
4.59 MB page transfers in about 0.57 MB.

**Auto-deploy on push is not connected yet.** `vercel git connect` fails even
with the repository public, which points to Vercel's GitHub App not being
authorized on the account rather than to repository visibility:

> `Error: Failed to connect ssaaach/install-base-reactivation-console to project.`

Authorize it under **Project → Settings → Git → Connect Git Repository**, after
which every push to `main` redeploys. Until then, redeploy explicitly:

```bash
npx vercel deploy --prod --yes
```

`vercel.json` is already set up, so a fresh project is zero-config:

```bash
# one-off, from the repo root
npx vercel            # preview deployment
npx vercel --prod     # production
```

Or connect the repo at [vercel.com/new](https://vercel.com/new) — import
`install-base-reactivation-console` and deploy. Vercel reads `vercel.json` and
serves `app/dist` directly. Leave the framework preset as **Other**; there is
nothing to build.

`.vercelignore` keeps the pipeline, data and reports out of the upload. That
also stops Vercel spotting `requirements.txt` and trying to run a Python build.

### Netlify

`netlify.toml` sets `publish = "app/dist"` with an empty build command:

```bash
npx netlify-cli deploy           # draft URL
npx netlify-cli deploy --prod
```

### Cloudflare Pages

No config file needed — set **build command** to empty and **build output
directory** to `app/dist` in the dashboard.

### GitHub Pages

The repository is public, so Pages works on the free plan:

```bash
git subtree push --prefix app/dist origin gh-pages
```

### Redeploying after a pipeline run

The deployed file is a build artifact, and it is committed, so any host watching
the repo redeploys on push:

```bash
python pipeline/run_all.py        # regenerates app/dist/index.html
git add app/dist/index.html
git commit -m "Rebuild console"
git push
```

### One thing to decide before you deploy

**A static deploy is public.** Anyone with the URL can open the console, whether
or not the repo stays private — Vercel's password protection and Netlify's
site-wide password are paid features.

For this project that is fine, and worth stating plainly rather than assuming:
everything in the console is US Government public-domain procurement data,
Backblaze's published drive statistics, figures from a public SEC filing, or a
clearly-labelled synthetic layer. Recipient names, award PIIDs and obligated
amounts are all public record, and every award row links back to its own
USAspending page. There is nothing in it that is not already public.

What still matters is the framing: the page states on every screen that this is
federal procurement data used as a proxy, and **not any vendor's install base**.
Keep that statement wherever this is published.

## Repository layout

```
pipeline/
  config.py               scope, PSC families and eras, thresholds, as-of date
  fetch_usaspending.py    step 1  bulk download + disk cache + manifest
  fetch_backblaze.py      source 2, empirical failure hazard by drive age
  fetch_sec_benchmarks.py source 3, filing benchmarks (benchmark only)
  build_awards.py         flatten the downloads, dedup, missingness report
  entity_resolution.py    step 2  matching + the match report + its grading
  install_base.py         step 3  accounts, coverage, renewals, cohorts, whitespace
  provenance.py           step 4  the field-level provenance table
  synthetic_layer.py      step 5  SYNTHETIC telemetry, cases, health
  plays.py                step 6  reason codes and play assignment
  survival.py             step 7  survival model, time-based validation, calibration
  ranking.py              step 8  expected-value ranking + sensitivity
  export_app_data.py      step 9  one JSON bundle for the console
  assumptions.py          the assumption register, single source of truth
  make_docs.py            regenerates ASSUMPTIONS.md, docs/PROVENANCE.md, this file
  run_all.py              orchestrator
tests/
  test_invariants.py      24 checks on the claims this README makes
app/
  index.html              the console template (__APP_DATA__ placeholder)
  data/app_data.json      the bundle that gets inlined (gitignored; regenerable)
  dist/index.html         the built, self-contained page - this is what deploys
vercel.json               static deploy config: serves app/dist, no build
netlify.toml              the same for Netlify
.vercelignore             keeps the pipeline out of the upload
data/   raw/ interim/ processed/
reports/                  match report, channel rule, survival, sensitivity, plots
```

## Licences and citation

- **USAspending.gov** — US Government work, public domain (17 U.S.C. §105).
  Retrieved per `data/raw/manifest.json`.
- **Backblaze Drive Stats** — published by Backblaze for public use with
  attribution. Retrieval date in `data/processed/backblaze_hazard.json`.
- **SEC EDGAR** — public records. Filing accession numbers and retrieval dates in
  `data/processed/sec_benchmarks.json`.

Each dataset is used within its terms. Nothing here is any vendor's install base.
