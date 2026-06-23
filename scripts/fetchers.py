#!/usr/bin/env python3
"""Per-platform permit fetch adapters -- the data-extraction layer.

Each city in cities.py declares a `platform`; `fetch_permits(cfg, which,
text_fields)` dispatches on it to one adapter:

  socrata       fetch_all   (SoQL build_where + offset paging)
  opendatasoft  ods_fetch   (ODSQL like + records API, geo_point_2d -> lat/lon)
  ckan          ckan_fetch  (per-keyword datastore_search q + flat-CSV download)
  arcgis        arcgis_fetch(SQL where POSTed to /query, OBJECTID keyset paging)
  excel         excel_fetch (download yearly ArcGIS .xlsx/.xls items, parse)

Every adapter returns a list of plain dict rows with the city's own column
names; the caller re-checks each row with classify_keywords (the server filter
is only a coarse prefilter) and maps fields via cities.py. Stdlib + requests.
"""
import csv
import datetime
import re
import sys
import time
import zipfile

import requests

from keywords import KEYWORDS, classify_keywords

PAGE = 50000  # Socrata max rows per request

# Some open-data portals (e.g. donnees.montreal.ca) reject the default
# python-requests user-agent with HTTP 403; send a browser-like one.
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SCIA-Accessible-Housing-Finder)"}


def dataset_url(cfg, which):
    """Socrata JSON endpoint for a city's 'building' or 'development' dataset."""
    return "%s/resource/%s.json" % (cfg["domain"], cfg[which]["dataset"])


# --- Server-side keyword prefilter + category exclusion (Socrata) -----------
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


def _apply_exclude(where, exclude):
    """AND a NOT-IN filter onto a Socrata 'where' to coarsely drop a permit
    category that matches a keyword but is not building accessibility (e.g.
    Austin's 'Driveway / Sidewalks' curb ramps). This is only a server-side
    prefilter -- drop_excluded() re-applies it for every platform. Comparison is
    case-insensitive (matching the keyword prefilter); NULL fields are kept."""
    if not exclude or not exclude.get("values"):
        return where
    field = exclude["field"]
    vals = ", ".join("upper('%s')" % str(v).replace("'", "''") for v in exclude["values"])
    return "(%s) AND (%s IS NULL OR upper(%s) NOT IN (%s))" % (where, field, field, vals)


def drop_excluded(rows, exclude):
    """Source-of-truth, platform-agnostic version of `exclude`: drop rows whose
    `field` value is in the excluded set (case-insensitive). No-op if `exclude`
    is unset or has no values, so any platform's city can use it."""
    if not exclude or not exclude.get("values"):
        return rows
    field = exclude["field"]
    vals = {str(v).strip().upper() for v in exclude["values"]}
    return [r for r in rows if str(r.get(field) or "").strip().upper() not in vals]


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


# --- CKAN fetch adapter (Toronto, Montreal) --------------------------------
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


# --- ArcGIS REST fetch adapter (Mississauga, Markham, Ottawa) ---------------
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
    dispatching on the city's open-data platform. The Socrata path also applies
    the optional `exclude` as a server-side prefilter; every platform's rows are
    re-filtered by drop_excluded() in the caller."""
    platform = cfg.get("platform", "socrata")
    if platform == "socrata":
        where = _apply_exclude(build_where(text_fields), cfg[which].get("exclude"))
        return fetch_all(dataset_url(cfg, which), where)
    if platform == "opendatasoft":
        return ods_fetch(cfg, which, text_fields)
    if platform == "ckan":
        return ckan_fetch(cfg, which, text_fields)
    if platform == "arcgis":
        return arcgis_fetch(cfg, which, text_fields)
    if platform == "excel":
        return excel_fetch(cfg, which, text_fields)
    raise SystemExit("Unknown platform '%s' for %s" % (platform, cfg["display_name"]))
