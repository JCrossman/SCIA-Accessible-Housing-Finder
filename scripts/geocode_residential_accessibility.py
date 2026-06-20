#!/usr/bin/env python3
"""Geocode the residential accessibility addresses that lack coordinates by
matching them against the City of Edmonton 'Parcel Addresses' open dataset
(ut27-nrpn) -- same Socrata source, no API key, no third-party geocoder.

Reads  : data/edmonton_accessibility_residential_merged.csv
Writes : data/edmonton_accessibility_residential_merged.csv (in place, adds a
         'coord_source' column: development | parcel_geocode | none)

Permit addresses look like '11622 - 127 AVENUE NW' or, with a suite prefix,
'101, 2755 - 109 STREET NW'. We parse house_number + street_name and look up
the PARCEL point in the address dataset.
"""
import csv
import os
import re
import time

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MERGED_CSV = os.path.join(DATA_DIR, "edmonton_accessibility_residential_merged.csv")
ADDRESS_URL = "https://data.edmonton.ca/resource/ut27-nrpn.json"

BATCH = 20      # address tuples per request
TIMEOUT = 90
SLEEP = 1.0     # politeness delay between requests to avoid throttling


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
    clauses = []
    for hn, st in pairs:
        clauses.append("(house_number='%s' AND street_name='%s')"
                       % (soql_escape(hn), soql_escape(st)))
    # Match any address record (PARCEL or SUITE). Commercial / multi-tenant
    # buildings are often only registered as SUITE; their coordinates are within
    # a few metres of the parcel, which is fine for mapping. We keep the first
    # match per address.
    where = "(" + " OR ".join(clauses) + ")"
    params = {
        "$select": "house_number,street_name,latitude,longitude",
        "$where": where,
        "$limit": 50000,
    }
    for attempt in range(4):
        try:
            r = requests.get(ADDRESS_URL, params=params, timeout=TIMEOUT)
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
        key = (row.get("house_number", "").upper(), row.get("street_name", "").upper())
        lat, lon = row.get("latitude"), row.get("longitude")
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


def main():
    import sys
    # Optional path to a merged CSV (defaults to the residential one).
    merged_csv = sys.argv[1] if len(sys.argv) > 1 else MERGED_CSV
    rows = list(csv.DictReader(open(merged_csv, encoding="utf-8")))

    # Determine which rows need geocoding and parse their addresses.
    need = []          # list of (row, hn, st)
    to_lookup = set()  # unique (hn, st)
    for row in rows:
        has = bool(row.get("latitude") and row.get("longitude"))
        prior = row.get("coord_source", "")
        if has:
            # Preserve a prior parcel_geocode label across idempotent re-runs;
            # otherwise coords present on first load came from dev permits.
            row["coord_source"] = prior if prior in ("development", "parcel_geocode") else "development"
        else:
            row["coord_source"] = ""
        if not has:
            hn, st = parse_permit_address(row.get("address", ""))
            if hn:
                need.append((row, hn, st))
                to_lookup.add((hn, st))

    print("Rows total            : %d" % len(rows))
    print("Already have coords    : %d" % sum(1 for r in rows if r["coord_source"] == "development"))
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

    # Rewrite CSV with the new column inserted after longitude.
    fieldnames = list(rows[0].keys())
    if "coord_source" not in fieldnames:
        fieldnames.insert(fieldnames.index("has_coords") + 1, "coord_source")
    with open(merged_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

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
