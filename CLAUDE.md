# CLAUDE.md

Guidance for Claude Code (and other AI assistants) working in this repository.

## What this project is

A data pipeline + interactive map that identifies Edmonton properties with
**accessibility-related building work** (ramps, lifts/elevators, wheelchair
access, barrier-free features, etc.), built for Spinal Cord Injury Alberta's
accessible housing efforts. All source data is from the **City of Edmonton
Open Data Portal** (Socrata); no API key is needed to gather data.

## Repository layout

```
scripts/   Python data pipeline (stdlib + requests only; no pandas)
data/      Generated CSVs and the interactive map (committed outputs)
README.md  User-facing overview and run instructions
```

### The pipeline (run in this order)

1. `scripts/edmonton_accessibility_query.py` — query Edmonton Open Data for
   accessibility keywords; write raw + residential CSVs.
2. `scripts/merge_residential_accessibility.py` — dedupe residential building +
   development permits into one address-level master list.
3. `scripts/geocode_residential_accessibility.py` — fill in coordinates by
   matching the Parcel Addresses dataset (`ut27-nrpn`).
4. `scripts/export_unmatched_addresses.py` — export addresses still missing
   coordinates for manual review.
5. `scripts/generate_accessibility_map.py` — build the Google Maps HTML view.

## Conventions

- **Python**: standard library + `requests` only. No pandas / heavy deps. Keep
  scripts runnable with `python scripts/<name>.py` from the repo root.
- **Data source IDs**: Building `24uj-dj8v`, Development `2ccn-pwtu`,
  Parcel Addresses `ut27-nrpn` (all on `data.edmonton.ca`).
- **Socrata gotchas**: `house_number` in the Parcel Addresses dataset is a
  *numeric* column — querying a letter-suffixed value (e.g. `7606A`) throws a
  type-mismatch 400. Parse to the numeric base. Building permits have a typo'd
  `neighbourhood_numberr` field. Use retry/backoff; the API throttles.
- **Network calls**: always page results, retry with exponential backoff, and
  fail soft (skip a batch rather than crash the run).

## Security — never commit secrets

- **Do NOT commit any Google API key.** The map reads the key at runtime (a
  browser button storing it in `localStorage`); it is never baked into the
  committed HTML. If a key must be embedded for a public deployment, it must be
  HTTP-referrer-restricted and that decision is made explicitly by a human.
- `streetview-key.local.js` and `.env` are gitignored. Keep it that way.

## Known caveats (carry these forward, don't "fix" silently)

- **Keyword false positives**: substrings like "ramp" / "lift" can match
  parking-garage ramps or freight lifts, not accessibility features. Each
  record keeps its full description so a human can filter. Do not aggressively
  prune matches without surfacing the tradeoff.
- **Coverage**: ~93% of unique addresses are geocoded; the rest are in
  `data/edmonton_accessibility_unmatched_addresses.csv` for manual lookup.
- Results reflect the City's currently published (rolling) data, so counts
  change when the pipeline is re-run.

## When making changes

- Regenerate affected `data/` outputs and commit them alongside code changes so
  the repo stays self-consistent.
- After editing the map generator, syntax-check the emitted JavaScript (e.g.
  extract the inline `<script>` and run `node --check`) — nested quotes have
  broken it before.
- Keep the README's "Results at a glance" numbers in sync with reality.
