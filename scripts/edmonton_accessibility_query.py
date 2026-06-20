#!/usr/bin/env python3
"""Query City of Edmonton Open Data (Socrata) for building & development permits
containing accessibility-related keywords, for the SCI Alberta accessible
housing database.

Outputs two CSVs (one per dataset) and prints summary statistics:
  - record counts
  - keyword hit frequency
  - geographic distribution by neighbourhood

No API key required (public Socrata endpoints). Uses only the stdlib + requests.
"""
import csv
import os
import sys
import time
from collections import Counter

import requests

# --- Configuration ---------------------------------------------------------

BUILDING_URL = "https://data.edmonton.ca/resource/24uj-dj8v.json"
DEVELOPMENT_URL = "https://data.edmonton.ca/resource/2ccn-pwtu.json"

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Accessibility keywords. Each entry is the canonical label; the list of
# variants are the substrings actually matched (case-insensitive).
KEYWORDS = {
    "ramp": ["ramp"],
    "wheelchair": ["wheelchair", "wheel chair"],
    "accessible": ["accessible", "accessibility"],
    "barrier-free": ["barrier-free", "barrier free"],
    "grab bar": ["grab bar"],
    "lift": ["lift", "elevator"],
    "mobility": ["mobility"],
    "handicap": ["handicap"],
    "universal design": ["universal design"],
    "ada": ["ada compliant", "ada-compliant"],
    # High-value additions, especially for older years (2009-2015) that used
    # plainer construction wording rather than "barrier-free"/"accessible".
    "handrail": ["handrail", "hand rail"],
    "step-free entry": ["no-step", "no step", "step-free", "step free",
                        "level entry", "zero threshold", "no threshold",
                        "curbless"],
    "accessible bathroom": ["roll-in shower", "roll in shower", "walk-in tub",
                            "walk in tub", "curbless shower"],
    "wider doorway": ["widen door", "door widening", "wider door",
                      "widened door", "doorway widening"],
    "adaptable/visitable": ["adaptable", "visitable", "visitability"],
    "aging in place": ["aging in place", "age in place"],
    "automatic door": ["automatic door", "power door operator",
                       "powered door", "auto door operator"],
}

PAGE = 50000  # Socrata max rows per request

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
]


def building_code(row):
    """Extract the 3-digit code from a building_type like 'Apartments (310)'."""
    bt = row.get("building_type") or ""
    if "(" in bt and ")" in bt:
        return bt[bt.rfind("(") + 1:bt.rfind(")")].strip()
    return ""


def is_residential_building(row):
    if building_code(row) in RESIDENTIAL_BUILDING_CODES:
        return True
    jc = (row.get("job_category") or "").strip().lower()
    return jc in RESIDENTIAL_JOB_CATEGORIES


def is_residential_development(row):
    zoning = (row.get("zoning") or "").upper()
    # zoning may be comma-separated (e.g. "BE,IM"); residential if any token
    # starts with R but is not a non-residential R-prefixed code.
    for token in zoning.replace(",", " ").split():
        if token.startswith("R"):
            return True
    desc = (row.get("description_of_development") or "").lower()
    return any(term in desc for term in RESIDENTIAL_DESC_TERMS)


def soql_like_clause(field, variants):
    """Build a case-insensitive OR clause for one field across all variants."""
    parts = []
    for kw in variants:
        kw_esc = kw.replace("'", "''")
        parts.append("upper(%s) like upper('%%%s%%')" % (field, kw_esc))
    return "(" + " OR ".join(parts) + ")"


def build_where(text_fields):
    """OR every keyword variant across every supplied text field."""
    all_variants = [v for variants in KEYWORDS.values() for v in variants]
    field_clauses = []
    for field in text_fields:
        field_clauses.append(soql_like_clause(field, all_variants))
    return " OR ".join(field_clauses)


def fetch_all(url, where):
    """Page through a Socrata dataset using $where, returning all rows."""
    rows = []
    offset = 0
    while True:
        params = {"$where": where, "$limit": PAGE, "$offset": offset}
        for attempt in range(4):
            try:
                r = requests.get(url, params=params, timeout=120)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                wait = 2 ** (attempt + 1)
                print("  request failed (%s); retrying in %ss" % (e, wait), file=sys.stderr)
                time.sleep(wait)
        else:
            raise RuntimeError("Failed to fetch after retries: %s" % url)
        batch = r.json()
        rows.extend(batch)
        print("  fetched %d (total %d)" % (len(batch), len(rows)))
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


def classify_keywords(row, text_fields):
    """Return the set of canonical keyword labels present in this row."""
    blob = " ".join(str(row.get(f, "")) for f in text_fields).lower()
    hits = set()
    for label, variants in KEYWORDS.items():
        if any(v in blob for v in variants):
            hits.add(label)
    return hits


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
    os.makedirs(OUT_DIR, exist_ok=True)

    # Building permits: descriptive text in job_description + job_category.
    b_text = ["job_description", "job_category"]
    b_where = build_where(b_text)
    print("Querying building permits ...")
    building = fetch_all(BUILDING_URL, b_where)
    b_csv = os.path.join(OUT_DIR, "edmonton_building_permits_accessibility.csv")
    write_csv(b_csv, building)
    print("  saved -> %s" % b_csv)

    building_res = [r for r in building if is_residential_building(r)]
    b_res_csv = os.path.join(OUT_DIR, "edmonton_building_permits_accessibility_residential.csv")
    write_csv(b_res_csv, building_res)
    print("  saved -> %s (%d residential)" % (b_res_csv, len(building_res)))

    building_com = [r for r in building if not is_residential_building(r)]
    b_com_csv = os.path.join(OUT_DIR, "edmonton_building_permits_accessibility_commercial.csv")
    write_csv(b_com_csv, building_com)
    print("  saved -> %s (%d non-residential)" % (b_com_csv, len(building_com)))

    # Development permits: descriptive text in description_of_development.
    d_text = ["description_of_development"]
    d_where = build_where(d_text)
    print("\nQuerying development permits ...")
    development = fetch_all(DEVELOPMENT_URL, d_where)
    d_csv = os.path.join(OUT_DIR, "edmonton_development_permits_accessibility.csv")
    write_csv(d_csv, development)
    print("  saved -> %s" % d_csv)

    development_res = [r for r in development if is_residential_development(r)]
    d_res_csv = os.path.join(OUT_DIR, "edmonton_development_permits_accessibility_residential.csv")
    write_csv(d_res_csv, development_res)
    print("  saved -> %s (%d residential)" % (d_res_csv, len(development_res)))

    development_com = [r for r in development if not is_residential_development(r)]
    d_com_csv = os.path.join(OUT_DIR, "edmonton_development_permits_accessibility_commercial.csv")
    write_csv(d_com_csv, development_com)
    print("  saved -> %s (%d non-residential)" % (d_com_csv, len(development_com)))

    # --- Sample output ---
    def show_sample(name, rows, fields):
        print("\n----- SAMPLE: %s (first 5) -----" % name)
        for row in rows[:5]:
            addr = row.get("address", "")
            nbhd = row.get("neighbourhood", "")
            desc = " ".join(str(row.get(f, "")) for f in fields)[:120]
            print("  [%s | %s] %s" % (addr, nbhd, desc))

    show_sample("building permits", building, b_text)
    show_sample("development permits", development, d_text)

    # --- Summaries ---
    summarize("building permits (all)", building, b_text, "neighbourhood")
    summarize("building permits (residential only)", building_res, b_text, "neighbourhood")
    summarize("development permits (all)", development, d_text, "neighbourhood")
    summarize("development permits (residential only)", development_res, d_text, "neighbourhood")

    print("\nDone. CSVs written to %s" % OUT_DIR)


if __name__ == "__main__":
    main()
