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
import re
import sys
import time
from collections import Counter

import requests

from cities import get_city

# --- Configuration ---------------------------------------------------------
# Per-city data sources, field names and classification rules live in cities.py.
# Dataset URLs are built from each city's Socrata domain + dataset id at runtime.

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def dataset_url(cfg, which):
    """Socrata JSON endpoint for a city's 'building' or 'development' dataset."""
    return "%s/resource/%s.json" % (cfg["domain"], cfg[which]["dataset"])

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


# --- Generic field-match rule (Calgary, Vancouver) -------------------------
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


_RES_BUILDING = {
    "edmonton": _res_building_edmonton,
    "calgary": _res_building_fieldmatch,
    "vancouver": _res_building_fieldmatch,
    "toronto": _res_building_toronto,
}
_RES_DEVELOPMENT = {
    "edmonton": _res_development_edmonton,
    "calgary": _res_development_calgary,
    "vancouver": _desc_says_residential,  # no dev dataset; never invoked
    "toronto": _desc_says_residential,    # no dev dataset; never invoked
}


def is_residential_building(row, cfg):
    return _RES_BUILDING[cfg["residential"]["kind"]](row, cfg)


def is_residential_development(row, cfg):
    return _RES_DEVELOPMENT[cfg["residential"]["kind"]](row, cfg)


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


# --- OpenDataSoft fetch adapter (Vancouver) --------------------------------
ODS_PAGE = 100        # OpenDataSoft records API max page size
ODS_MAX_OFFSET = 10000  # records API caps offset+limit at 10000


def ods_build_where(text_fields):
    """ODSQL 'where' OR-ing every keyword variant across every text field.

    ODSQL uses double-quoted patterns with * wildcards (not SoQL's LIKE), e.g.
    projectdescription like "*ramp*". Matching is case-insensitive; the Python
    classify_keywords pass re-confirms, so this only needs to be a superset.
    """
    all_variants = [v for variants in KEYWORDS.values() for v in variants]
    clauses = []
    for field in text_fields:
        for kw in all_variants:
            k = kw.replace("\\", "\\\\").replace('"', '\\"')
            clauses.append('%s like "*%s*"' % (field, k))
    return "(" + " OR ".join(clauses) + ")"


def ods_fetch(cfg, which, text_fields):
    """Page an OpenDataSoft Explore v2.1 dataset, normalizing geo_point_2d into
    latitude/longitude so downstream code sees the same canonical fields."""
    dataset = cfg[which]["dataset"]
    base = "%s/api/explore/v2.1/catalog/datasets/%s/records" % (cfg["domain"], dataset)
    where = ods_build_where(text_fields)
    rows = []
    offset = 0
    while True:
        params = {"where": where, "limit": ODS_PAGE, "offset": offset}
        for attempt in range(4):
            try:
                r = requests.get(base, params=params, timeout=120)
                r.raise_for_status()
                break
            except requests.RequestException as e:
                wait = 2 ** (attempt + 1)
                print("  request failed (%s); retrying in %ss" % (e, wait), file=sys.stderr)
                time.sleep(wait)
        else:
            raise RuntimeError("Failed to fetch after retries: %s" % base)
        data = r.json()
        batch = data.get("results", [])
        for rec in batch:
            gp = rec.get("geo_point_2d")
            if isinstance(gp, dict):
                rec["latitude"], rec["longitude"] = gp.get("lat"), gp.get("lon")
            elif isinstance(gp, (list, tuple)) and len(gp) == 2:
                rec["latitude"], rec["longitude"] = gp[0], gp[1]
            rows.append(rec)
        total = data.get("total_count")
        print("  fetched %d (total %d / %s)" % (len(batch), len(rows), total))
        offset += ODS_PAGE
        if len(batch) < ODS_PAGE:
            break
        if offset >= ODS_MAX_OFFSET:
            print("  WARNING: hit OpenDataSoft %d-record window; some matches "
                  "may be omitted (switch to the exports API)" % ODS_MAX_OFFSET,
                  file=sys.stderr)
            break
        time.sleep(0.3)
    return rows


# --- CKAN fetch adapter (Toronto) ------------------------------------------
def ckan_compose_address(rec):
    """Toronto splits the address across four columns; join into one string."""
    parts = [str(rec.get(k) or "").strip()
             for k in ("STREET_NUM", "STREET_NAME", "STREET_TYPE", "STREET_DIRECTION")]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _ckan_add(merged, rec, resource, text_fields):
    """Dedupe by PERMIT_NUM (per-resource key fallback), synthesize address."""
    key = rec.get("PERMIT_NUM") or ("%s:%s" % (resource, rec.get("_id") or id(rec)))
    if key not in merged:
        rec["address"] = ckan_compose_address(rec)
        merged[key] = rec


def _ckan_datastore_query(base, resource, variants, merged, text_fields):
    """Per-keyword full-text `q` paging of a datastore-active CKAN resource."""
    for kw in variants:
        offset = 0
        while True:
            params = {"resource_id": resource, "q": kw, "limit": 1000, "offset": offset}
            for attempt in range(4):
                try:
                    r = requests.get(base, params=params, timeout=120)
                    r.raise_for_status()
                    break
                except requests.RequestException as e:
                    wait = 2 ** (attempt + 1)
                    print("  request failed (%s); retrying in %ss" % (e, wait), file=sys.stderr)
                    time.sleep(wait)
            else:
                raise RuntimeError("Failed to fetch after retries: %s" % base)
            result = r.json().get("result", {})
            recs = result.get("records", [])
            for rec in recs:
                _ckan_add(merged, rec, resource, text_fields)
            offset += len(recs)
            if not recs or offset >= result.get("total", 0):
                break
            time.sleep(0.2)


def ckan_download_filter(cfg, resource_id, text_fields, merged):
    """Stream a non-datastore CKAN CSV resource and keep only rows that match an
    accessibility keyword (used for Toronto's large pre-2017 Cleared CSV, which
    the datastore `q` API cannot reach)."""
    show = "%s/api/3/action/resource_show" % cfg["domain"]
    r = requests.get(show, params={"id": resource_id}, timeout=60)
    r.raise_for_status()
    url = r.json()["result"]["url"]
    print("  downloading flat CSV %s ..." % url.rsplit("/", 1)[-1])
    kept = 0
    with requests.get(url, stream=True, timeout=900) as resp:
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        reader = csv.DictReader(resp.iter_lines(decode_unicode=True))
        for rec in reader:
            if classify_keywords(rec, text_fields):
                before = len(merged)
                _ckan_add(merged, rec, resource_id, text_fields)
                kept += len(merged) - before
    print("  download %s: kept %d (unique total %d)" % (resource_id[:8], kept, len(merged)))


def ckan_fetch(cfg, which, text_fields):
    """Fetch matching permits from one or more CKAN resources and synthesize a
    single `address` field. `dataset` may be a list mixing datastore resource ids
    (queried by full-text `q`) and `{"id":…, "download": True}` flat-CSV resources
    (downloaded and filtered locally). Rows are deduped by PERMIT_NUM."""
    base = "%s/api/3/action/datastore_search" % cfg["domain"]
    resources = cfg[which]["dataset"]
    if isinstance(resources, (str, dict)):
        resources = [resources]
    variants, seen = [], set()
    for vs in KEYWORDS.values():
        for v in vs:
            if v not in seen:
                seen.add(v)
                variants.append(v)
    merged = {}
    for resource in resources:
        if isinstance(resource, dict) and resource.get("download"):
            ckan_download_filter(cfg, resource["id"], text_fields, merged)
        else:
            rid = resource["id"] if isinstance(resource, dict) else resource
            _ckan_datastore_query(base, rid, variants, merged, text_fields)
            print("  resource %s: unique so far %d" % (rid[:8], len(merged)))
    return list(merged.values())


def fetch_permits(cfg, which, text_fields):
    """Fetch matching permits for a city's 'building'/'development' dataset,
    dispatching on the city's open-data platform."""
    platform = cfg.get("platform", "socrata")
    if platform == "socrata":
        return fetch_all(dataset_url(cfg, which), build_where(text_fields))
    if platform == "opendatasoft":
        return ods_fetch(cfg, which, text_fields)
    if platform == "ckan":
        return ckan_fetch(cfg, which, text_fields)
    raise SystemExit("Unknown platform '%s' for %s" % (platform, cfg["display_name"]))


def classify_keywords(row, text_fields):
    """Return the set of canonical keyword labels present in this row."""
    blob = " ".join(str(row.get(f, "")) for f in text_fields).lower()
    hits = set()
    for label, variants in KEYWORDS.items():
        if any(v in blob for v in variants):
            hits.add(label)
    return hits


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
    # Confirm each row really contains a keyword. The server filter is a coarse
    # prefilter; SoQL 'like' is exact substring (no change), but OpenDataSoft's
    # text analyzer over-matches, so this drops those false positives.
    building = [r for r in building if classify_keywords(r, b_text)]
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
        development = [r for r in development if classify_keywords(r, d_text)]
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
