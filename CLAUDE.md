# CLAUDE.md

Guidance for Claude Code (and other AI assistants) working in this repository.

## What this project is

A data pipeline + interactive map that identifies Canadian properties
(**Edmonton, Calgary, Vancouver, Toronto**) with **accessibility-related
building work** (ramps, lifts/elevators, wheelchair access, barrier-free
features, etc.), built for Spinal Cord Injury Alberta's accessible housing
efforts. Source data is from the cities' **Open Data portals** across three
platforms: Socrata (`data.edmonton.ca`, `data.calgary.ca`), OpenDataSoft
(`opendata.vancouver.ca`), and CKAN (Toronto, `ckan0.cf.opendata.inter.prod-toronto.ca`).
No API key is needed to gather data.

## Repository layout

```
scripts/   Python data pipeline + the map generator and the JS a11y audit
scripts/cities.py   Per-city config (datasets, field maps, residential rules)
data/<city>/   Generated CSVs per city (edmonton/, calgary/, vancouver/)
data/      The combined map (accessibility_map.html) + config.js (public key)
docs/      Banner image + the accessibility CI workflow (copy to .github to use)
README.md  User-facing overview and run instructions
package.json / scripts/audit.mjs   Node tooling for the offline axe-core audit
```

### Multi-city: config-driven + per-platform adapters

`scripts/cities.py` holds a `CITIES` dict; each entry declares a `platform`
(`socrata` | `opendatasoft` | `ckan`), maps this pipeline's canonical field names
to the city's real columns, plus its residential rule and whether/how permits
carry coordinates. Everything else (keyword list, dedup, map UI) is shared.

- **Fetch is dispatched by platform** in `fetch_permits()` (query script):
  Socrata uses SoQL `build_where` + `fetch_all`; OpenDataSoft uses `ods_fetch`
  (ODSQL `like` + records API + `geo_point_2d`→lat/lon); CKAN uses `ckan_fetch`
  (per-keyword `datastore_search?q=`, union by `_id`, synthesize one `address`
  from STREET_NUM/NAME/TYPE/DIRECTION). **Adding a city on an existing platform
  is just a `CITIES` entry**; a new platform (e.g. ArcGIS for Red
  Deer/Lethbridge/Ottawa) needs one new adapter.
- **Geocoding is also platform-aware** (`geocode_residential_accessibility.py`):
  Edmonton matches Parcel Addresses (Socrata); Toronto matches Address Points
  (CKAN: filter by exact street number, match `LINEAR_NAME` in Python, read
  `geometry` which the datastore returns as a JSON *string*). ~85% for Toronto.
- **Server filter is a coarse prefilter; `classify_keywords` is the source of
  truth.** After fetching, every row is re-checked with `classify_keywords` and
  dropped if it has no keyword. No-op for Socrata (SoQL `like` is exact
  substring), but it removes OpenDataSoft's analyzer over-matches (~30% for
  Vancouver). Keep this — don't trust the server filter alone.
- **Building-only cities**: set `development: None` (Vancouver). The query writes
  empty development CSVs so merge stays uniform; classifiers/summaries guard on it.

### The pipeline (run per city, in this order)

Each script takes a `<city>` slug (`edmonton` | `calgary` | `vancouver` |
`toronto`). Outputs go to `data/<city>/`.

1. `edmonton_accessibility_query.py <city>` — query Open Data for accessibility
   keywords; write raw + residential + commercial CSVs (each permit classified
   residential vs non-residential via the city's rule).
2. `merge_residential_accessibility.py <city> [residential|commercial]` —
   dedupe building + development permits into one address-level master list for
   the chosen cut (default `residential`). Run once per cut. Carries coordinates
   from whichever permits supply them; adds a `city` column.
3. `geocode_residential_accessibility.py <city> [cut]` — Edmonton: geocode
   against Parcel Addresses (`ut27-nrpn`, Socrata). Toronto: geocode against
   Address Points (CKAN). Calgary/Vancouver: no-op (permits already carry
   coords), just records `coord_source`. Dispatched by `cfg["geocode"]["needed"]`
   and `["platform"]`.
4. `export_unmatched_addresses.py <city>` — export addresses still missing
   coordinates for manual review.
5. `generate_accessibility_map.py` — build the **single combined** map
   (`data/accessibility_map.html`) reading every city's merged lists. Run once
   after all cities are processed.

The merge/geocode scripts are also parameterized by "cut": the same code serves
homes (`residential`) and businesses (`commercial` = everything non-residential:
offices, shops, restaurants, rec centres, clinics, schools, warehouses,
parkades, etc.).

## The map (`generate_accessibility_map.py`)

- **Base**: Google Maps JS API + marker clustering. The API key is read at
  runtime from `data/config.js` (a public, referrer-restricted browser key) or
  an in-map button; never a private secret.
- **Layers + cities on one map**: `build_points(csv, ptype, city, cfg)` tags
  each point with `type` (`home`/`business`) and `city` (slug). A **Homes /
  Businesses / Both** toggle (`typeFilter`, default `home`) and a **City** toggle
  (`cityFilter`, default `all`, only rendered when >1 city is present) both feed
  the single `applyFilter()`. Count/list noun + denominator respect both.
- **Two pins**: blue wheelchair pin when the keywords/description contain
  "wheelchair" (`p.wc`), else a grey "?" pin (wheelchair not confirmed). Pins
  differ by glyph + colour (not colour alone). Pin type is independent of the
  home/business layer.
- **Filters** (one `applyFilter()` pass drives map + list + count): the
  home/business type toggle, a feature filter (keyword categories), a permit-year
  range, and a "Show only confirmed wheelchair access" button that sets the
  feature filter to wheelchair-only and restores on toggle-off.
- **Accessible list view** ("View as list") is the keyboard/screen-reader path —
  the visual marker map is not keyboard-operable, so keep the list in sync.
- **Marker glyph**: Font Awesome 6 "wheelchair" (CC BY 4.0) — keep the
  attribution in the README Credits.

## Conventions

- **Python**: standard library + `requests` only. No pandas / heavy deps. Keep
  scripts runnable with `python scripts/<name>.py` from the repo root.
- **Data source IDs** (all in `scripts/cities.py`): Edmonton building
  `24uj-dj8v`, development `2ccn-pwtu`, Parcel Addresses `ut27-nrpn`
  (`data.edmonton.ca`); Calgary building `c2es-76ed`, development `6933-unw5`
  (`data.calgary.ca`); Vancouver `issued-building-permits`
  (`opendata.vancouver.ca`, OpenDataSoft); Toronto building permits resources
  `6d0229af-…` (Active) + `a96c0ba4-…` (Cleared since 2017) + `c647bdae-…`
  (Cleared 2000–2016, download-only flat CSV) + Address Points `0b3756af-…`
  (CKAN). `building.dataset` may be a *list* mixing datastore ids (string,
  `q`-fetched) and `{"id":…, "download": True}` flat-CSV resources (streamed +
  filtered locally); ckan_fetch unions them, deduped by PERMIT_NUM. Never
  hardcode dataset URLs/fields in scripts — add them to `cities.py`.
- **Per-city classification**: Edmonton uses building_type numeric codes +
  R-prefix zoning; Calgary uses `permitclassmapped == "Residential"` (building)
  and `landusedistrict` R-/M- prefixes (development); Vancouver uses
  `propertyuse in {"Dwelling Uses", ...}` (the generic field-match rule shared
  with Calgary's building rule); Toronto uses `RESIDENTIAL` sq-m > 0 with a
  CURRENT_USE/PROPOSED_USE dwelling-term fallback. All fall back to a shared
  dwelling-term scan.
- **Platform gotchas**: Edmonton `house_number` in Parcel Addresses is a
  *numeric* column — a letter-suffixed value (e.g. `7606A`) throws a
  type-mismatch 400; parse to the numeric base. Calgary addresses use `#unit`
  prefixes and quadrants (`SW`) and no ` - ` separator (handled in
  `normalize_address`). OpenDataSoft (Vancouver) returns multi-value fields as
  *lists* (e.g. `propertyuse`) — the field-match classifier handles list-or-string;
  its `like` text search over-matches, hence the `classify_keywords` post-filter.
  CKAN (Toronto): full-text `q` works on the permits resource but **not** on the
  Address Points resource (filter by exact number + match name in Python), and
  `geometry` comes back as a JSON *string*; addresses are split across four
  columns. Use retry/backoff; the APIs throttle.
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
- **Coverage**: Edmonton homes 354/355, businesses 1,032/1,044 (geocoded);
  Calgary 196/196 + 650/650 and Vancouver 520/522 + 738/741 (coords from
  permits); Toronto 667/801 + 2,423/2,843 (~84%, geocoded against Address
  Points; queries Active + Cleared-since-2017 datastores AND the pre-2017 Cleared
  flat CSV via download). Any unmatched rows are in
  `data/<city>/unmatched_addresses.csv`.
- **Businesses are a weaker signal than homes**: commercial accessibility is
  largely *required* by building codes, and some matches are freight lifts /
  loading ramps (warehouses, parkades), not human access. Keep the "worth
  checking" framing and the README's "Note on businesses" caveat.
- **Calgary/Vancouver/Toronto descriptions are terser** than Edmonton's, so they
  surface fewer matches; same "worth checking, not proof" framing.
- Results reflect the cities' currently published (rolling) data, so counts
  change when the pipeline is re-run. Update the README "Results at a glance"
  table when they do.

## When making changes

- Regenerate affected `data/` outputs and commit them alongside code changes so
  the repo stays self-consistent.
- After editing the map generator, syntax-check the emitted JavaScript (e.g.
  extract the inline `<script>` and run `node --check`) — nested quotes have
  broken it before.
- Run the accessibility audit after map changes: `npm run audit` (offline; stubs
  Google Maps). It must report no serious/critical violations.
- Keep the README's "Results at a glance" numbers, the "Keywords searched" list,
  and the "Live map" pin/filter descriptions in sync with the code.
