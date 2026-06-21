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
