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
import datetime
import os
import re
import sys
import time
import zipfile
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
# variants are the substrings actually matched (case-insensitive). French
# variants (for Montreal) are accessibility-specific on purpose -- generic words
# like porte/escalier/acces are deliberately excluded (measured as noise). Note
# also that bare "rampe" (= balcony/stair RAILING in Quebecois French) and "main
# courante" (= stair handrail) are NOT used: in Montreal data they overwhelmingly
# describe railings, not wheelchair ramps, so we match only "rampe d'acces".
KEYWORDS = {
    "ramp": ["ramp", "rampe d'accès", "rampe d'acces"],
    "wheelchair": ["wheelchair", "wheel chair", "fauteuil roulant"],
    "accessible": ["accessible", "accessibility",
                   "accessibilité", "accessibilite"],
    "barrier-free": ["barrier-free", "barrier free", "sans obstacle"],
    "grab bar": ["grab bar", "barre d'appui", "barre d appui"],
    "lift": ["lift", "elevator", "ascenseur", "élévateur", "elevateur",
             "plate-forme élévatrice", "plateforme elevatrice"],
    "mobility": ["mobility", "mobilité réduite", "mobilite reduite"],
    "handicap": ["handicap", "handicapé", "handicape"],
    "universal design": ["universal design", "design universel",
                         "conception universelle"],
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
    "adaptable/visitable": ["adaptable", "visitable", "visitability",
                            "logement adaptable"],
    "aging in place": ["aging in place", "age in place"],
    "automatic door": ["automatic door", "power door operator",
                       "powered door", "auto door operator",
                       "porte automatique", "ouvre-porte"],
}

# Matching is plain case-insensitive substring, EXCEPT for a few short English
# tokens that collide with foreign words as substrings. The English token "ramp"
# is a substring of French "rampe" (= a balcony/stair RAILING), which would flood
# Montreal with thousands of railing permits. For those tokens we use a regex
# instead; every other token stays a fast substring test, so English-city results
# are unchanged. (French wheelchair ramps are still caught by the explicit
# "rampe d'acces" variants below.)
_KEYWORD_REGEX = {
    # ramp / ramps / wheelchair ramp, but NOT French "rampe" (railing).
    "ramp": re.compile(r"ramp(?!e)"),
}


def _variant_matches(variant, blob):
    """True if `variant` is present in `blob` (already lowercased)."""
    rx = _KEYWORD_REGEX.get(variant)
    if rx is not None:
        return rx.search(blob) is not None
    return variant in blob


PAGE = 50000  # Socrata max rows per request

# Some open-data portals (e.g. donnees.montreal.ca) reject the default
# python-requests user-agent with HTTP 403; send a browser-like one.
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SCIA-Accessible-Housing-Finder)"}

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


def _res_building_textscan(row, cfg):
    # Generic: a permit is residential if any configured field names a dwelling.
    # Used by ArcGIS cities whose use/type fields are free-ish text
    # (Maple Ridge SubDescription, Mississauga BLDG_TYPE, Markham
    # FOLDERDESCRIPTION, Ottawa BLG_TYPE_F, etc.).
    fields = cfg["residential"]["residential_text_fields"]
    blob = " ".join(str(row.get(f, "")) for f in fields).lower()
    return any(term in blob for term in RESIDENTIAL_DESC_TERMS)


_RES_BUILDING = {
    "edmonton": _res_building_edmonton,
    "calgary": _res_building_fieldmatch,
    "vancouver": _res_building_fieldmatch,
    "toronto": _res_building_toronto,
    "textscan": _res_building_textscan,
}
_RES_DEVELOPMENT = {
    "edmonton": _res_development_edmonton,
    "calgary": _res_development_calgary,
    "vancouver": _desc_says_residential,  # no dev dataset; never invoked
    "toronto": _desc_says_residential,    # no dev dataset; never invoked
    "textscan": _desc_says_residential,   # ArcGIS cities are building-only
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
def _ckan_add(merged, rec, resource, compose):
    """Dedupe by PERMIT_NUM (per-resource key fallback). If `compose` is given
    (e.g. Toronto's STREET_NUM/NAME/TYPE/DIRECTION), join those into one
    `address`; otherwise leave the raw single address field (e.g. Montreal's
    `emplacement`) for the merge step to read via address_field."""
    key = rec.get("PERMIT_NUM") or ("%s:%s" % (resource, rec.get("_id") or id(rec)))
    if key not in merged:
        if compose:
            rec["address"] = re.sub(
                r"\s+", " ", " ".join(str(rec.get(k) or "").strip() for k in compose)).strip()
        merged[key] = rec


def _ckan_datastore_query(base, resource, variants, merged, compose):
    """Per-keyword full-text `q` paging of a datastore-active CKAN resource."""
    for kw in variants:
        offset = 0
        while True:
            params = {"resource_id": resource, "q": kw, "limit": 1000, "offset": offset}
            for attempt in range(4):
                try:
                    r = requests.get(base, params=params, headers=HTTP_HEADERS, timeout=120)
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
                _ckan_add(merged, rec, resource, compose)
            offset += len(recs)
            if not recs or offset >= result.get("total", 0):
                break
            time.sleep(0.2)


def ckan_download_filter(cfg, resource_id, text_fields, merged, compose):
    """Stream a non-datastore CKAN CSV resource and keep only rows that match an
    accessibility keyword (used for Toronto's large pre-2017 Cleared CSV, which
    the datastore `q` API cannot reach)."""
    show = "%s/api/3/action/resource_show" % cfg["domain"]
    r = requests.get(show, params={"id": resource_id}, headers=HTTP_HEADERS, timeout=60)
    r.raise_for_status()
    url = r.json()["result"]["url"]
    print("  downloading flat CSV %s ..." % url.rsplit("/", 1)[-1])
    kept = 0
    with requests.get(url, stream=True, headers=HTTP_HEADERS, timeout=900) as resp:
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        reader = csv.DictReader(resp.iter_lines(decode_unicode=True))
        for rec in reader:
            if classify_keywords(rec, text_fields):
                before = len(merged)
                _ckan_add(merged, rec, resource_id, compose)
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
    compose = cfg[which].get("address_compose")
    merged = {}
    for resource in resources:
        if isinstance(resource, dict) and resource.get("download"):
            ckan_download_filter(cfg, resource["id"], text_fields, merged, compose)
        else:
            rid = resource["id"] if isinstance(resource, dict) else resource
            _ckan_datastore_query(base, rid, variants, merged, compose)
            print("  resource %s: unique so far %d" % (rid[:8], len(merged)))
    return list(merged.values())


# --- ArcGIS REST fetch adapter (Maple Ridge, Mississauga, Markham, Ottawa) --
def arcgis_where(text_fields):
    """SQL-92 LIKE clause OR-ing every keyword variant across each text field
    (case-insensitive via UPPER). ArcGIS supports standard SQL in `where`."""
    clauses = []
    for field in text_fields:
        for variants in KEYWORDS.values():
            for kw in variants:
                k = kw.upper().replace("'", "''")
                clauses.append("UPPER(%s) LIKE '%%%s%%'" % (field, k))
    return "(" + " OR ".join(clauses) + ")"


def _arcgis_get(url, params, post=False):
    # The keyword `where` clause is long, so POST the query to avoid URL-length
    # limits (some servers 404/414 on very long GET query strings).
    for attempt in range(4):
        try:
            if post:
                r = requests.post(url, data=params, timeout=120)
            else:
                r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            wait = 2 ** (attempt + 1)
            print("  request failed (%s); retrying in %ss" % (e, wait), file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("Failed to fetch after retries: %s" % url)


def arcgis_fetch(cfg, which, text_fields):
    """Query an ArcGIS REST FeatureServer/MapServer layer, paging by OBJECTID
    keyset (works on all server versions). Normalizes point geometry (in
    EPSG:4326) into latitude/longitude and optionally composes a one-line
    address from several columns."""
    sub = cfg[which]
    layer = sub["dataset"].rstrip("/")           # full URL ending in /<layerId>
    query = layer + "/query"
    meta = _arcgis_get(layer, {"f": "json"})
    oid_field = meta.get("objectIdField") or "OBJECTID"
    page = min(meta.get("maxRecordCount") or 1000, 2000)
    where = arcgis_where(text_fields)
    compose = sub.get("address_compose")
    date_field = sub.get("date_field")

    def iso_date(v):
        # ArcGIS returns dates as epoch milliseconds; convert to YYYY-MM-DD.
        try:
            return datetime.datetime.utcfromtimestamp(v / 1000).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OverflowError, OSError):
            return v

    rows = []
    last_oid = -1
    while True:
        params = {
            "where": "(%s) AND %s > %d" % (where, oid_field, last_oid),
            "outFields": "*", "returnGeometry": "true", "outSR": 4326,
            "orderByFields": oid_field, "resultRecordCount": page, "f": "json",
        }
        data = _arcgis_get(query, params, post=True)
        feats = data.get("features", [])
        for ft in feats:
            attr = dict(ft.get("attributes", {}))
            geom = ft.get("geometry") or {}
            if geom.get("x") is not None and geom.get("y") is not None:
                attr["latitude"], attr["longitude"] = geom["y"], geom["x"]
            if compose:
                attr["address"] = re.sub(
                    r"\s+", " ", " ".join(str(attr.get(k) or "").strip() for k in compose)).strip()
            if date_field and isinstance(attr.get(date_field), (int, float)):
                attr[date_field] = iso_date(attr[date_field])
            oid = attr.get(oid_field)
            if isinstance(oid, (int, float)):
                last_oid = max(last_oid, int(oid))
            rows.append(attr)
        print("  fetched %d (total %d)" % (len(feats), len(rows)))
        if len(feats) < page:
            break
        time.sleep(0.2)
    return rows


# --- Excel-download adapter (Ottawa yearly permit spreadsheets) -------------
_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(ref):
    """'C5' -> 2 (0-based column index) from a cell reference."""
    letters = re.match(r"[A-Z]+", ref or "A").group()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx_sheets(data):
    """Parse every worksheet of an .xlsx into a list of grids (each a list of row
    lists), standard-library only (honours cell column refs + shared strings)."""
    import io
    import xml.etree.ElementTree as ET
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(_XLSX_NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(_XLSX_NS + "t")))
    grids = []
    for sname in sorted(n for n in z.namelist()
                        if re.match(r"xl/worksheets/sheet\d+\.xml$", n)):
        sd = ET.fromstring(z.read(sname)).find(_XLSX_NS + "sheetData")
        if sd is None:
            continue
        grid = []
        for row in sd.findall(_XLSX_NS + "row"):
            cells = {}
            for c in row.findall(_XLSX_NS + "c"):
                v = c.find(_XLSX_NS + "v")
                if v is None:
                    continue
                val = shared[int(v.text)] if c.get("t") == "s" else v.text
                cells[_col_index(c.get("r", "A1"))] = val
            grid.append([cells.get(i, "") for i in range(max(cells) + 1 if cells else 0)])
        grids.append(grid)
    return grids


def _read_xls_sheets(data):
    import xlrd
    book = xlrd.open_workbook(file_contents=data)
    return [[[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
            for sh in book.sheets()]


def _pick_permit_sheet(grids):
    """Return (grid, header_row_index, header) for the largest sheet that has a
    DESCRIPTION header (Ottawa files often have a combined sheet plus per-month
    sheets; the combined one is the largest)."""
    best = None
    for grid in grids:
        for i, r in enumerate(grid[:8]):
            up = [str(x).strip().upper() for x in r]
            if "DESCRIPTION" in up:
                if best is None or len(grid) > len(best[0]):
                    best = (grid, i, up)
                break
    return best


def excel_fetch(cfg, which, text_fields):
    """Download per-year permit spreadsheets (ArcGIS item /data), parse them, and
    normalize to common keys. Columns are mapped by header name (robust to
    sheet/format drift); the file's year stamps a permit_date so the year filter
    works (the rows themselves carry no issue date)."""
    sub = cfg[which]
    item_url = "https://www.arcgis.com/sharing/rest/content/items/%s"
    rows = []
    for entry in sub["dataset"]:
        item_id, year = entry["id"], entry.get("year", "")
        meta = _arcgis_get(item_url % item_id, {"f": "json"})
        name = (meta.get("name") or "").lower()
        data = requests.get((item_url % item_id) + "/data", timeout=300).content
        try:
            grids = _read_xls_sheets(data) if name.endswith(".xls") else _read_xlsx_sheets(data)
        except Exception as e:   # noqa: BLE001 - skip a bad file, keep the run
            print("  %s: parse failed (%s); skipping" % (year, e), file=sys.stderr)
            continue
        picked = _pick_permit_sheet(grids)
        if picked is None:
            print("  %s: no DESCRIPTION header; skipping" % year, file=sys.stderr)
            continue
        grid, header_row, header = picked
        idx = {h: j for j, h in enumerate(header)}

        def col(*aliases):
            for a in aliases:
                if a in idx:
                    return idx[a]
            return None

        c_desc = col("DESCRIPTION")
        c_num = col("ST #", "ST#", "ST NO", "STREET NUMBER")
        c_road = col("ROAD", "STREET", "STREET NAME")
        c_blg = col("BLG TYPE", "BLDG TYPE", "BUILDING TYPE")
        c_ward = col("WARD")
        kept0 = len(rows)
        for r in grid[header_row + 1:]:
            def g(c):
                return str(r[c]).strip() if c is not None and c < len(r) and r[c] is not None else ""
            num, road = g(c_num), g(c_road)
            rows.append({
                "description": g(c_desc),
                "blg_type": g(c_blg),
                "ward": g(c_ward),
                "address": re.sub(r"\s+", " ", ("%s %s" % (num, road)).strip()),
                "permit_date": "%s-01-01" % year if year else "",
            })
        print("  %s: %d rows (running %d)" % (year, len(rows) - kept0, len(rows)))
    return rows


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
    if platform == "arcgis":
        return arcgis_fetch(cfg, which, text_fields)
    if platform == "excel":
        return excel_fetch(cfg, which, text_fields)
    raise SystemExit("Unknown platform '%s' for %s" % (platform, cfg["display_name"]))


def classify_keywords(row, text_fields):
    """Return the set of canonical keyword labels present in this row."""
    blob = " ".join(str(row.get(f, "")) for f in text_fields).lower()
    hits = set()
    for label, variants in KEYWORDS.items():
        if any(_variant_matches(v, blob) for v in variants):
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
