#!/usr/bin/env python3
"""Query a city's Open Data for building & development permits containing
accessibility-related keywords (ramps, lifts, wheelchair access, barrier-free,
...), for the SCI Alberta accessible housing database.

Orchestration only: the per-platform fetch adapters live in `fetchers.py`, the
keyword list + matching in `keywords.py`, and the per-city sources/field maps in
`cities.py`. This module classifies each fetched permit residential vs
non-residential and writes raw + residential + commercial CSVs per dataset.

No API key required (public endpoints). Stdlib + requests only.

Usage: python scripts/accessibility_query.py <city>
"""
import csv
import os
import re
import sys
from collections import Counter

from cities import get_city
from keywords import classify_keywords
from fetchers import fetch_permits, drop_excluded

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# --- Residential classification -------------------------------------------
# Building permits carry a building_type with a numeric code in parentheses.
# Residential dwelling codes: 0xx accessory (garage), 1xx single/low-density,
# 2xx semi-detached, 3xx row houses & apartments.
RESIDENTIAL_BUILDING_CODES = {
    "010",  # Detached Garage (accessory to a dwelling)
    "110",  # Single Detached / Backyard House
    "130",  # Mobile Home
    "210",  # Semi-Detached House
    "215",  # Semi-Detached Condo
    "310",  # Apartments
    "315",  # Apartment Condos
    "330",  # Row House
    "335",  # Row House Condo
}
# job_category values that are residential even when building_type is blank.
RESIDENTIAL_JOB_CATEGORIES = {
    "single, semi-detached & rowhousing",
    "house combination",
    "uncovered deck combination",
    "home improvement",
}

# Development permits have no building_type. Residential zones in Edmonton
# Zoning Bylaw 20001 start with "R" (RS, RSF, RM, RL, RR, ...).
# Plus a dwelling-term fallback against the description.
RESIDENTIAL_DESC_TERMS = [
    "single detached", "semi-detached", "semi detached", "duplex",
    "row house", "rowhouse", "town house", "townhouse", "apartment",
    "dwelling", "garden suite", "backyard house", "secondary suite",
    "garage suite", "multi-unit", "multi unit", "residential",
    # French dwelling terms (Montreal / future francophone cities).
    "logement", "habitation", "résidentiel", "residentiel", "unifamilial",
    "triplex", "plex", "résidence", "residence", "maison", "condo",
]


def building_code(row):
    """Extract the 3-digit code from a building_type like 'Apartments (310)'."""
    bt = row.get("building_type") or ""
    if "(" in bt and ")" in bt:
        return bt[bt.rfind("(") + 1:bt.rfind(")")].strip()
    return ""


def _desc_says_residential(row, cfg):
    """Shared fallback: does the permit's descriptive text name a dwelling?"""
    sub = cfg.get("development") or cfg["building"]
    desc = " ".join(str(row.get(f, "")) for f in sub["text_fields"]).lower()
    return any(term in desc for term in RESIDENTIAL_DESC_TERMS)


# --- Edmonton rules --------------------------------------------------------
def _res_building_edmonton(row, cfg):
    if building_code(row) in RESIDENTIAL_BUILDING_CODES:
        return True
    jc = (row.get("job_category") or "").strip().lower()
    return jc in RESIDENTIAL_JOB_CATEGORIES


def _res_development_edmonton(row, cfg):
    zoning = (row.get("zoning") or "").upper()
    # zoning may be comma-separated (e.g. "BE,IM"); residential if any token
    # starts with R (Edmonton Zoning Bylaw 20001: RS, RSF, RM, RL, RR, ...).
    for token in zoning.replace(",", " ").split():
        if token.startswith("R"):
            return True
    return _desc_says_residential(row, cfg)


# --- Generic field-match rule (Calgary, Vancouver, Austin) -----------------
def _res_building_fieldmatch(row, cfg):
    # Match one authoritative classification column against a set of residential
    # values. Calgary: permitclassmapped in {"Residential"}. Vancouver:
    # propertyuse in {"Dwelling Uses", ...}. Anything else -> commercial.
    # The column may be a string (Socrata) or a list (OpenDataSoft multi-value).
    val = row.get(cfg["residential"]["building_class_field"])
    values = val if isinstance(val, list) else [val]
    res = cfg["residential"]["building_residential_values"]
    return any(str(v).strip() in res for v in values if v is not None)


def _calgary_district_is_residential(token):
    t = token.strip().upper()
    # Land Use Bylaw 1P2007: R-* are residential districts (R-1, R-C1, R-CG,
    # R-G, RM-4, ...); M-* are Multi-Residential (M-C1, M-CG, M-G, M-H, M-X, ...).
    return t.startswith("R") or t.startswith("M-")


def _res_development_calgary(row, cfg):
    district = row.get(cfg["residential"]["development_district_field"]) or ""
    for token in re.split(r"[\s,;/]+", district):
        if token and _calgary_district_is_residential(token):
            return True
    return _desc_says_residential(row, cfg)


# --- Toronto rule ----------------------------------------------------------
def _res_building_toronto(row, cfg):
    # RESIDENTIAL is square-metres of residential occupancy covered by the
    # permit; > 0 means the work touches a dwelling. Fall back to the use fields
    # naming a dwelling (the sq-m column is often blank/0 on residential rows).
    field = cfg["residential"]["building_residential_numeric_field"]
    try:
        if float(row.get(field) or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    use = (str(row.get("CURRENT_USE") or "") + " " + str(row.get("PROPOSED_USE") or "")).lower()
    return any(term in use for term in RESIDENTIAL_DESC_TERMS)


def _res_building_textscan(row, cfg):
    # Generic: a permit is residential if any configured field names a dwelling.
    # Used by ArcGIS cities whose use/type fields are free-ish text
    # (Mississauga BLDG_TYPE, Markham FOLDERDESCRIPTION, Ottawa blg_type, etc.).
    fields = cfg["residential"]["residential_text_fields"]
    blob = " ".join(str(row.get(f, "")) for f in fields).lower()
    return any(term in blob for term in RESIDENTIAL_DESC_TERMS)


_RES_BUILDING = {
    "edmonton": _res_building_edmonton,
    "calgary": _res_building_fieldmatch,
    "vancouver": _res_building_fieldmatch,
    "austin": _res_building_fieldmatch,   # permit_class_mapped in {"Residential"}
    "toronto": _res_building_toronto,
    "textscan": _res_building_textscan,
}
_RES_DEVELOPMENT = {
    "edmonton": _res_development_edmonton,
    "calgary": _res_development_calgary,
    "vancouver": _desc_says_residential,  # no dev dataset; never invoked
    "austin": _desc_says_residential,     # no dev dataset; never invoked
    "toronto": _desc_says_residential,    # no dev dataset; never invoked
    "textscan": _desc_says_residential,   # ArcGIS cities are building-only
}


def is_residential_building(row, cfg):
    return _RES_BUILDING[cfg["residential"]["kind"]](row, cfg)


def is_residential_development(row, cfg):
    return _RES_DEVELOPMENT[cfg["residential"]["kind"]](row, cfg)


def keyword_filter(rows, text_fields, weak_alone):
    """Keep rows that match a keyword, dropping any whose ONLY matched keyword(s)
    are 'weak alone' for this city (e.g. Austin's "lift" -- ~99% noise
    standalone: house-raising "lifted residence", "Elevator Drive", forklifts).
    classify_keywords is run ONCE per row (the result feeds both checks)."""
    kept, dropped_weak = [], 0
    for r in rows:
        labels = classify_keywords(r, text_fields)
        if not labels:
            continue
        if weak_alone and labels <= weak_alone:
            dropped_weak += 1
            continue
        kept.append(r)
    return kept, dropped_weak


def strip_fields(rows, fields):
    """Drop the given columns from every row in place (privacy minimization)."""
    for row in rows:
        for f in fields:
            row.pop(f, None)


def write_csv(path, rows):
    """Write rows (list of dicts) to CSV, unioning all keys for the header."""
    if not rows:
        # still create an empty file with no header
        open(path, "w").close()
        return []
    fieldnames = []
    seen = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    return fieldnames


def summarize(name, rows, text_fields, nbhd_field):
    print("\n" + "=" * 60)
    print("%s: %d matching records" % (name.upper(), len(rows)))
    print("=" * 60)

    kw_counter = Counter()
    nbhd_counter = Counter()
    for row in rows:
        for label in classify_keywords(row, text_fields):
            kw_counter[label] += 1
        nbhd = (row.get(nbhd_field) or "(unknown)").strip() or "(unknown)"
        nbhd_counter[nbhd] += 1

    print("\nKeyword hits (records containing each keyword):")
    for label, n in kw_counter.most_common():
        print("  %-18s %d" % (label, n))

    print("\nTop 15 neighbourhoods:")
    for nbhd, n in nbhd_counter.most_common(15):
        print("  %-32s %d" % (nbhd, n))
    print("  (%d distinct neighbourhoods total)" % len(nbhd_counter))

    return kw_counter, nbhd_counter


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "edmonton"
    cfg = get_city(city)
    out_dir = os.path.join(OUT_DIR, city)
    os.makedirs(out_dir, exist_ok=True)
    b_cfg = cfg["building"]
    d_cfg = cfg.get("development")   # None for building-only cities (Vancouver)
    b_nbhd, b_addr = b_cfg["neighbourhood_field"], b_cfg["address_field"]

    def path(stem):
        return os.path.join(out_dir, stem)

    # Building permits.
    b_text = b_cfg["text_fields"]
    print("Querying %s building permits ..." % cfg["display_name"])
    building = fetch_permits(cfg, "building", b_text)
    # Source-of-truth `exclude` (case-insensitive, every platform; Socrata also
    # prefilters server-side) then keyword/weak-alone filtering.
    building = drop_excluded(building, b_cfg.get("exclude"))
    building, dropped_weak = keyword_filter(
        building, b_text, set(b_cfg.get("weak_alone_keywords", [])))
    if dropped_weak:
        print("  dropped %d rows whose only keyword(s) were weak-alone %s"
              % (dropped_weak, sorted(b_cfg.get("weak_alone_keywords", []))))
    strip_fields(building, b_cfg.get("drop_fields", []))  # drop unused name cols
    write_csv(path("building_permits_accessibility.csv"), building)

    building_res = [r for r in building if is_residential_building(r, cfg)]
    write_csv(path("building_permits_accessibility_residential.csv"), building_res)
    print("  building: %d total, %d residential" % (len(building), len(building_res)))

    building_com = [r for r in building if not is_residential_building(r, cfg)]
    write_csv(path("building_permits_accessibility_commercial.csv"), building_com)
    print("  building: %d non-residential" % len(building_com))

    # Development permits (skipped for building-only cities, with empty CSVs
    # written so the downstream merge step stays uniform).
    development = development_res = []
    if d_cfg:
        d_text = d_cfg["text_fields"]
        d_nbhd, d_addr = d_cfg["neighbourhood_field"], d_cfg["address_field"]
        print("\nQuerying %s development permits ..." % cfg["display_name"])
        development = fetch_permits(cfg, "development", d_text)
        development = drop_excluded(development, d_cfg.get("exclude"))
        development, _ = keyword_filter(
            development, d_text, set(d_cfg.get("weak_alone_keywords", [])))
        strip_fields(development, d_cfg.get("drop_fields", []))
        write_csv(path("development_permits_accessibility.csv"), development)

        development_res = [r for r in development if is_residential_development(r, cfg)]
        write_csv(path("development_permits_accessibility_residential.csv"), development_res)
        print("  development: %d total, %d residential" % (len(development), len(development_res)))

        development_com = [r for r in development if not is_residential_development(r, cfg)]
        write_csv(path("development_permits_accessibility_commercial.csv"), development_com)
        print("  development: %d non-residential" % len(development_com))
    else:
        for stem in ("development_permits_accessibility.csv",
                     "development_permits_accessibility_residential.csv",
                     "development_permits_accessibility_commercial.csv"):
            write_csv(path(stem), [])
        print("\n(%s has no development-permit dataset; skipped)" % cfg["display_name"])

    # --- Sample output ---
    def show_sample(name, rows, fields, addr_f, nbhd_f):
        print("\n----- SAMPLE: %s (first 5) -----" % name)
        for row in rows[:5]:
            desc = " ".join(str(row.get(f, "")) for f in fields)[:120]
            print("  [%s | %s] %s" % (row.get(addr_f, ""), row.get(nbhd_f, ""), desc))

    show_sample("building permits", building, b_text, b_addr, b_nbhd)

    # --- Summaries ---
    summarize("building permits (all)", building, b_text, b_nbhd)
    summarize("building permits (residential only)", building_res, b_text, b_nbhd)
    if d_cfg:
        show_sample("development permits", development, d_text, d_addr, d_nbhd)
        summarize("development permits (all)", development, d_text, d_nbhd)
        summarize("development permits (residential only)", development_res, d_text, d_nbhd)

    print("\nDone. CSVs written to %s" % out_dir)


if __name__ == "__main__":
    main()
