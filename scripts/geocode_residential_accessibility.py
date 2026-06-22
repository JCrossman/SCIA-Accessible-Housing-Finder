#!/usr/bin/env python3
"""Fill in coordinates for a city's merged accessibility address list.

For cities whose permits already carry latitude/longitude (e.g. Calgary), this
is a no-op that just records a 'coord_source' column. For cities whose building
permits lack coordinates (e.g. Edmonton), it geocodes the missing rows by
matching them against the city's 'Parcel Addresses' open dataset -- same Socrata
source, no API key, no third-party geocoder.

Field names, the parcel dataset and whether geocoding is needed all come from
scripts/cities.py.

Usage : python scripts/geocode_residential_accessibility.py <city> [residential|commercial]
Reads/Writes (in place): data/<city>/accessibility_<cut>_merged.csv
  adds a 'coord_source' column: permit | parcel_geocode | none
"""
import csv
import json
import os
import re
import sys
import time

import requests

from cities import get_city

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

BATCH = 20      # address tuples per request
TIMEOUT = 90
SLEEP = 1.0     # politeness delay between requests to avoid throttling

# Set by main() before any lookups: {"url", "house", "street", "lat", "lon"}.
_GCFG = None

# Street types / directions used to peel a street's core name off a one-line
# address (e.g. "5100 YONGE ST E" -> name "YONGE") for CKAN address matching.
_DIRS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_TYPES = {"ST", "STREET", "AVE", "AV", "AVENUE", "RD", "ROAD", "BLVD", "BOULEVARD",
          "DR", "DRIVE", "CRT", "CT", "COURT", "CRES", "CRESCENT", "LANE", "LN",
          "PL", "PLACE", "WAY", "TER", "TERRACE", "GDNS", "GARDENS", "SQ", "SQUARE",
          "PKWY", "PARKWAY", "HWY", "TRL", "TRAIL", "CIR", "CIRCLE", "GRV", "GROVE",
          "HTS", "HEIGHTS", "PARK", "PK", "GATE", "GT", "MEWS", "PATH", "RUN", "ROW"}


def parse_permit_address(addr):
    """Return (house_number, street_name) or (None, None) if unparseable."""
    a = (addr or "").upper().strip()
    if " - " not in a:
        return None, None
    left, street = a.split(" - ", 1)
    # left may be 'HOUSE' or 'SUITE, HOUSE' -> take the last number group.
    # house_number in the Parcel Addresses dataset is a NUMERIC column, so a
    # letter-suffixed house number (e.g. '7606A') throws a type-mismatch 400.
    # Strip the suffix and query the numeric base parcel (e.g. '7606').
    nums = re.findall(r"\d+", left)
    if not nums:
        return None, None
    return nums[-1], street.strip()


def soql_escape(s):
    return s.replace("'", "''")


def _query(pairs):
    """Run one OR-query for the given pairs. Returns (result_dict, ok)."""
    house, street = _GCFG["house"], _GCFG["street"]
    lat_f, lon_f = _GCFG["lat"], _GCFG["lon"]
    clauses = []
    for hn, st in pairs:
        clauses.append("(%s='%s' AND %s='%s')"
                       % (house, soql_escape(hn), street, soql_escape(st)))
    # Match any address record (PARCEL or SUITE). Commercial / multi-tenant
    # buildings are often only registered as SUITE; their coordinates are within
    # a few metres of the parcel, which is fine for mapping. We keep the first
    # match per address.
    where = "(" + " OR ".join(clauses) + ")"
    params = {
        "$select": "%s,%s,%s,%s" % (house, street, lat_f, lon_f),
        "$where": where,
        "$limit": 50000,
    }
    for attempt in range(4):
        try:
            r = requests.get(_GCFG["url"], params=params, timeout=TIMEOUT)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            wait = 2 ** (attempt + 1)
            print("  query failed (%s); retry in %ss" % (e, wait))
            time.sleep(wait)
    else:
        return {}, False
    out = {}
    for row in r.json():
        key = (row.get(house, "").upper(), row.get(street, "").upper())
        lat, lon = row.get(lat_f), row.get(lon_f)
        if lat and lon and key not in out:
            out[key] = (lat, lon)
    return out, True


def fetch_batch(pairs):
    """Query a batch; if it fails, split to isolate a poison address.
    Returns {(hn,st): (lat,lon)}."""
    result, ok = _query(pairs)
    if ok:
        return result
    if len(pairs) == 1:
        print("  single address failed permanently; skipping %s" % (pairs[0],))
        return {}
    print("  batch of %d failed; splitting" % len(pairs))
    mid = len(pairs) // 2
    out = {}
    out.update(fetch_batch(pairs[:mid]))
    time.sleep(SLEEP)
    out.update(fetch_batch(pairs[mid:]))
    return out


def _write(merged_csv, rows):
    fieldnames = list(rows[0].keys())
    if "coord_source" not in fieldnames:
        fieldnames.insert(fieldnames.index("has_coords") + 1, "coord_source")
    with open(merged_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _parse_oneline_address(addr):
    """Split a one-line address ('5100 YONGE ST E') into (number, core_name)
    by stripping a trailing direction and street type. Returns (None, None) if
    unparseable."""
    toks = (addr or "").upper().split()
    if not toks:
        return None, None
    m = re.match(r"(\d+)", toks[0])
    if not m:
        return None, None
    number = m.group(1)
    rest = toks[1:]
    if rest and rest[-1] in _DIRS:
        rest = rest[:-1]
    if rest and rest[-1] in _TYPES:
        rest = rest[:-1]
    name = " ".join(rest).strip()
    return (number, name) if name else (None, None)


def _ckan_geocode(merged_csv, rows, cfg):
    """Geocode one-line addresses against a CKAN address-point datastore
    (Toronto's Address Points). Matches on exact street number + a full-text
    street-name query, taking the first point's geometry coordinates."""
    g = cfg["geocode"]
    base = "%s/api/3/action/datastore_search" % g["domain"]
    resource, numf, namef = g["dataset"], g["number_field"], g["name_field"]
    cache = {}

    def _coords(rec):
        geom = rec.get("geometry")
        if isinstance(geom, str):          # datastore returns geometry as a JSON string
            try:
                geom = json.loads(geom)
            except ValueError:
                return None
        c = geom.get("coordinates") if isinstance(geom, dict) else None
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            return (c[1], c[0])   # GeoJSON [lon, lat] -> (lat, lon)
        return None

    def lookup(number, name):
        # Full-text `q` is not indexed on the address-point datastore, so filter
        # by exact street number and match the street name in Python.
        key = (number, name)
        if key in cache:
            return cache[key]
        # A street number can recur on many streets citywide, so fetch them all
        # (high limit) and match the street name in Python.
        params = {"resource_id": resource, "filters": json.dumps({numf: number}),
                  "limit": 2000}
        coords = None
        for attempt in range(3):
            try:
                r = requests.get(base, params=params, timeout=60)
                r.raise_for_status()
                recs = r.json().get("result", {}).get("records", [])
                # 1) exact core-name match (LINEAR_NAME, e.g. "Yonge")
                for rec in recs:
                    if str(rec.get("LINEAR_NAME") or "").strip().upper() == name:
                        coords = _coords(rec)
                        if coords:
                            break
                # 2) fallback: name appears in the full street name ("Yonge St")
                if not coords:
                    for rec in recs:
                        if name in str(rec.get(namef) or "").strip().upper():
                            coords = _coords(rec)
                            if coords:
                                break
                break
            except requests.RequestException:
                time.sleep(2 ** attempt)
        cache[key] = coords
        time.sleep(0.1)
        return coords

    matched = 0
    need = 0
    for i, row in enumerate(rows):
        if row.get("latitude") and row.get("longitude"):
            row["coord_source"] = "permit"
            continue
        need += 1
        number, name = _parse_oneline_address(row.get("address", ""))
        coords = lookup(number, name) if number and name else None
        if coords:
            row["latitude"], row["longitude"] = coords
            row["has_coords"] = "yes"
            row["coord_source"] = "address_points"
            matched += 1
        else:
            row["coord_source"] = "none"
        if i % 200 == 0:
            print("  geocoded %d/%d rows (matched %d, %d cached)"
                  % (i, len(rows), matched, len(cache)))

    _write(merged_csv, rows)
    total = sum(1 for r in rows if r.get("has_coords") == "yes")
    print("\nNewly geocoded         : %d" % matched)
    print("Still missing coords   : %d" % (need - matched))
    print("Total with coordinates : %d / %d (%.0f%%)"
          % (total, len(rows), 100.0 * total / len(rows) if rows else 0))
    print("Saved -> %s" % merged_csv)


def _arcgis_geocode(merged_csv, rows, cfg):
    """Geocode one-line addresses against an ArcGIS address-point layer (Ottawa's
    Municipal Address Points): match on exact street number + full road name,
    taking the point geometry (EPSG:4326)."""
    g = cfg["geocode"]
    query = g["layer"].rstrip("/") + "/query"
    numf, roadf = g["number_field"], g["road_field"]
    cache = {}

    def parse(addr):
        a = re.sub(r"\s+", " ", (addr or "").upper().strip())
        m = re.match(r"(\d+)\s+(.+)", a)   # leading number, then full road name
        return (m.group(1), m.group(2).strip()) if m else (None, None)

    def lookup(num, road):
        key = (num, road)
        if key in cache:
            return cache[key]
        where = "%s=%s AND UPPER(%s)='%s'" % (numf, num, roadf, road.replace("'", "''"))
        params = {"where": where, "outFields": numf, "returnGeometry": "true",
                  "outSR": 4326, "resultRecordCount": 1, "f": "json"}
        coords = None
        for attempt in range(3):
            try:
                r = requests.get(query, params=params, timeout=60)
                r.raise_for_status()
                feats = r.json().get("features", [])
                if feats:
                    geom = feats[0].get("geometry") or {}
                    if geom.get("x") is not None and geom.get("y") is not None:
                        coords = (geom["y"], geom["x"])
                break
            except requests.RequestException:
                time.sleep(2 ** attempt)
        cache[key] = coords
        time.sleep(0.1)
        return coords

    matched = need = 0
    for i, row in enumerate(rows):
        if row.get("latitude") and row.get("longitude"):
            row["coord_source"] = "permit"
            continue
        need += 1
        num, road = parse(row.get("address", ""))
        coords = lookup(num, road) if num and road else None
        if coords:
            row["latitude"], row["longitude"] = coords
            row["has_coords"] = "yes"
            row["coord_source"] = "address_points"
            matched += 1
        else:
            row["coord_source"] = "none"
        if i % 200 == 0:
            print("  geocoded %d/%d rows (matched %d, %d cached)"
                  % (i, len(rows), matched, len(cache)))

    _write(merged_csv, rows)
    total = sum(1 for r in rows if r.get("has_coords") == "yes")
    print("\nNewly geocoded         : %d" % matched)
    print("Still missing coords   : %d" % (need - matched))
    print("Total with coordinates : %d / %d (%.0f%%)"
          % (total, len(rows), 100.0 * total / len(rows) if rows else 0))
    print("Saved -> %s" % merged_csv)


def _no_geocode(merged_csv, rows):
    """Cities whose permits already carry coordinates: just label the source."""
    for row in rows:
        has = bool(row.get("latitude") and row.get("longitude"))
        row["coord_source"] = "permit" if has else "none"
    _write(merged_csv, rows)
    total = sum(1 for r in rows if r.get("has_coords") == "yes")
    print("No geocoding needed (permits carry coordinates).")
    print("Total with coordinates : %d / %d (%.0f%%)"
          % (total, len(rows), 100.0 * total / len(rows) if rows else 0))
    print("Saved -> %s" % merged_csv)


def main():
    global _GCFG
    city = sys.argv[1] if len(sys.argv) > 1 else "edmonton"
    cut = sys.argv[2] if len(sys.argv) > 2 else "residential"
    cfg = get_city(city)
    merged_csv = os.path.join(DATA_DIR, city, "accessibility_%s_merged.csv" % cut)
    rows = list(csv.DictReader(open(merged_csv, encoding="utf-8")))

    if not cfg["geocode"]["needed"]:
        _no_geocode(merged_csv, rows)
        return

    if cfg["geocode"].get("platform") == "ckan":
        _ckan_geocode(merged_csv, rows, cfg)
        return

    if cfg["geocode"].get("platform") == "arcgis":
        _arcgis_geocode(merged_csv, rows, cfg)
        return

    g = cfg["geocode"]
    _GCFG = {
        "url": "%s/resource/%s.json" % (cfg["domain"], g["dataset"]),
        "house": g["house_field"], "street": g["street_field"],
        "lat": g["lat_field"], "lon": g["lon_field"],
    }

    # Determine which rows need geocoding and parse their addresses.
    need = []          # list of (row, hn, st)
    to_lookup = set()  # unique (hn, st)
    for row in rows:
        has = bool(row.get("latitude") and row.get("longitude"))
        prior = row.get("coord_source", "")
        if has:
            # Preserve a prior parcel_geocode label across idempotent re-runs;
            # otherwise coords present on first load came from the permits.
            row["coord_source"] = prior if prior in ("permit", "parcel_geocode") else "permit"
        else:
            row["coord_source"] = ""
            hn, st = parse_permit_address(row.get("address", ""))
            if hn:
                need.append((row, hn, st))
                to_lookup.add((hn, st))

    print("Rows total            : %d" % len(rows))
    print("Already have coords    : %d" % sum(1 for r in rows if r["coord_source"] == "permit"))
    print("Need geocoding         : %d (%d unique addresses)" % (len(need), len(to_lookup)))

    # Batched lookups.
    lookup = list(to_lookup)
    geo = {}
    for i in range(0, len(lookup), BATCH):
        batch = lookup[i:i + BATCH]
        result = fetch_batch(batch)
        geo.update(result)
        print("  geocoded batch %d-%d: %d/%d matched (running %d)"
              % (i, i + len(batch), len(result), len(batch), len(geo)))
        time.sleep(SLEEP)

    # Apply results.
    matched = 0
    for row, hn, st in need:
        coords = geo.get((hn, st))
        if coords:
            row["latitude"], row["longitude"] = coords
            row["has_coords"] = "yes"
            row["coord_source"] = "parcel_geocode"
            matched += 1
        else:
            row["coord_source"] = "none"

    _write(merged_csv, rows)

    total_coords = sum(1 for r in rows if r.get("has_coords") == "yes")
    print("\nNewly geocoded         : %d" % matched)
    print("Still missing coords   : %d" % (len(need) - matched))
    print("Total with coordinates : %d / %d (%.0f%%)"
          % (total_coords, len(rows), 100.0 * total_coords / len(rows)))
    by_source = {}
    for r in rows:
        by_source[r["coord_source"]] = by_source.get(r["coord_source"], 0) + 1
    print("Coord source breakdown : %s" % by_source)
    print("Saved -> %s" % merged_csv)


if __name__ == "__main__":
    main()
