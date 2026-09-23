# Entity resolution match report

Generated 2026-09-23T10:55:00.840680+00:00  
Source: USAspending.gov bulk award download, 270,924 distinct prime awards.


## Recipients (vendors)

| Measure | Value |
|---|---:|
| Distinct raw name strings (before) | 12,536 |
| Resolved entities (after) | 11,245 |
| Spelling variants collapsed | 1,291 |
| Collapse rate | 10.3% |
| UEI-backed entities | 11,245 |
| Entities with no UEI anywhere | 0 |
| Evidence-logged merges | 0 |
| Refused as ambiguous (need review) | 0 |
| Unresolved (own entity, flagged) | 0 |
| Same-parent candidates (reported, not merged) | 260 |
| One name under several UEIs | 211 |

Merge threshold: token-set similarity >= 0.92, ambiguity margin 0.02.


## How good is the matching? Scored against a held-back label

`recipient_parent_uei` is USAspending's own corporate-family rollup. It is never used to do the matching, only to grade it.

| Measure | Value |
|---|---:|
| Entities with a known parent | 11,201 |
| Real multi-UEI parent families | 367 |
| Pairs our rule flagged | 1,488 |
| True positives | 765 |
| False positives | 721 |
| **Same-parent precision** | **51.5%** |
| **Same-parent recall** | **24.3%** |
| Normalisation agreement with USAspending's own | 88.9% |

> Precision says how trustworthy our flagged same-parent pairs are. Recall is expected to be low: identical normalised names catch 'ACME CORP' vs 'ACME INC', not a parent owning a differently named subsidiary. Low recall here is a property of the rule, stated rather than hidden.


## Awarding organisations

| Measure | Value |
|---|---:|
| Awards carrying an office code | 100.0% |
| Distinct office name strings (before) | 2,802 |
| Code-backed offices (after) | 3,096 |
| Name strings reused across offices | 123 |
| Offices sharing a name with another | 416 |
| Offices with several name spellings | 0 |
| Distinct sub-agency name strings | 166 |
| Sub-agency entities | 175 |
| Sub-agencies with several spellings | 1 |

> Codes are authoritative and present on effectively every record, so organisations resolve on code rather than on name similarity. The account grain is the awarding OFFICE within its sub-agency.
