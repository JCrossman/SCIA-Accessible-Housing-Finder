#!/usr/bin/env python3
"""Export the residential accessibility addresses that could not be geocoded
(coord_source == 'none') for manual coordinate cleanup.

Includes the parsed house_number / street_name used for the parcel lookup so a
person can quickly search them in the City's address tools or Google Maps.

Usage : python scripts/export_unmatched_addresses.py <city>
Reads  : data/<city>/accessibility_residential_merged.csv
Writes : data/<city>/unmatched_addresses.csv
"""
import csv
import os
import sys

from cities import get_city
from geocode_residential_accessibility import parse_permit_address

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

COLS = [
    "address", "parsed_house_number", "parsed_street_name", "neighbourhood",
    "ward", "keywords", "total_permits", "n_building_permits",
    "n_development_permits", "building_row_ids", "development_file_numbers",
    "sample_description",
]


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "edmonton"
    cfg = get_city(city)
    data_city = os.path.join(DATA_DIR, city)
    merged_csv = os.path.join(data_city, "accessibility_residential_merged.csv")
    out_csv = os.path.join(data_city, "unmatched_addresses.csv")

    rows = list(csv.DictReader(open(merged_csv, encoding="utf-8")))
    out = []
    for r in rows:
        if r.get("coord_source") == "none":
            hn, st = parse_permit_address(r.get("address", "")) if cfg["geocode"]["needed"] else ("", "")
            rec = {c: r.get(c, "") for c in COLS}
            rec["parsed_house_number"] = hn or ""
            rec["parsed_street_name"] = st or ""
            out.append(rec)

    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(out)

    print("Exported %d unmatched %s addresses -> %s"
          % (len(out), cfg["display_name"], out_csv))


if __name__ == "__main__":
    main()
