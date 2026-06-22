# SCIA Accessible Housing Finder

![Accessible Housing Finder — mapping Edmonton and Calgary homes with accessibility features. Built for Spinal Cord Injury Alberta using City of Edmonton and City of Calgary open data.](docs/social-preview.png)

A tool that builds a database and interactive map of Canadian properties
(**Edmonton, Calgary, and Vancouver**) with **accessibility-related building
work** — ramps, lifts/elevators, wheelchair access, barrier-free features, and
similar — to support [Spinal Cord Injury Alberta](https://sci-ab.ca/)'s
accessible housing work.

All data comes from each city's **free public Open Data** — the
[City of Edmonton](https://data.edmonton.ca/) and
[City of Calgary](https://data.calgary.ca/) (Socrata) and
[City of Vancouver](https://opendata.vancouver.ca/) (OpenDataSoft) portals. No
API key is required to gather the data; each city is a config entry, and the
pipeline has a small fetch adapter per open-data platform.

## The problem

For people with spinal cord injuries and other mobility disabilities, finding
housing that is actually wheelchair-accessible or barrier-free in Edmonton is
hard. There is **no central list** of which homes have ramps, lifts, or
barrier-free bathrooms, so individuals and the organizations supporting them
often have to piece it together one listing at a time, with no way to search
for the features that matter.

Meanwhile, the City of Edmonton's public building and development permits **do**
record this work — when a ramp is added, an elevator installed, or a bathroom
made barrier-free — but that information sits buried inside large permit datasets
that were never designed to answer the question "where is the accessible
housing?"

## How this helps

This project mines those public permit records for accessibility-related work,
narrows the results to homes, pins them on a searchable, filterable map, and
shows a Street View photo of each one. It turns scattered permit data into a
**browsable starting point** that Spinal Cord Injury Alberta and the people it
serves can use to find and track accessible housing across the city.

## In plain language

We built a free, online map that shows Alberta homes (in **Edmonton and
Calgary**) that have had accessibility-related work done — things like
wheelchair ramps, lifts, and barrier-free bathrooms. There's no existing list of
accessible homes, but the cities' public building records quietly capture this
work — so we pulled it out and put it on a map. You can open it in any web
browser, switch between cities, click any dot to see the address and what work
was done, and even view a street-level photo of the property. It's a starting
point to help us find and track accessible housing across the province. One thing to keep in mind: it's an
early draft pulled automatically from city data, so a few entries may not be
true accessibility features (for example, a "ramp" that's actually a
parking-garage ramp) — we'll refine the list over time.

## 🗺️ Live map

**[View the interactive map →](https://jcrossman.github.io/SCIA-Accessible-Housing-Finder/)**

No setup needed — just open the link. Click any dot for the address, the
accessibility work done, permit history, and a Street View photo.

**Two pin types:**

- <img src="docs/pin-wheelchair.png" alt="Blue pin with a white wheelchair symbol" height="22" valign="middle"> **Blue wheelchair pin** — the permit's keywords or description
  explicitly mention *wheelchair* (confirmed wheelchair access).
- <img src="docs/pin-unsure.png" alt="Grey pin with a white question mark" height="22" valign="middle"> **Grey "?" pin** — an accessibility permit (ramp, lift,
  barrier-free, etc.) where wheelchair access is *not* confirmed — worth a
  closer look.

**Controls:**

- **City** toggle — *All cities* by default; narrow to Edmonton, Calgary, or
  Vancouver.
- **Homes / Businesses / Both** toggle — homes by default; switch to commercial
  & public places (offices, shops, restaurants, rec centres, clinics, schools,
  etc.) or show both.
- **Show only confirmed wheelchair access** — a one-tap button that narrows the
  map (and the Feature filter) to just the blue-pin (confirmed-wheelchair) ones.
- **Map / Satellite** toggle (top-left) for satellite imagery.
- **Filter** panel to narrow by feature (ramps, lifts, wheelchair access,
  step-free entries, or general barrier-free work) and by **permit year**.
- **View as list** — a keyboard- and screen-reader-friendly text version of the
  same places.

> **Note on businesses:** commercial accessibility is largely *required* by the
> Alberta Building Code, so a business permit is a weaker signal than a home
> retrofit — and big chunks (warehouses, parkades) are freight lifts / loading
> ramps, not human access. Treat business pins as "worth checking," and see the
> honest notes below.

## What it produces

- **An interactive map** (`data/accessibility_map.html`) — open in any
  browser. Each pin is a property; click it for the address, neighbourhood,
  what accessibility work was done, permit counts, dates, and a Street View
  photo of the front of the building.
- **Spreadsheets (CSV)** you can open in Excel — see [Data files](#data-files).

## Results at a glance

| City | Homes (mapped) | Businesses / public places (mapped) |
| --- | --- | --- |
| **Edmonton** | 355 (354 mapped, 100%) | 1,044 (1,032 mapped, 99%) |
| **Calgary** | 196 (196 mapped, 100%) | 650 (650 mapped, 100%) |
| **Vancouver** | 522 (520 mapped, 100%) | 741 (738 mapped, 100%) |
| **Total on the map** | **1,073 homes** | **2,435 businesses** — **~3,490 places** total |

These accessibility-keyword permits are a tiny slice of each city's hundreds of
thousands of building + development permits — and a *floor*, since many real
accessibility upgrades don't use these keywords.

**Data coverage:** Edmonton building permits run from **2009** and development
permits from **2015**; Calgary's and Vancouver's go back further still — each
through the present, the full span the cities currently publish (no date filter
is applied). Coverage is not uniform across those years: older permits less often
use modern terms like "barrier-free," so recent years are over-represented.
Calgary and Vancouver permit descriptions are also terser than Edmonton's, so
they yield fewer matches.

> **Caveat — keyword false positives.** A word like "ramp" sometimes refers to a
> *parking-garage* ramp rather than a wheelchair ramp. Each record keeps its full
> permit description so these can be reviewed and filtered.

## Data sources (city Open Data portals)

| City | Dataset | ID | Used for |
| --- | --- | --- | --- |
| Edmonton | General Building Permits | `24uj-dj8v` | Construction/renovation records |
| Edmonton | Development Permits | `2ccn-pwtu` | Land-use/development approvals |
| Edmonton | Parcel Addresses | `ut27-nrpn` | Address → latitude/longitude (geocoding) |
| Calgary | Building Permits | `c2es-76ed` | Construction/renovation records (coords included) |
| Calgary | Development Permits | `6933-unw5` | Land-use/development approvals (coords included) |
| Vancouver | Issued Building Permits | `issued-building-permits` | Construction/renovation records (coords included) |

Per-city sources, field names, platform, and classification rules live in
[`scripts/cities.py`](scripts/cities.py). Calgary and Vancouver permits already
carry coordinates, so they need no separate geocoding step. Vancouver is on
**OpenDataSoft** (the others are Socrata); the query step has a fetch adapter per
platform. Vancouver publishes building permits only (no separate development set).

## Data files

Outputs are namespaced per city under `data/<city>/` (e.g. `data/edmonton/`,
`data/calgary/`):

| File (per city) | Contents |
| --- | --- |
| `building_permits_accessibility.csv` | All building-permit matches |
| `development_permits_accessibility.csv` | All development-permit matches |
| `..._residential.csv` (building + development) | Residential-only cuts |
| `..._commercial.csv` (building + development) | Non-residential (business / public place) cuts |
| `accessibility_residential_merged.csv` | **Homes master list** — deduped, located |
| `accessibility_commercial_merged.csv` | **Businesses master list** — deduped, located |
| `unmatched_addresses.csv` | Addresses that need a manual location lookup |

The single combined map for all cities is `data/accessibility_map.html` (homes +
businesses, with a city filter).

## How to re-run / refresh the data

Requires Python 3.9+ and the `requests` library.

Each pipeline script takes a `<city>` argument (`edmonton`, `calgary`, or
`vancouver`); the final map step reads every city and writes one combined map.
Run the per-city steps once for each city.

```bash
pip install -r requirements.txt

# Per city (repeat with calgary / vancouver in place of edmonton):
# 1. Query Open Data and write the raw + residential + commercial CSVs
python scripts/edmonton_accessibility_query.py edmonton

# 2. Merge building + development permits into one address list per cut
python scripts/merge_residential_accessibility.py edmonton residential  # homes
python scripts/merge_residential_accessibility.py edmonton commercial   # businesses

# 3. Fill in coordinates (Edmonton: geocode from Parcel Addresses;
#    Calgary/Vancouver: no-op, permits already carry coordinates)
python scripts/geocode_residential_accessibility.py edmonton residential
python scripts/geocode_residential_accessibility.py edmonton commercial

# 4. (optional) Export the addresses that couldn't be geocoded
python scripts/export_unmatched_addresses.py edmonton

# Once per refresh, after all cities are processed:
# 5. Build the single combined map (reads every city's merged lists)
python scripts/generate_accessibility_map.py
```

All scripts read/write the `data/` folder (per-city subfolders).

## Using the map

1. Open `data/accessibility_map.html` in a browser.
2. Click **Enter Google key to load map**, paste your Google Maps API key once
   (it is stored in your browser only — never committed or shared).
3. Click any dot to see the property details and a Street View photo.

**Accessible alternative:** because an interactive pin map is hard to use with a
keyboard or screen reader, the panel includes a **"View as list"** button that
opens a keyboard- and screen-reader-friendly text list of the same homes
(address, features, years, and a link to each in Google Maps / Street View). The
list reflects whatever filters are active.

The map uses the **Google Maps JavaScript API**, so the key needs both of these
enabled in the [Google Cloud Console](https://console.cloud.google.com/):

- **Maps JavaScript API** (to display the map)
- **Street View Static API** (for the in-popup photos)

Google's recurring free monthly credit covers far more usage than this project
generates, so in practice it stays within the free tier.

## Keywords searched

`ramp`, `wheelchair`, `accessible` / `accessibility`, `barrier-free`,
`grab bar`, `lift` / `elevator`, `mobility`, `handicap`, `universal design`,
`ada compliant`, `handrail`, `step-free entry` (no-step, level entry,
curbless, zero/no threshold), `accessible bathroom` (roll-in shower,
walk-in tub), `wider doorway` (door widening), `adaptable` / `visitable`,
`aging in place`, and `automatic door` / `power door operator`.

The later terms were added to catch older permits (2009–2015) that described the
same work in plainer language rather than "barrier-free"/"accessible".

## Notes

- Results reflect what the City currently publishes (a rolling window of recent
  years), so re-running refreshes the numbers.
- The published map uses a **public browser key** in `data/config.js`, which is
  restricted to this site's domain and capped by daily quotas (a Maps
  JavaScript key is public by design — see the alignment notes below). For
  local or private use you can instead enter a key via the in-map button, which
  is stored only in your own browser.

## Credits

- Map marker wheelchair glyph: the "wheelchair" icon from
  [Font Awesome Free](https://fontawesome.com/) 6, licensed under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## The Open State alignment

This project is built under **[The Open State](https://github.com/JCrossman/the-open-state)**
and is governed by its
**[Constitution](https://github.com/JCrossman/the-open-state/blob/main/CONSTITUTION.md)**
at tag **`constitution-v1.1`**.

**What it is — and what it is not.** The Open State's Civic Access Protocol is
designed for *transactional assistive technology*: tools that act inside a
citizen's own authenticated session to complete an action (a booking, a payment,
a submission). This project is a different shape — a **read-only discovery
tool**, a public map built from open data. It has no citizen login, no session,
no credentials, and takes no consequential actions. It therefore makes **no
claim of full Civic Access Protocol compliance**, and it deliberately does
**not** use `@open-state/kit` (the `vault` / `confirm-gate` / `capture`
primitives) — those exist for the session and credential articles that do not
apply here.

**The articles it lives by:**

- **Art. 3 — Accessibility is the purpose.** Accessibility is the whole point,
  not a feature. Accessibility attributes are surfaced as first-class and are
  **filterable**, so a citizen can restrict the map to the features they need.
- **Arts. 5 / 6 — Minimization & no exploitation.** It collects no citizen
  data, is not monetized, and sells nothing.
- **Art. 7 — Honesty about limits.** It separates what is verified from what is
  assumed (see the caveats below and on the map itself).
- **Art. 8 — Openness.** Public, MIT-licensed, and forkable, so the method can
  be reused for the next service.

### Honest notes and known limits

*Recorded rather than hidden (Constitution Art. 7):*

- **Permits are not availability, and not proof of current accessibility.** A
  permit shows accessibility work was approved or done at some point; it does
  not mean the home is still accessible, or is for sale or rent today. Treat
  every pin as "worth checking," not as fact.
- **Third-party dependency, against the movement's usual grain.** The map uses
  **Google Maps and Street View** (and loads a clustering library from a CDN).
  Google is a third party that meters and can profile usage, which runs against
  The Open State's preference for no third-party trackers or CDNs (Art. 6). This
  is a **deliberate trade-off**: Street View's coverage is uniquely valuable for
  judging a property from the street, and no open alternative matches it today.
  The browser key is public by design but is **restricted to this site's domain
  and capped by daily quotas**. A privacy-respecting, self-hosted map stack is a
  known future option (it would cost the Street View feature).
- **Keyword false positives.** Substring matches like "ramp" / "lift" can catch
  parking-garage ramps or freight lifts. Full permit text is kept in every
  record so a human can judge.
- **Location coverage is ~99–100%.** Calgary and Vancouver permits carry
  coordinates; almost all Edmonton addresses geocode. Any remainder are listed in
  each city's `data/<city>/unmatched_addresses.csv` for manual lookup.
- **Calgary and Vancouver are newer, lighter layers.** Their permit descriptions
  are terser than Edmonton's, so they surface fewer matches, and the same "worth
  checking, not proof" framing applies. (Vancouver's open-data text search also
  over-matches, so every record is re-checked against the keyword list before it
  is kept.)

### The genuine Civic Access Protocol piece is the next layer

The map is the *discovery* front door. The point where The Open State's protocol
truly applies is the planned **availability / application-assist** step (see
[issue #2](https://github.com/JCrossman/SCIA-Accessible-Housing-Finder/issues/2)):
helping a citizen *act* — check a listing or navigate a housing application —
inside their own session, at their direction. That layer should adopt
`@open-state/kit` and meet Articles 1, 2, 9, and 10 in full.
