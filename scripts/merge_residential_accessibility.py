#!/usr/bin/env python3
"""Merge a city's building- and development-permit accessibility CSVs into a
single deduplicated, address-level list for the SCI Alberta accessible housing
database.

- Dedupes by normalized street address.
- Carries latitude/longitude from whichever permits supply them (development
  permits in Edmonton; both permit types in Calgary) so the list is mappable.
- Unions the accessibility keywords and records which source datasets and how
  many permits contributed to each address.

Field names, dataset locations and which permits carry coordinates all come
from scripts/cities.py, so this script is city-agnostic.

Usage : python scripts/merge_residential_accessibility.py <city> [residential|commercial]
Input : data/<city>/{building,development}_permits_accessibility_<cut>.csv
Output: data/<city>/accessibility_<cut>_merged.csv
"""
import csv
import os
import re
import sys

from cities import get_city
from edmonton_accessibility_query import classify_keywords

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def normalize_address(addr):
    """Normalize a permit address for matching/deduplication.

    Handles Edmonton forms ('11622 - 127 AVENUE NW', '11, 2930 - 51 AVENUE NW')
    and Calgary forms ('403 52 AV SW', '#705 3204 RIDEAU PL SW'). Uppercase,
    drop a leading '#unit' or 'suite,' prefix, then collapse the ' - ' separator
    and all punctuation/space runs.
    """
    a = (addr or "").upper().strip()
    if not a:
        return ""
    # Drop a leading unit prefix: "#705 3204 ..." -> "3204 ..."
    a = re.sub(r"^#\s*\w+\s+", "", a)
    # Drop a leading "suite," prefix like "11, 2930 - ..." -> "2930 - ..."
    a = re.sub(r"^\s*\d+\s*,\s*", "", a)
    # Replace the dash separator and any punctuation with single spaces.
    a = re.sub(r"[^\w]+", " ", a)
    return re.sub(r"\s+", " ", a).strip()


def load(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "edmonton"
    # "residential" (default) or "commercial" — picks input + output filenames.
    cut = sys.argv[2] if len(sys.argv) > 2 else "residential"
    cfg = get_city(city)
    b_cfg = cfg["building"]
    d_cfg = cfg.get("development") or {}   # {} for building-only cities (Vancouver)
    data_city = os.path.join(DATA_DIR, city)
    b_csv = os.path.join(data_city, "building_permits_accessibility_%s.csv" % cut)
    d_csv = os.path.join(data_city, "development_permits_accessibility_%s.csv" % cut)
    out_csv = os.path.join(data_city, "accessibility_%s_merged.csv" % cut)
    building = load(b_csv)
    development = load(d_csv)

    # key -> aggregated record
    merged = {}

    def get(key, display_addr, neighbourhood):
        if key not in merged:
            merged[key] = {
                "address": display_addr,
                "neighbourhood": (neighbourhood or "").strip(),
                "ward": "",
                "latitude": "",
                "longitude": "",
                "sources": set(),
                "n_building_permits": 0,
                "n_development_permits": 0,
                "keywords": set(),
                "building_row_ids": [],
                "development_file_numbers": [],
                "permit_dates": [],
                "sample_description": "",
            }
        return merged[key]

    def carry_coords(rec, row, sub_cfg):
        """Copy lat/lon onto the record if this dataset supplies them."""
        lat_f, lon_f = sub_cfg.get("lat_field"), sub_cfg.get("lon_field")
        if lat_f and row.get(lat_f) and row.get(lon_f) and not rec["latitude"]:
            rec["latitude"] = row[lat_f]
            rec["longitude"] = row[lon_f]

    b_text = b_cfg["text_fields"]
    d_text = d_cfg.get("text_fields") or []   # empty for building-only cities

    for r in building:
        addr = r.get(b_cfg["address_field"], "")
        key = normalize_address(addr)
        if not key:
            continue
        rec = get(key, addr, r.get(b_cfg["neighbourhood_field"]))
        rec["sources"].add("building")
        rec["n_building_permits"] += 1
        rec["keywords"] |= classify_keywords(r, b_text)
        if r.get(b_cfg["id_field"]):
            rec["building_row_ids"].append(r[b_cfg["id_field"]])
        if r.get(b_cfg["date_field"]):
            rec["permit_dates"].append(r[b_cfg["date_field"]])
        carry_coords(rec, r, b_cfg)
        if not rec["sample_description"]:
            rec["sample_description"] = (r.get(b_text[0]) or "").strip()

    for r in development:
        addr = r.get(d_cfg["address_field"], "")
        key = normalize_address(addr)
        if not key:
            continue
        rec = get(key, addr, r.get(d_cfg["neighbourhood_field"]))
        rec["sources"].add("development")
        rec["n_development_permits"] += 1
        rec["keywords"] |= classify_keywords(r, d_text)
        if r.get(d_cfg["id_field"]):
            rec["development_file_numbers"].append(r[d_cfg["id_field"]])
        if r.get(d_cfg["date_field"]):
            rec["permit_dates"].append(r[d_cfg["date_field"]])
        carry_coords(rec, r, d_cfg)
        ward_f = d_cfg.get("ward_field")
        if ward_f and r.get(ward_f):
            rec["ward"] = r[ward_f]
        if not rec["sample_description"]:
            rec["sample_description"] = (r.get(d_text[0]) or "").strip()

    # Flatten to rows.
    out = []
    for rec in merged.values():
        dates = sorted(d for d in rec["permit_dates"] if d)
        out.append({
            "city": city,
            "address": rec["address"],
            "neighbourhood": rec["neighbourhood"],
            "ward": rec["ward"],
            "latitude": rec["latitude"],
            "longitude": rec["longitude"],
            "has_coords": "yes" if rec["latitude"] else "no",
            "sources": "+".join(sorted(rec["sources"])),
            "total_permits": rec["n_building_permits"] + rec["n_development_permits"],
            "n_building_permits": rec["n_building_permits"],
            "n_development_permits": rec["n_development_permits"],
            "keywords": "; ".join(sorted(rec["keywords"])),
            "earliest_permit_date": dates[0] if dates else "",
            "latest_permit_date": dates[-1] if dates else "",
            "building_row_ids": "; ".join(rec["building_row_ids"]),
            "development_file_numbers": "; ".join(rec["development_file_numbers"]),
            "sample_description": rec["sample_description"],
        })

    # Sort: most permits first, then address.
    out.sort(key=lambda x: (-x["total_permits"], x["address"]))

    fieldnames = list(out[0].keys())
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out)

    # --- Summary ---
    total = len(out)
    both = sum(1 for x in out if x["sources"] == "building+development")
    b_only = sum(1 for x in out if x["sources"] == "building")
    d_only = sum(1 for x in out if x["sources"] == "development")
    with_coords = sum(1 for x in out if x["has_coords"] == "yes")
    dup_collapsed = (len(building) + len(development)) - total

    print("Merged %s %s accessibility list" % (cfg["display_name"], cut))
    print("=" * 50)
    print("Input permits      : %d building + %d development = %d"
          % (len(building), len(development), len(building) + len(development)))
    print("Unique addresses   : %d (%d duplicate permits collapsed)" % (total, dup_collapsed))
    print("  in both datasets : %d" % both)
    print("  building only    : %d" % b_only)
    print("  development only : %d" % d_only)
    print("With coordinates   : %d (%.0f%%)" % (with_coords, 100.0 * with_coords / total))
    print("\nAddresses with the most accessibility permits:")
    for x in out[:10]:
        print("  %2d permits  %-32s [%s]" % (x["total_permits"], x["address"], x["keywords"]))
    print("\nSaved -> %s" % out_csv)


if __name__ == "__main__":
    main()
