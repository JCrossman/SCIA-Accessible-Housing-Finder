#!/usr/bin/env python3
"""Export the residential accessibility addresses that could not be geocoded
(coord_source == 'none') for manual coordinate cleanup.

Includes the parsed house_number / street_name used for the parcel lookup so a
person can quickly search them in the City's address tools or Google Maps.

Reads  : data/edmonton_accessibility_residential_merged.csv
Writes : data/edmonton_accessibility_unmatched_addresses.csv
"""
import csv
import os

from geocode_residential_accessibility import parse_permit_address

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MERGED_CSV = os.path.join(DATA_DIR, "edmonton_accessibility_residential_merged.csv")
OUT_CSV = os.path.join(DATA_DIR, "edmonton_accessibility_unmatched_addresses.csv")

COLS = [
    "address", "parsed_house_number", "parsed_street_name", "neighbourhood",
    "ward", "keywords", "total_permits", "n_building_permits",
    "n_development_permits", "building_row_ids", "development_file_numbers",
    "sample_description",
]


def main():
    rows = list(csv.DictReader(open(MERGED_CSV, encoding="utf-8")))
    out = []
    for r in rows:
        if r.get("coord_source") == "none":
            hn, st = parse_permit_address(r.get("address", ""))
            rec = {c: r.get(c, "") for c in COLS}
            rec["parsed_house_number"] = hn or ""
            rec["parsed_street_name"] = st or ""
            out.append(rec)

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)

    print("Exported %d unmatched addresses -> %s" % (len(out), OUT_CSV))


if __name__ == "__main__":
    main()
