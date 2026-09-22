"""Step 2 - Entity resolution for recipients and awarding organisations.

Input : data/interim/awards_raw.parquet
Output: data/interim/recipient_clusters.parquet
        data/interim/org_clusters.parquet
        data/interim/awards_resolved.parquet
        reports/match_report.json / .md   - the artefact

The problem
-----------
Recipient names in USAspending are free text captured by thousands of contracting
officers. The same firm appears as "DELL MARKETING L.P.", "Dell Marketing LP",
"DELL MARKETING, L.P." and "DELL MARKETING LP DBA DELL EMC". recipient_uei is the
reliable identifier, and the brief expects it to be missing on older records
(UEI replaced DUNS in April 2022).

WHAT THE DATA ACTUALLY SHOWS, AND WHY THAT IS WORTH SAYING
-----------------------------------------------------------
In this extract `recipient_uei` is populated on **100%** of the 270k awards, back
to FY2016. USAspending's bulk download back-fills it; the paged search endpoint
does not, which is where the "missing UEI" problem is usually met.

So the UEI-less matcher below fires on **zero** rows here, and the match report
says so rather than implying it did heavy lifting. It stays in the pipeline for
three reasons: the search-endpoint path in this same repo does produce records
with gaps, a narrower or older pull would too, and its refusal logic is what
stops a future run from merging on a weak name match.

The resolution work that IS load-bearing here is the rest of it: collapsing
name spellings onto UEIs, refusing to merge distinct UEIs, and grading the
same-parent candidates against a held-back label.

Rules
-----
1. UEI is authoritative. Each distinct UEI is its own entity. We never merge two
   UEIs, because two registrations are two legal entities even under one parent.
   Instead we REPORT same-parent candidates.
2. A record with no UEI joins an entity only on evidence: exact normalised-name
   equality, or token-set similarity at or above MATCH_THRESHOLD. Every join is
   logged with its rule and score.
3. If the two best candidates sit within AMBIGUITY_MARGIN of each other we refuse
   to choose and flag the record for review.
4. Nothing merges silently.

What makes this checkable
-------------------------
The download carries recipient_parent_uei and recipient_parent_name, which is
USAspending's own corporate-family rollup. We never use it to do the matching -
that would be circular - but we DO use it afterwards to score ourselves:

  * same-parent precision: of the UEI pairs our name similarity flagged as likely
    same-parent, how many really do share a parent UEI?
  * same-parent recall: of the real multi-UEI parent families present, how many
    did our similarity flag at all?
  * normalisation agreement: how often our normalised name matches USAspending's
    own normalised recipient_name field.

Those three numbers turn "we did fuzzy matching" into something a reviewer can
argue with.

Awarding organisations resolve on codes, which are present on essentially every
record: awarding_office_code within awarding_sub_agency_code. The free-text names
are what vary.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C  # noqa: E402

IN = C.INTERIM / "awards_raw.parquet"
OUT_RECIP = C.INTERIM / "recipient_clusters.parquet"
OUT_ORG = C.INTERIM / "org_clusters.parquet"
OUT_AWARDS = C.INTERIM / "awards_resolved.parquet"
REPORT_JSON = C.REPORTS / "match_report.json"
REPORT_MD = C.REPORTS / "match_report.md"

MATCH_THRESHOLD = 0.92
AMBIGUITY_MARGIN = 0.02
BLOCK_DF_CAP = 400

LEGAL_SUFFIXES = {
    "LLC", "L L C", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO",
    "COMPANY", "LTD", "LIMITED", "LP", "L P", "LLP", "PLLC", "PC", "PA",
    "PLC", "SA", "NV", "BV", "GMBH", "AG", "AB", "AS", "OY", "SPA", "SRL",
    "PTY", "PVT", "PTE", "KG", "SE",
}
ABBREV = {
    "INTL": "INTERNATIONAL", "INTERNATL": "INTERNATIONAL",
    "TECH": "TECHNOLOGY", "TECHS": "TECHNOLOGIES", "TECHNOL": "TECHNOLOGY",
    "SVC": "SERVICE", "SVCS": "SERVICES", "SERV": "SERVICE",
    "SYS": "SYSTEMS", "SOLNS": "SOLUTIONS",
    "MFG": "MANUFACTURING", "ASSOC": "ASSOCIATES", "ASSOCS": "ASSOCIATES",
    "GOVT": "GOVERNMENT", "FED": "FEDERAL", "NATL": "NATIONAL",
    "AMER": "AMERICA", "USA": "UNITED STATES",
    "CONSULT": "CONSULTING", "ENGR": "ENGINEERING", "ENGRG": "ENGINEERING",
    "INDS": "INDUSTRIES", "PROD": "PRODUCTS",
    "DIST": "DISTRIBUTING", "DISTR": "DISTRIBUTING",
}
DBA_SPLIT = re.compile(r"\s+(?:DBA|D/?B/?A|DOING BUSINESS AS|FKA|F/?K/?A|AKA)\s+")
NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def log(m: str) -> None:
    print(f"[{_dt.datetime.now():%H:%M:%S}] {m}", flush=True)


# ------------------------------------------------------------ normalisation
def split_dba(raw: str) -> tuple:
    parts = DBA_SPLIT.split(str(raw).upper(), maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return str(raw).upper().strip(), None


def normalise(raw) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not str(raw).strip():
        return ""
    name, _alias = split_dba(raw)
    name = name.replace("&", " AND ")
    name = NON_ALNUM.sub(" ", name).strip()
    toks = [t for t in name.split() if t]
    if toks and toks[0] == "THE":
        toks = toks[1:]
    toks = " ".join(ABBREV.get(t, t) for t in toks).split()
    while len(toks) > 1 and toks[-1] in LEGAL_SUFFIXES:
        toks.pop()
    toks = [t for t in toks if t not in LEGAL_SUFFIXES] or toks
    return " ".join(toks)


def token_set_ratio(a: str, b: str) -> float:
    """fuzzywuzzy-style token_set_ratio on stdlib difflib."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    inter = sorted(ta & tb)
    if not inter:
        return SequenceMatcher(None, a, b).ratio()
    core = " ".join(inter)
    s1 = " ".join(inter + sorted(ta - tb))
    s2 = " ".join(inter + sorted(tb - ta))
    return max(SequenceMatcher(None, core, s1).ratio(),
               SequenceMatcher(None, core, s2).ratio(),
               SequenceMatcher(None, s1, s2).ratio())


# ------------------------------------------------------------ recipients
def resolve_recipients(df: pd.DataFrame) -> tuple:
    log("resolving recipients")
    raw_col = "recipient_name_raw" if "recipient_name_raw" in df.columns else "recipient_name"
    w = df[[raw_col, "recipient_uei"]].copy()
    w[raw_col] = w[raw_col].fillna("").astype(str)
    w["recipient_uei"] = w["recipient_uei"].fillna("").astype(str)

    name_awards = Counter(w[raw_col])
    name_ueis = defaultdict(Counter)
    for nm, uei in zip(w[raw_col], w["recipient_uei"]):
        if uei and nm:
            name_ueis[nm][uei] += 1

    distinct = [n for n in name_awards if n]
    norm_of = {n: normalise(n) for n in distinct}
    alias_of = {n: split_dba(n)[1] for n in distinct}
    log(f"  {len(distinct):,} distinct raw recipient names")

    # entities: one per UEI
    uei_names = defaultdict(Counter)
    for nm, ctr in name_ueis.items():
        for uei, k in ctr.items():
            uei_names[uei][nm] += k
    entity_of_uei = {u: f"E-{i:05d}" for i, u in enumerate(sorted(uei_names), 1)}
    entity_norms = defaultdict(set)
    for uei, ctr in uei_names.items():
        for nm in ctr:
            entity_norms[entity_of_uei[uei]].add(norm_of[nm])

    tok_df = Counter()
    for ent, norms in entity_norms.items():
        for nrm in norms:
            for t in set(nrm.split()):
                tok_df[t] += 1
    index = defaultdict(set)
    exact = defaultdict(set)
    for ent, norms in entity_norms.items():
        for nrm in norms:
            exact[nrm].add(ent)
            for t in set(nrm.split()):
                if tok_df[t] <= BLOCK_DF_CAP:
                    index[t].add(ent)

    ueiless = [n for n in distinct if not name_ueis.get(n)]
    log(f"  {len(entity_of_uei):,} UEI-backed entities; "
        f"{len(ueiless):,} names never carry a UEI")

    merges, ambiguous, unresolved = [], [], []
    name_to_entity = {nm: entity_of_uei[ctr.most_common(1)[0][0]]
                      for nm, ctr in name_ueis.items()}

    seq = 0
    for nm in sorted(ueiless, key=lambda n: -name_awards[n]):
        nrm = norm_of[nm]
        if not nrm:
            seq += 1
            own = f"U-{seq:05d}"
            name_to_entity[nm] = own
            unresolved.append({"raw_name": nm, "awards": name_awards[nm], "entity": own,
                               "reason": "name normalises to empty string"})
            continue
        if nrm in exact:
            cands = sorted(exact[nrm])
            if len(cands) == 1:
                name_to_entity[nm] = cands[0]
                merges.append({"raw_name": nm, "normalised": nrm, "entity": cands[0],
                               "rule": "exact_normalised_name", "score": 1.0,
                               "awards": name_awards[nm]})
                continue
            ambiguous.append({"raw_name": nm, "normalised": nrm, "awards": name_awards[nm],
                              "candidates": cands[:5], "scores": [1.0] * min(len(cands), 5),
                              "reason": "identical normalised name on several UEI entities"})
        else:
            cand = set()
            for t in set(nrm.split()):
                if tok_df.get(t, 0) <= BLOCK_DF_CAP:
                    cand |= index.get(t, set())
            scored = []
            for ent in cand:
                best = max(token_set_ratio(nrm, en) for en in entity_norms[ent])
                if best >= MATCH_THRESHOLD:
                    scored.append((best, ent))
            scored.sort(reverse=True)
            if scored and (len(scored) == 1
                           or scored[0][0] - scored[1][0] > AMBIGUITY_MARGIN):
                score, ent = scored[0]
                name_to_entity[nm] = ent
                merges.append({"raw_name": nm, "normalised": nrm, "entity": ent,
                               "rule": "token_set_similarity", "score": round(score, 4),
                               "awards": name_awards[nm],
                               "matched_against": sorted(entity_norms[ent])[:3]})
                continue
            if len(scored) > 1:
                ambiguous.append({
                    "raw_name": nm, "normalised": nrm, "awards": name_awards[nm],
                    "candidates": [e for _s, e in scored[:5]],
                    "scores": [round(s, 4) for s, _e in scored[:5]],
                    "reason": (f"top two within {AMBIGUITY_MARGIN} "
                               f"({scored[0][0]:.4f} vs {scored[1][0]:.4f})")})
        seq += 1
        own = f"U-{seq:05d}"
        name_to_entity[nm] = own
        entity_norms[own].add(nrm)
        unresolved.append({"raw_name": nm, "normalised": nrm, "awards": name_awards[nm],
                           "entity": own,
                           "reason": ("no UEI and ambiguous match; not merged"
                                      if nrm in exact else
                                      "no UEI and no match at threshold")})

    # Canonical name per entity = its most-awarded spelling. Built from the UEI
    # side as well as the name side: a UEI whose every spelling is dominated by
    # some other UEI still needs a name, or its awards come out nameless.
    ent_names = defaultdict(Counter)
    for uei, ctr in uei_names.items():
        ent = entity_of_uei[uei]
        for nm, k in ctr.items():
            ent_names[ent][nm] += k
    for nm, ent in name_to_entity.items():
        if ent.startswith("U-"):
            ent_names[ent][nm] += name_awards[nm]
    canonical = {e: c.most_common(1)[0][0] for e, c in ent_names.items()}

    same_parent = [{"normalised": nrm, "entities": sorted(ents),
                    "canonical_names": [canonical.get(e, "?") for e in sorted(ents)]}
                   for nrm, ents in exact.items() if len(ents) > 1]
    name_uei_conflicts = [{"raw_name": nm, "ueis": [u for u, _ in ctr.most_common()],
                           "counts": [k for _u, k in ctr.most_common()]}
                          for nm, ctr in name_ueis.items() if len(ctr) > 1]

    clusters = pd.DataFrame({
        "recipient_name_key": list(name_to_entity),
        "recipient_entity_id": [name_to_entity[n] for n in name_to_entity]})
    n_entities = len(set(entity_of_uei.values()) | set(
        e for e in name_to_entity.values() if e.startswith("U-")))
    clusters["recipient_normalised"] = clusters["recipient_name_key"].map(norm_of)
    clusters["recipient_canonical"] = clusters["recipient_entity_id"].map(canonical)
    clusters["dba_alias"] = clusters["recipient_name_key"].map(alias_of)
    clusters["awards_with_this_spelling"] = clusters["recipient_name_key"].map(name_awards)

    n_before, n_after = len(distinct), n_entities
    rep = {
        "distinct_raw_names_before": n_before,
        "entities_after": int(n_after),
        "uei_backed_entities": len(entity_of_uei),
        "uei_missing_entities": int(seq),
        "spelling_variants_collapsed": int(n_before - n_after),
        "collapse_rate": round((n_before - n_after) / max(n_before, 1), 4),
        "merge_threshold": MATCH_THRESHOLD,
        "ambiguity_margin": AMBIGUITY_MARGIN,
        "merge_count": len(merges),
        "merges": sorted(merges, key=lambda m: -m["awards"])[:500],
        "ambiguous_count": len(ambiguous),
        "ambiguous_requiring_review": sorted(ambiguous, key=lambda m: -m["awards"])[:300],
        "unresolved_count": len(unresolved),
        "unresolved": sorted(unresolved, key=lambda m: -m["awards"])[:300],
        "same_parent_candidate_count": len(same_parent),
        "same_parent_candidates": same_parent[:300],
        "name_uei_conflict_count": len(name_uei_conflicts),
        "name_uei_conflicts": sorted(name_uei_conflicts,
                                     key=lambda m: -len(m["ueis"]))[:200],
    }
    log(f"  {n_before:,} spellings -> {n_after:,} entities "
        f"({len(merges):,} evidence-logged merges, {len(ambiguous):,} refused)")
    return clusters, rep, {"norm_of": norm_of, "uei_names": uei_names,
                           "entity_of_uei": entity_of_uei, "exact": exact,
                           "canonical": canonical, "same_parent": same_parent}


# ------------------------------------------------------------ validation
def validate_against_parent(df: pd.DataFrame, aux: dict) -> dict:
    """Score our same-parent detection against USAspending's own parent rollup.

    The parent columns are never used to do the matching. They are held back
    purely to grade it.
    """
    if "recipient_parent_uei" not in df.columns:
        return {"available": False,
                "note": "recipient_parent_uei not present in this extract"}

    v = df[["recipient_uei", "recipient_parent_uei"]].dropna()
    v = v[(v["recipient_uei"].astype(str) != "") & (v["recipient_parent_uei"].astype(str) != "")]
    if v.empty:
        return {"available": False, "note": "no populated parent UEIs"}

    uei_to_parent = dict(zip(v["recipient_uei"], v["recipient_parent_uei"]))
    entity_of_uei = aux["entity_of_uei"]
    parent_of_entity = {entity_of_uei[u]: p for u, p in uei_to_parent.items()
                        if u in entity_of_uei}

    # truth: parent families containing more than one of our entities
    fam = defaultdict(set)
    for ent, par in parent_of_entity.items():
        fam[par].add(ent)
    true_multi = {p: e for p, e in fam.items() if len(e) > 1}
    true_pairs = set()
    for ents in true_multi.values():
        se = sorted(ents)
        for i in range(len(se)):
            for j in range(i + 1, len(se)):
                true_pairs.add((se[i], se[j]))

    # ours: pairs flagged by identical normalised name
    flagged_pairs = set()
    for grp in aux["same_parent"]:
        se = sorted(grp["entities"])
        for i in range(len(se)):
            for j in range(i + 1, len(se)):
                flagged_pairs.add((se[i], se[j]))

    # Only score pairs where both sides have a known parent, else "wrong" would
    # just mean "unlabelled".
    known = set(parent_of_entity)
    scorable = {p for p in flagged_pairs if p[0] in known and p[1] in known}
    tp = len(scorable & true_pairs)
    fp = len(scorable - true_pairs)
    fn = len(true_pairs - flagged_pairs)
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)

    # normalisation agreement against USAspending's own normalised name
    agree = None
    if "recipient_name" in df.columns and "recipient_name_raw" in df.columns:
        s = df[["recipient_name", "recipient_name_raw"]].dropna().drop_duplicates()
        s = s.head(40000)
        ours = s["recipient_name_raw"].map(normalise)
        theirs = s["recipient_name"].map(normalise)
        agree = round(float((ours == theirs).mean()), 4)

    return {
        "available": True,
        "method": ("parent UEI held back from matching and used only to grade the "
                   "same-parent candidates our name similarity flagged"),
        "entities_with_known_parent": len(parent_of_entity),
        "true_multi_uei_parent_families": len(true_multi),
        "true_same_parent_pairs": len(true_pairs),
        "flagged_pairs": len(flagged_pairs),
        "scorable_flagged_pairs": len(scorable),
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "same_parent_precision": round(precision, 4),
        "same_parent_recall": round(recall, 4),
        "normalisation_agreement_with_usaspending": agree,
        "reading": ("Precision says how trustworthy our flagged same-parent pairs are. "
                    "Recall is expected to be low: identical normalised names catch "
                    "'ACME CORP' vs 'ACME INC', not a parent owning a differently named "
                    "subsidiary. Low recall here is a property of the rule, stated "
                    "rather than hidden."),
    }


# ------------------------------------------------------------ organisations
def resolve_orgs(df: pd.DataFrame) -> tuple:
    """Awarding offices within sub-agencies. Codes are authoritative; names vary."""
    log("resolving awarding organisations")
    cols = ["awarding_agency_name", "awarding_sub_agency_code", "awarding_sub_agency_name",
            "awarding_office_code", "awarding_office_name"]
    w = df[[c for c in cols if c in df.columns]].copy()
    for c in w.columns:
        w[c] = w[c].fillna("").astype(str).str.strip()

    has_office = (w["awarding_office_code"] != "").mean() if "awarding_office_code" in w else 0.0

    sub_names = defaultdict(Counter)
    for code, nm in zip(w["awarding_sub_agency_code"], w["awarding_sub_agency_name"]):
        if code:
            sub_names[code][nm] += 1
    sub_canon = {c: ctr.most_common(1)[0][0] for c, ctr in sub_names.items()}

    off_names = defaultdict(Counter)
    for sc, oc, on in zip(w["awarding_sub_agency_code"], w["awarding_office_code"],
                          w["awarding_office_name"]):
        if oc:
            off_names[(sc, oc)][on] += 1
    off_canon = {k: ctr.most_common(1)[0][0] for k, ctr in off_names.items()}
    off_id = {k: f"A-{i:05d}" for i, k in enumerate(sorted(off_names), 1)}

    rows = []
    for (sc, oc), ctr in off_names.items():
        for on in ctr:
            rows.append({"awarding_sub_agency_code": sc, "awarding_office_code": oc,
                         "awarding_office_name": on,
                         "org_entity_id": off_id[(sc, oc)],
                         "office_canonical": off_canon[(sc, oc)],
                         "sub_agency_canonical": sub_canon.get(sc, "")})
    clusters = pd.DataFrame(rows)

    office_variants = [{"sub_agency_code": sc, "office_code": oc,
                        "canonical": off_canon[(sc, oc)],
                        "variant_count": len(ctr),
                        "variants": [n for n, _k in ctr.most_common()][:6]}
                       for (sc, oc), ctr in off_names.items() if len(ctr) > 1]
    sub_variants = [{"sub_agency_code": c, "canonical": sub_canon[c],
                     "variant_count": len(ctr),
                     "variants": [n for n, _k in ctr.most_common()][:6]}
                    for c, ctr in sub_names.items() if len(ctr) > 1]

    distinct_office_names = len({n for n in w.get("awarding_office_name", pd.Series(dtype=str)) if n})
    distinct_sub_names = len({n for n in w["awarding_sub_agency_name"] if n})
    # Several offices share one name string (e.g. many "W6QM MICC-FT EUSTIS"), so
    # there are FEWER name strings than offices. Counting that as a "collapse"
    # would be backwards; it is a name collision, and it is why offices resolve on
    # code rather than on name.
    name_to_offices = defaultdict(set)
    for (sc, oc), ctr in off_names.items():
        for on in ctr:
            if on:
                name_to_offices[on].add((sc, oc))
    colliding = {n: v for n, v in name_to_offices.items() if len(v) > 1}

    rep = {
        "office_code_present_rate": round(float(has_office), 4),
        "distinct_office_name_strings_before": distinct_office_names,
        "office_entities_after": len(off_id),
        "office_name_strings_shared_by_several_offices": len(colliding),
        "offices_sharing_a_name": sum(len(v) for v in colliding.values()),
        "office_name_collision_note": (
            "There are fewer distinct office NAME strings than office CODES: the "
            "same name is reused across offices. Resolving on name would have "
            "merged genuinely separate buying organisations, which is why the "
            "authority here is awarding_office_code."),
        "offices_with_multiple_name_spellings": len(office_variants),
        "office_name_variants": sorted(office_variants,
                                       key=lambda v: -v["variant_count"])[:120],
        "distinct_sub_agency_name_strings_before": distinct_sub_names,
        "sub_agency_entities_after": len(sub_names),
        "sub_agency_collapse_rate": round(
            (distinct_sub_names - len(sub_names)) / max(distinct_sub_names, 1), 4),
        "sub_agencies_with_multiple_name_spellings": len(sub_variants),
        "sub_agency_name_variants": sorted(sub_variants,
                                           key=lambda v: -v["variant_count"])[:120],
        "note": ("Codes are authoritative and present on effectively every record, so "
                 "organisations resolve on code rather than on name similarity. The "
                 "account grain is the awarding OFFICE within its sub-agency."),
    }
    log(f"  {distinct_office_names:,} office name strings -> {len(off_id):,} "
        f"code-backed offices ({has_office:.1%} of awards carry an office code)")
    return clusters, rep


# ------------------------------------------------------------ markdown
def write_markdown(rep: dict) -> None:
    r, o, v = rep["recipients"], rep["organisations"], rep["validation"]
    L = ["# Entity resolution match report\n",
         f"Generated {rep['generated_at']}  ",
         f"Source: USAspending.gov bulk award download, {rep['awards']:,} distinct prime awards.\n",
         "\n## Recipients (vendors)\n", "| Measure | Value |", "|---|---:|",
         f"| Distinct raw name strings (before) | {r['distinct_raw_names_before']:,} |",
         f"| Resolved entities (after) | {r['entities_after']:,} |",
         f"| Spelling variants collapsed | {r['spelling_variants_collapsed']:,} |",
         f"| Collapse rate | {r['collapse_rate']:.1%} |",
         f"| UEI-backed entities | {r['uei_backed_entities']:,} |",
         f"| Entities with no UEI anywhere | {r['uei_missing_entities']:,} |",
         f"| Evidence-logged merges | {r['merge_count']:,} |",
         f"| Refused as ambiguous (need review) | {r['ambiguous_count']:,} |",
         f"| Unresolved (own entity, flagged) | {r['unresolved_count']:,} |",
         f"| Same-parent candidates (reported, not merged) | {r['same_parent_candidate_count']:,} |",
         f"| One name under several UEIs | {r['name_uei_conflict_count']:,} |",
         f"\nMerge threshold: token-set similarity >= {r['merge_threshold']}, "
         f"ambiguity margin {r['ambiguity_margin']}.\n"]

    if v.get("available"):
        L += ["\n## How good is the matching? Scored against a held-back label\n",
              "`recipient_parent_uei` is USAspending's own corporate-family rollup. It is "
              "never used to do the matching, only to grade it.\n",
              "| Measure | Value |", "|---|---:|",
              f"| Entities with a known parent | {v['entities_with_known_parent']:,} |",
              f"| Real multi-UEI parent families | {v['true_multi_uei_parent_families']:,} |",
              f"| Pairs our rule flagged | {v['flagged_pairs']:,} |",
              f"| True positives | {v['true_positives']:,} |",
              f"| False positives | {v['false_positives']:,} |",
              f"| **Same-parent precision** | **{v['same_parent_precision']:.1%}** |",
              f"| **Same-parent recall** | **{v['same_parent_recall']:.1%}** |",
              f"| Normalisation agreement with USAspending's own | "
              f"{v['normalisation_agreement_with_usaspending']:.1%} |"
              if v.get("normalisation_agreement_with_usaspending") is not None else "",
              f"\n> {v['reading']}\n"]

    if r["merges"]:
        L += ["\n### Largest merges, with evidence\n",
              "| Raw name | Normalised | Entity | Rule | Score | Awards |",
              "|---|---|---|---|---:|---:|"]
        L += [f"| {m['raw_name'][:44]} | {m['normalised'][:34]} | {m['entity']} "
              f"| {m['rule']} | {m['score']:.3f} | {m['awards']:,} |"
              for m in r["merges"][:25]]
    if r["ambiguous_requiring_review"]:
        L += ["\n### Refused - ambiguous, require human review\n",
              "| Raw name | Candidates | Scores | Awards |", "|---|---|---|---:|"]
        L += [f"| {m['raw_name'][:40]} | {', '.join(m['candidates'][:3])} "
              f"| {', '.join(f'{s:.3f}' for s in m['scores'][:3])} | {m['awards']:,} |"
              for m in r["ambiguous_requiring_review"][:25]]

    L += ["\n## Awarding organisations\n", "| Measure | Value |", "|---|---:|",
          f"| Awards carrying an office code | {o['office_code_present_rate']:.1%} |",
          f"| Distinct office name strings (before) | {o['distinct_office_name_strings_before']:,} |",
          f"| Code-backed offices (after) | {o['office_entities_after']:,} |",
          f"| Name strings reused across offices | {o['office_name_strings_shared_by_several_offices']:,} |",
          f"| Offices sharing a name with another | {o['offices_sharing_a_name']:,} |",
          f"| Offices with several name spellings | {o['offices_with_multiple_name_spellings']:,} |",
          f"| Distinct sub-agency name strings | {o['distinct_sub_agency_name_strings_before']:,} |",
          f"| Sub-agency entities | {o['sub_agency_entities_after']:,} |",
          f"| Sub-agencies with several spellings | {o['sub_agencies_with_multiple_name_spellings']:,} |"]
    if o["office_name_variants"]:
        L += ["\n### Offices whose name is spelled several ways\n",
              "| Office code | Canonical | Variants |", "|---|---|---|"]
        L += [f"| {x['office_code']} | {x['canonical'][:40]} | {x['variant_count']}: "
              f"{' / '.join(n[:26] for n in x['variants'][:3])} |"
              for x in o["office_name_variants"][:20]]
    L += [f"\n> {o['note']}\n"]
    REPORT_MD.write_text("\n".join(x for x in L if x), encoding="utf-8")


# ------------------------------------------------------------ driver
def main() -> None:
    if not IN.exists():
        raise SystemExit(f"missing {IN} - run build_awards.py first")
    df = pd.read_parquet(IN)
    log(f"loaded {len(df):,} awards")

    recip, recip_rep, aux = resolve_recipients(df)
    validation = validate_against_parent(df, aux)
    org, org_rep = resolve_orgs(df)

    recip.to_parquet(OUT_RECIP, index=False)
    org.to_parquet(OUT_ORG, index=False)

    # ---- attach entity ids. UEI wins; a shared name string never drags a record
    # carrying its own UEI into the wrong entity.
    raw_col = "recipient_name_raw" if "recipient_name_raw" in df.columns else "recipient_name"
    out = df.copy()
    out["_name_key"] = out[raw_col].fillna("").astype(str)
    name_to_entity = dict(zip(recip["recipient_name_key"], recip["recipient_entity_id"]))
    uei_to_entity = {u: aux["entity_of_uei"][u] for u in aux["entity_of_uei"]}

    by_uei = out["recipient_uei"].fillna("").astype(str).map(uei_to_entity)
    by_name = out["_name_key"].map(name_to_entity)
    out["recipient_entity_id"] = by_uei.where(by_uei.notna(), by_name)
    out["recipient_canonical"] = out["recipient_entity_id"].map(aux["canonical"])
    out["recipient_normalised"] = out["_name_key"].map(aux["norm_of"])
    out = out.drop(columns=["_name_key"])

    org_key = org.set_index(["awarding_sub_agency_code", "awarding_office_code",
                             "awarding_office_name"])
    for c in ("awarding_sub_agency_code", "awarding_office_code", "awarding_office_name"):
        out[c] = out[c].fillna("").astype(str).str.strip()
    idx = pd.MultiIndex.from_arrays([out["awarding_sub_agency_code"],
                                     out["awarding_office_code"],
                                     out["awarding_office_name"]])
    out["org_entity_id"] = org_key["org_entity_id"].reindex(idx).to_numpy()
    out["office_canonical"] = org_key["office_canonical"].reindex(idx).to_numpy()
    out["sub_agency_canonical"] = org_key["sub_agency_canonical"].reindex(idx).to_numpy()

    unresolved_recip = int(out["recipient_entity_id"].isna().sum())
    unresolved_org = int(out["org_entity_id"].isna().sum())
    out.to_parquet(OUT_AWARDS, index=False)

    report = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "awards": int(len(df)),
        "awards_with_unresolved_recipient": unresolved_recip,
        "awards_with_unresolved_org": unresolved_org,
        "recipients": recip_rep,
        "organisations": org_rep,
        "validation": validation,
        "method": {
            "normalisation": ("uppercase; & -> AND; punctuation to space; leading THE "
                              "dropped; DBA/FKA/AKA split off; token abbreviations "
                              "expanded; legal-form suffixes removed (legal forms only "
                              "- GROUP, SYSTEMS, TECHNOLOGIES are kept)"),
            "similarity": "token-set ratio over difflib.SequenceMatcher",
            "blocking": f"inverted token index, tokens with document frequency <= {BLOCK_DF_CAP}",
            "authority": ("recipient_uei for vendors; awarding_office_code within "
                          "awarding_sub_agency_code for organisations"),
            "never_merged": ("two distinct UEIs are never merged; they are reported as "
                             "same-parent candidates and graded against recipient_parent_uei"),
        },
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, default=str))
    write_markdown(report)
    log(f"awards+entities -> {OUT_AWARDS}")
    log(f"match report -> {REPORT_JSON.name} / {REPORT_MD.name}")
    if validation.get("available"):
        log(f"same-parent precision {validation['same_parent_precision']:.1%}, "
            f"recall {validation['same_parent_recall']:.1%}")
    log(f"unresolved: {unresolved_recip:,} recipient, {unresolved_org:,} org")


if __name__ == "__main__":
    main()
