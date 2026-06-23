#!/usr/bin/env python3
"""Label each mapped address with a permit *completion* status, and drop
addresses whose permits were all abandoned (cancelled/void/withdrawn/...).

A permit map shows that accessibility work was *permitted*, not that it was
finished. Where a city publishes a permit status we can do better (honesty,
Constitution Art. 7): each address gets the most-complete label among its
surviving permits:
  - completed : a city sign-off (Calgary "Completed", Austin "Final",
                Mississauga "all inspections signed off", Toronto "Closed",
                Markham "Occupancy Granted", or an Edmonton occupancy date)
  - issued    : a permit was issued/active but completion is not confirmed
  - unknown   : the city (or that permit) publishes no status at all
                (Vancouver, Ottawa, Montreal, most of Edmonton)
"unknown" is never silently treated as completed or as not-completed.

Rules live in cities.py (BUILDING_STATUS). Runs in place AFTER geocoding, so it
preserves the resolved coordinates (no re-geocoding).

Usage : python scripts/classify_completion.py <city> [residential|commercial]
Reads : data/<city>/{building,development}_permits_accessibility_<cut>.csv
Writes (in place): data/<city>/accessibility_<cut>_merged.csv  (+ `completion`)
"""
import csv
import os
import sys
from collections import defaultdict

from cities import get_city, BUILDING_STATUS
from merge_residential_accessibility import normalize_address

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def permit_class(row, rule):
    """completed | abandoned | issued | unknown for one raw permit row."""
    if not rule:
        return "unknown"
    cif = rule.get("completed_if_present")
    if cif and (row.get(cif) or "").strip():
        return "completed"
    field = rule.get("field")
    if not field:
        return "unknown"
    val = (row.get(field) or "").strip().upper()
    if not val:
        return "unknown"
    if val in {a.upper() for a in rule.get("abandoned", set())}:
        return "abandoned"
    if val in {c.upper() for c in rule.get("completed", set())}:
        return "completed"
    return "issued"


def rollup(classes):
    """Most-complete surviving label for an address (drop abandoned permits)."""
    surviving = [c for c in classes if c != "abandoned"]
    if classes and not surviving:
        return None  # every permit abandoned -> drop the address
    if "completed" in surviving:
        return "completed"
    if "issued" in surviving:
        return "issued"
    return "unknown"


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "edmonton"
    cut = sys.argv[2] if len(sys.argv) > 2 else "residential"
    cfg = get_city(city)
    cdir = os.path.join(DATA_DIR, city)
    merged_csv = os.path.join(cdir, "accessibility_%s_merged.csv" % cut)
    if not os.path.exists(merged_csv):
        print("No merged file for %s %s; skipping." % (city, cut))
        return
    rows = list(csv.DictReader(open(merged_csv, encoding="utf-8")))

    # Group raw permits by normalized address -> list of completion classes.
    classes = defaultdict(list)
    b_rule = BUILDING_STATUS.get(city)
    b_addr = cfg["building"]["address_field"]
    b_csv = os.path.join(cdir, "building_permits_accessibility_%s.csv" % cut)
    if os.path.exists(b_csv):
        for r in csv.DictReader(open(b_csv, encoding="utf-8")):
            k = normalize_address(r.get(b_addr, ""))
            if k:
                classes[k].append(permit_class(r, b_rule))
    # No city publishes a development-permit status -> those permits are "unknown".
    d_cfg = cfg.get("development")
    if d_cfg:
        d_csv = os.path.join(cdir, "development_permits_accessibility_%s.csv" % cut)
        d_addr = d_cfg["address_field"]
        if os.path.exists(d_csv):
            for r in csv.DictReader(open(d_csv, encoding="utf-8")):
                k = normalize_address(r.get(d_addr, ""))
                if k:
                    classes[k].append("unknown")

    out, dropped, tally = [], 0, {"completed": 0, "issued": 0, "unknown": 0}
    for row in rows:
        comp = rollup(classes.get(normalize_address(row.get("address", "")), []))
        if comp is None:
            dropped += 1
            continue
        row["completion"] = comp
        tally[comp] += 1
        out.append(row)

    fieldnames = list(rows[0].keys()) if rows else ["completion"]
    if "completion" not in fieldnames:
        fieldnames.append("completion")
    with open(merged_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out)

    print("%-11s %-11s kept %4d  [completed %d, issued %d, unknown %d]  dropped %d (all-abandoned)"
          % (city, cut, len(out), tally["completed"], tally["issued"],
             tally["unknown"], dropped))


if __name__ == "__main__":
    main()
