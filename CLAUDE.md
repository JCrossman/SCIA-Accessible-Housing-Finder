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

- **No private secrets in the repo.** No passwords, tokens, or service-account
  keys in code, logs, tool output, or history.
- **The one intentional exception is the public browser key** in
  `data/config.js`. A Google *Maps JavaScript* key is public by design (every
  visitor's browser sees it), so it is committed on purpose — protected by an
  HTTP-referrer restriction to the Pages domain plus daily quota caps, **not**
  by secrecy. Any embedded key MUST be referrer-restricted + quota-capped, and
  that decision is made explicitly by a human.
- For local/private use, the map also accepts a key via the in-map button,
  stored only in `localStorage`. `streetview-key.local.js` and `.env` are
  gitignored. Keep it that way.

## The Open State — governing constitution

This project is built under **The Open State** and conforms, *in the ways that
apply to a read-only discovery tool*, to its Constitution
(https://github.com/JCrossman/the-open-state/blob/main/CONSTITUTION.md, tag
`constitution-v1.1`).

**Shape matters.** This is a public, read-only open-data map: no citizen login,
no session, no credentials, no consequential actions. The Civic Access
Protocol's *mechanical* articles — 1 (credentials), 2 (the human decides), 9
(token passthrough), 10 (act in-session) — therefore do **not** apply, and
`@open-state/kit` is intentionally **not** used. Do not add it as cargo-cult
conformance. If this project ever gains a transactional/session layer (e.g.
helping a citizen act on a listing or application), that layer MUST adopt the
kit's `vault` / `confirm-gate` / `capture` and meet Articles 1, 2, 9, and 10 in
full.

**The non-negotiables that DO apply:**

- **Accessibility is the purpose (Art. 3).** Screen-reader-clean, plain-language
  output; accessibility attributes first-class and **filterable**. Never
  regress this.
- **Honesty (Art. 7).** Distinguish verified from assumed; keep the "draft /
  permit ≠ availability / false-positive" caveats; fail visibly. Polite request
  rates against the City and Google APIs; nothing that degrades them.
- **Minimization & no exploitation (Arts. 5, 6).** Collect no citizen data; no
  monetization; only City permit data in the repo (no PII / synthetic only).
- **Openness (Art. 8).** Keep it public, MIT, forkable.

**Known tension to keep honest (Art. 7):** the map depends on Google
Maps/Street View and a CDN — third parties, against the movement's no-tracker
preference — a deliberate trade-off for Street View's coverage. Keep this
disclosed in the README; the key stays domain-restricted + quota-capped.

If a requested change conflicts with these, say so and stop rather than
complying — cite the article.

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
