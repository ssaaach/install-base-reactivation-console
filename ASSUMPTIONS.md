# Assumption register

Every commercial number the console uses lives here. None of these is a finding. Where an assumption has a public anchor it is cited; where it has none, that is stated rather than disguised.

Generated from `pipeline/assumptions.py` - edit there, not here.

| ID | Assumption | Value | Kind |
|---|---|---|---|
| `A-01` | Gross margin on an incremental award | 0.7038 fraction of award value | **ASSUMPTION** |
| `A-02` | Engagement cost per account per cycle | 12,000 USD | **ASSUMPTION** |
| `A-03` | Opportunity horizon | 365 days | **ASSUMPTION** |
| `A-04` | Expected award value | derived at runtime USD per account | **DERIVED** |
| `A-05` | Coverage window | 365 days | **ASSUMPTION** |
| `A-06` | Dormancy threshold | 24 months | **ASSUMPTION** |
| `A-07` | Refresh-risk asset age | derived at runtime years | **DERIVED** |
| `A-08` | Peer adoption floor for whitespace | 0.4 fraction of peer accounts | **ASSUMPTION** |
| `A-09` | Expansion plausibility bounds for the synthetic layer | derived at runtime ratio | **ASSUMPTION** |

---

## A-01 - Gross margin on an incremental award

**Value:** 0.7038 fraction of award value  
**Kind:** ASSUMPTION

**Basis.** Anchored to a public enterprise-storage filer's reported gross margin.

**Source.** Everpure, Inc. (CIK 0001474432, SIC 3572 Computer Storage Devices) reported a 70.4% GAAP gross margin for the fiscal year ending 2026-02-01, SEC accession 0001474432-26-000027, retrieved 2026-09-20.

**Why this is an assumption.** A vendor's blended corporate gross margin is not the margin any particular federal award would carry. Federal pricing, channel discounts and contract vehicle fees all move it. The benchmark establishes an order of magnitude, nothing more.

**Sensitivity.** Ranking is recomputed across 0.30-0.80; see reports/ev_sensitivity.json.

## A-02 - Engagement cost per account per cycle

**Value:** 12,000 USD  
**Kind:** ASSUMPTION

**Basis.** Fully loaded cost of one seller-led engagement cycle.

**Source.** No public source. This is a planning figure, not a measurement.

**Why this is an assumption.** Nothing in public procurement data reveals the cost of pursuing an account. Any reader should substitute their own number; the ranking reads it from this register.

**Sensitivity.** Changes the EV floor, so it decides which accounts fall below zero.

## A-03 - Opportunity horizon

**Value:** 365 days  
**Kind:** ASSUMPTION

**Basis.** One year, matching the federal budget cycle.

**Source.** Chosen to align with the fiscal year; not derived from the data.

**Why this is an assumption.** The horizon sets what 'will they buy' means. A shorter horizon raises precision and shrinks the addressable set.

**Sensitivity.** The survival model is refitted per horizon; 365d is the reported case.

## A-04 - Expected award value

**Value:** derived at runtime USD per account  
**Kind:** DERIVED

**Basis.** Median of the account's most recent awards (REAL), which is robust to the very large outlier awards in federal data.

**Source.** Computed from USAspending award amounts. Not assumed.

**Why this is an assumption.** It is not - this one is derived from real records.

**Sensitivity.** Median vs mean changes the ranking; median is used deliberately.

## A-05 - Coverage window

**Value:** 365 days  
**Kind:** ASSUMPTION

**Basis.** A maintenance award within this distance counts as covering the hardware.

**Source.** Chosen. No public rule defines service attachment windows.

**Why this is an assumption.** Widening the window raises the coverage rate mechanically. The reported rate is only meaningful alongside the window.

**Sensitivity.** Coverage is also reported same-vendor-only as a stricter read.

## A-06 - Dormancy threshold

**Value:** 24 months  
**Kind:** ASSUMPTION

**Basis.** No new award in 24 months marks an account dormant.

**Source.** Threshold chosen; the underlying absence of awards is REAL and not simulated.

**Why this is an assumption.** The cut point is a choice. The observation that no award exists is not.

**Sensitivity.** Dormancy counts are reported at 12/18/24/36 months in the console.

## A-07 - Refresh-risk asset age

**Value:** derived at runtime years  
**Kind:** DERIVED

**Basis.** The youngest drive-age bucket whose annualised failure rate is twice the observed floor, taken from Backblaze Drive Stats.

**Source.** Backblaze Drive Stats quarterly data, ~27.8M drive-days. Replaces the hand-picked age threshold v1 used.

**Why this is an assumption.** It is derived, but applying consumer/datacentre drive hazards to federal storage assets is itself an assumption about similarity.

**Sensitivity.** Falls back to a flat 5-year threshold if the curve is unavailable.

## A-08 - Peer adoption floor for whitespace

**Value:** 0.4 fraction of peer accounts  
**Kind:** ASSUMPTION

**Basis.** A category counts as whitespace once 40% of peers already buy it.

**Source.** Chosen. Lower floors generate more, weaker, gaps.

**Why this is an assumption.** The floor decides how many gaps exist.

**Sensitivity.** Whitespace volume scales roughly inversely with the floor.

## A-09 - Expansion plausibility bounds for the synthetic layer

**Value:** derived at runtime ratio  
**Kind:** ASSUMPTION

**Basis.** Synthetic utilisation and health are kept inside commercially sane bounds.

**Source.** Anchored to public filings for order of magnitude. Subscription net dollar retention reported at 117% (FY2025), 113% (FY2026) bounds plausible expansion.

**Why this is an assumption.** The synthetic layer is generated. These bounds only stop it drifting somewhere implausible; they do not make it evidence.

**Sensitivity.** Synthetic fields are excluded from the model and the ranking entirely.
