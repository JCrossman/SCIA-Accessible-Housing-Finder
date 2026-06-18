#!/usr/bin/env python3
"""Merge the residential building- and development-permit accessibility CSVs
into a single deduplicated, address-level list for the SCI Alberta accessible
housing database.

- Dedupes by normalized street address.
- Carries latitude/longitude from development permits (which have coordinates)
  onto matching building-permit addresses so the merged list is mappable.
- Unions the accessibility keywords and records which source datasets and how
  many permits contributed to each address.

Input : data/edmonton_building_permits_accessibility_residential.csv
        data/edmonton_development_permits_accessibility_residential.csv
Output: data/edmonton_accessibility_residential_merged.csv
"""
import csv
import os
import re

from edmonton_accessibility_query import classify_keywords

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
B_CSV = os.path.join(DATA_DIR, "edmonton_building_permits_accessibility_residential.csv")
D_CSV = os.path.join(DATA_DIR, "edmonton_development_permits_accessibility_residential.csv")
OUT_CSV = os.path.join(DATA_DIR, "edmonton_accessibility_residential_merged.csv")

B_TEXT = ["job_description", "job_category"]
D_TEXT = ["description_of_development"]


def normalize_address(addr):
    """Normalize an Edmonton permit address for matching.

    Forms seen: '11622 - 127 AVENUE NW', '11, 2930 - 51 AVENUE NW'
    (suite, house - street). Uppercase, drop suite prefix before the house
    number, collapse the ' - ' separator and all punctuation/space runs.
    """
    a = (addr or "").upper().strip()
    if not a:
        return ""
    # Drop a leading "suite," prefix like "11, 2930 - ..." -> "2930 - ..."
    a = re.sub(r"^\s*\d+\s*,\s*", "", a)
    # Replace the dash separator and any punctuation with single spaces.
    a = re.sub(r"[^\w]+", " ", a)
    return re.sub(r"\s+", " ", a).strip()


def load(path):
    return list(csv.DictReader(open(path, encoding="utf-8")))


def main():
    building = load(B_CSV)
    development = load(D_CSV)

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

    for r in building:
        addr = r.get("address", "")
        key = normalize_address(addr)
        if not key:
            continue
        rec = get(key, addr, r.get("neighbourhood"))
        rec["sources"].add("building")
        rec["n_building_permits"] += 1
        rec["keywords"] |= classify_keywords(r, B_TEXT)
        if r.get("row_id"):
            rec["building_row_ids"].append(r["row_id"])
        if r.get("permit_date"):
            rec["permit_dates"].append(r["permit_date"])
        if not rec["sample_description"]:
            rec["sample_description"] = (r.get("job_description") or "").strip()

    for r in development:
        addr = r.get("address", "")
        key = normalize_address(addr)
        if not key:
            continue
        rec = get(key, addr, r.get("neighbourhood"))
        rec["sources"].add("development")
        rec["n_development_permits"] += 1
        rec["keywords"] |= classify_keywords(r, D_TEXT)
        if r.get("city_file_number"):
            rec["development_file_numbers"].append(r["city_file_number"])
        if r.get("permit_date"):
            rec["permit_dates"].append(r["permit_date"])
        # Development permits carry coordinates + ward; prefer them.
        if r.get("latitude") and r.get("longitude"):
            rec["latitude"] = r["latitude"]
            rec["longitude"] = r["longitude"]
        if r.get("ward"):
            rec["ward"] = r["ward"]
        if not rec["sample_description"]:
            rec["sample_description"] = (r.get("description_of_development") or "").strip()

    # Flatten to rows.
    out = []
    for rec in merged.values():
        dates = sorted(d for d in rec["permit_dates"] if d)
        out.append({
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
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
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

    print("Merged residential accessibility list")
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
    print("\nSaved -> %s" % OUT_CSV)


if __name__ == "__main__":
    main()
