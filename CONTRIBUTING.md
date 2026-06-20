# Contributing

Thanks for helping improve the SCIA Accessible Housing Finder.

## Setup

Requires Python 3.9+.

```bash
pip install -r requirements.txt
```

## Running the pipeline

All scripts read and write the `data/` folder and are run from the repo root,
in order:

```bash
python scripts/edmonton_accessibility_query.py        # 1. query Edmonton Open Data
python scripts/merge_residential_accessibility.py     # 2. merge + dedupe residential
python scripts/geocode_residential_accessibility.py   # 3. add coordinates
python scripts/export_unmatched_addresses.py          # 4. (optional) export gaps
python scripts/generate_accessibility_map.py          # 5. build the map
```

When you change a script, re-run the affected steps and commit the regenerated
`data/` files in the same change so the repo stays self-consistent.

## Style

- Standard library + `requests` only — no heavy dependencies.
- Match the existing code: clear names, small functions, retry/backoff on every
  network call, and fail-soft batch handling.
- After editing `generate_accessibility_map.py`, validate the emitted
  JavaScript (extract the inline `<script>` block and run `node --check`).

## Accessibility audit

Accessibility is the purpose of this project (Constitution Art. 3), so changes to
the map are checked with [axe-core](https://github.com/dequelabs/axe-core). The
audit runs **offline** — it stubs Google Maps, so no API key or network is
needed:

```bash
npm install                                  # one time
npx playwright install --with-deps chromium  # one time
python3 scripts/generate_accessibility_map.py
npm run audit                                # fails on serious/critical issues
```

The same steps run in CI — see [`docs/accessibility-workflow.yml`](docs/accessibility-workflow.yml)
(copy it to `.github/workflows/accessibility.yml` to activate). Run the audit
locally before opening a PR that touches the map.

## Never commit secrets

Do not commit Google API keys or any credential. The map handles keys at
runtime (browser local storage). `streetview-key.local.js` and `.env` are
gitignored — keep them so. See [SECURITY.md](SECURITY.md).

## Data

Source data is from the [City of Edmonton Open Data Portal](https://data.edmonton.ca)
and remains under its terms of use. Be mindful of the keyword false-positive
caveat (see [CLAUDE.md](CLAUDE.md)) when curating results.
