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

Rules live in each city's building["status"] (cities.py). Runs in place AFTER
geocoding so it preserves the resolved coordinates (no re-geocoding). It
re-derives an address's permit counts / keywords / date range from its SURVIVING
permits, so a kept address never reports a dropped (abandoned) permit -- this
intentionally re-groups the raw permits by address (the same grouping
merge_residential_accessibility.py does at merge time); doing it here, post-
geocode, is what lets us drop abandoned permits without re-running geocoding.

Usage : python scripts/classify_completion.py <city> [residential|commercial]
Reads : data/<city>/{building,development}_permits_accessibility_<cut>.csv
Writes (in place): data/<city>/accessibility_<cut>_merged.csv  (+ `completion`)
"""
import csv
import os
import sys
from collections import Counter, defaultdict

from cities import get_city
from keywords import classify_keywords
from merge_residential_accessibility import normalize_address

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def permit_class(row, rule, completed_up, abandoned_up):
    """completed | abandoned | issued | unknown for one raw permit row.
    completed_up/abandoned_up are the rule's value sets, pre-upper-cased once."""
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
    if val in abandoned_up:
        return "abandoned"
    if val in completed_up:
        return "completed"
    return "issued"


def rollup(classes):
    """Most-complete surviving label for an address (abandoned permits removed)."""
    surviving = [c for c in classes if c != "abandoned"]
    if classes and not surviving:
        return None  # every permit abandoned -> drop the address
    if "completed" in surviving:
        return "completed"
    if "issued" in surviving:
        return "issued"
    return "unknown"


def _load(path):
    return list(csv.DictReader(open(path, encoding="utf-8"))) if os.path.exists(path) else []


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "edmonton"
    cut = sys.argv[2] if len(sys.argv) > 2 else "residential"
    cfg = get_city(city)
    cdir = os.path.join(DATA_DIR, city)
    merged_csv = os.path.join(cdir, "accessibility_%s_merged.csv" % cut)
    if not os.path.exists(merged_csv):
        print("No merged file for %s %s; skipping." % (city, cut))
        return
    with open(merged_csv, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        merged_fields = list(reader.fieldnames or [])
        merged_rows = list(reader)

    # Per-permit completion class + the bits needed to re-aggregate surviving
    # permits, grouped by normalized address. Development permits carry no status
    # (no city publishes one) -> always "unknown".
    b_cfg = cfg["building"]
    d_cfg = cfg.get("development")
    b_rule = b_cfg.get("status")
    b_comp = {c.upper() for c in (b_rule.get("completed") if b_rule else set()) or set()}
    b_aband = {c.upper() for c in (b_rule.get("abandoned") if b_rule else set()) or set()}
    b_text, b_addr, b_date = b_cfg["text_fields"], b_cfg["address_field"], b_cfg["date_field"]

    groups = defaultdict(list)

    def add(row, which, text_fields, addr_f, date_f, cls):
        k = normalize_address(row.get(addr_f, ""))
        if not k:
            return
        groups[k].append({
            "which": which, "cls": cls,
            "kws": classify_keywords(row, text_fields),
            "date": row.get(date_f, ""),
            "desc": (row.get(text_fields[0]) or "").strip(),
        })

    for r in _load(os.path.join(cdir, "building_permits_accessibility_%s.csv" % cut)):
        add(r, "building", b_text, b_addr, b_date, permit_class(r, b_rule, b_comp, b_aband))
    if d_cfg:
        d_text, d_addr, d_date = d_cfg["text_fields"], d_cfg["address_field"], d_cfg["date_field"]
        for r in _load(os.path.join(cdir, "development_permits_accessibility_%s.csv" % cut)):
            add(r, "development", d_text, d_addr, d_date, "unknown")

    out, dropped, tally = [], 0, Counter()
    for row in merged_rows:
        items = groups.get(normalize_address(row.get("address", "")), [])
        comp = rollup([it["cls"] for it in items])
        if comp is None:
            dropped += 1
            continue
        surviving = [it for it in items if it["cls"] != "abandoned"]
        if surviving:   # re-derive aggregates from surviving permits only
            n_b = sum(1 for it in surviving if it["which"] == "building")
            n_d = sum(1 for it in surviving if it["which"] == "development")
            kws = set().union(*[it["kws"] for it in surviving])
            dates = sorted(it["date"] for it in surviving if it["date"])
            row["n_building_permits"] = str(n_b)
            row["n_development_permits"] = str(n_d)
            row["total_permits"] = str(n_b + n_d)
            row["keywords"] = "; ".join(sorted(kws))
            row["sources"] = "+".join(sorted({it["which"] for it in surviving}))
            row["earliest_permit_date"] = dates[0] if dates else ""
            row["latest_permit_date"] = dates[-1] if dates else ""
            row["sample_description"] = next(
                (it["desc"] for it in surviving if it["desc"]), row.get("sample_description", ""))
        row["completion"] = comp
        tally[comp] += 1
        out.append(row)

    fieldnames = list(merged_fields)
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
