# Field-level provenance

Generated from `pipeline/provenance.py`. Every field in the final model carries one tag.

| Tag | Meaning | Fields |
|---|---|---:|
| **REAL** | sourced from a named public dataset, unmodified | 24 |
| **DERIVED** | computed from REAL fields only | 31 |
| **SYNTHETIC** | generated, because no public equivalent exists | 3 |
| **ASSUMPTION** | a stated commercial input, not a measurement | 3 |

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
