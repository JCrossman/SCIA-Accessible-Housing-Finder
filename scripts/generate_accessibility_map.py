#!/usr/bin/env python3
"""Generate a self-contained, browsable HTML map of the geocoded residential
accessibility permit locations for the SCI Alberta housing database.

- Google Maps JavaScript API base map with Google's marker clustering.
- Each popup shows address, neighbourhood, ward, matched keywords, permit
  counts, date range, and an embedded Street View photo of the property front.
- The Google API key is entered via an in-map button and stored in the
  browser (localStorage) only -- never baked into this committed file. The
  key needs the Maps JavaScript API and Street View Static API enabled.

Reads  : data/edmonton_accessibility_residential_merged.csv
Writes : data/edmonton_accessibility_map.html
"""
import csv
import html
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
MERGED_CSV = os.path.join(DATA_DIR, "edmonton_accessibility_residential_merged.csv")
COMMERCIAL_CSV = os.path.join(DATA_DIR, "edmonton_accessibility_commercial_merged.csv")
OUT_HTML = os.path.join(DATA_DIR, "edmonton_accessibility_map.html")

SOURCE_COLORS = {
    "development": "#1f78b4",     # blue
    "parcel_geocode": "#33a02c",  # green
}

# Map marker: a blue teardrop pin with the white International Symbol of Access
# (wheelchair). The wheelchair glyph is Font Awesome 6 Free "wheelchair"
# (CC BY 4.0). White-on-blue matches the official accessibility colourway.
_PIN_PATH = ("M14 1 C6.8 1 1 6.8 1 14 c0 9.8 13 25 13 25 s13 -15.2 13 -25 "
             "C27 6.8 21.2 1 14 1 z")
_FA_WHEELCHAIR = (
    "M192 96a48 48 0 1 0 0-96 48 48 0 1 0 0 96zM120.5 247.2c12.4-4.7 18.7-18.5 "
    "14-30.9s-18.5-18.7-30.9-14C43.1 225.1 0 283.5 0 352c0 88.4 71.6 160 160 "
    "160c61.2 0 114.3-34.3 141.2-84.7c6.2-11.7 1.8-26.2-9.9-32.5s-26.2-1.8-32.5 "
    "9.9C240 440 202.8 464 160 464C98.1 464 48 413.9 48 352c0-47.9 30.1-88.8 "
    "72.5-104.8zM259.8 176l-1.9-9.7c-4.5-22.3-24-38.3-46.8-38.3c-30.1 0-52.7 "
    "27.5-46.8 57l23.1 115.5c6 29.9 32.2 51.4 62.8 51.4l5.1 0c.4 0 .8 0 1.3 "
    "0l94.1 0c6.7 0 12.6 4.1 15 10.4L402 459.2c6 16.1 23.8 24.6 40.1 19.1l48-16c16.8"
    "-5.6 25.8-23.7 20.2-40.5s-23.7-25.8-40.5-20.2l-18.7 6.2-25.5-68c-11.7-31.2"
    "-41.6-51.9-74.9-51.9l-68.5 0-9.6-48 63.4 0c17.7 0 32-14.3 32-32s-14.3-32-32"
    "-32l-76.2 0z")


def _pin_svg(width, height, fill, glyph, extra_attrs=""):
    """A teardrop pin of a given colour with an inner glyph, at a pixel size."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
        'viewBox="0 0 28 40"%s>'
        '<path d="%s" fill="%s" stroke="#fff" stroke-width="1"/>%s</svg>'
        % (width, height, extra_attrs, _PIN_PATH, fill, glyph))


_WC_GLYPH = ('<g transform="translate(6.6 6.2) scale(0.0293)" fill="#fff">'
             '<path d="%s"/></g>' % _FA_WHEELCHAIR)
# Grey "?" pin for permits where wheelchair access is not explicitly confirmed.
_Q_GLYPH = ('<text x="14" y="19" text-anchor="middle" '
            'font-family="Arial, Helvetica, sans-serif" font-size="14" '
            'font-weight="bold" fill="#fff">?</text>')


def marker_wheelchair(width, height, extra_attrs=""):
    """Blue pin + white wheelchair: listing explicitly mentions wheelchair."""
    return _pin_svg(width, height, "#1f78b4", _WC_GLYPH, extra_attrs)


def marker_unsure(width, height, extra_attrs=""):
    """Grey pin + white '?': accessibility permit, wheelchair not confirmed."""
    return _pin_svg(width, height, "#5b6b7b", _Q_GLYPH, extra_attrs)



# Group the many keyword labels into a few human-friendly filter categories so
# the map's filter stays compact (6 buckets, not 17 checkboxes).
CATEGORY_OF_KEYWORD = {
    "ramp": "ramps", "handrail": "ramps",
    "lift": "lifts",
    "wheelchair": "wheelchair",
    "step-free entry": "entry", "wider doorway": "entry", "automatic door": "entry",
    "grab bar": "bathroom", "accessible bathroom": "bathroom",
    "accessible": "general", "barrier-free": "general", "universal design": "general",
    "ada": "general", "handicap": "general", "mobility": "general",
    "adaptable/visitable": "general", "aging in place": "general",
}
# Display order + labels for the filter checkboxes.
CATEGORY_ORDER = [
    ("ramps", "Ramps & rails"),
    ("lifts", "Lifts & elevators"),
    ("wheelchair", "Wheelchair access"),
    ("entry", "Step-free entry & doors"),
    ("bathroom", "Bathrooms"),
    ("general", "Barrier-free / accessible"),
]


def categories_for(keywords_str):
    """Map a '; '-joined keyword string to its sorted filter-category ids."""
    cats = set()
    for kw in (k.strip() for k in keywords_str.split(";")):
        c = CATEGORY_OF_KEYWORD.get(kw)
        if c:
            cats.add(c)
    return sorted(cats)


def build_points(csv_path, ptype):
    points = []
    if not os.path.exists(csv_path):
        return points
    with open(csv_path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lat, lon = r.get("latitude"), r.get("longitude")
            if not (lat and lon):
                continue
            try:
                latf, lonf = float(lat), float(lon)
            except ValueError:
                continue
            # Address string for Street View: Google geocodes this to the
            # front of the property (on the road), avoiding the rear-lane
            # panorama that nearest-to-coordinate lookups often pick.
            raw_addr = r.get("address", "")
            addr_q = raw_addr.replace(" - ", " ").strip()
            if addr_q:
                addr_q += ", Edmonton, AB, Canada"
            d0 = r.get("earliest_permit_date", "")[:10]
            d1 = r.get("latest_permit_date", "")[:10]

            def _yr(s):
                s = (s or "")[:4]
                return int(s) if s.isdigit() else None

            # "Explicit wheelchair" = the word appears in the matched keywords
            # or in the permit description shown in the popup.
            kw = r.get("keywords", "")
            desc = r.get("sample_description", "")
            is_wheelchair = ("wheelchair" in kw.lower()
                             or "wheelchair" in desc.lower())

            points.append({
                "lat": latf,
                "lon": lonf,
                "q": addr_q,
                "address": r.get("address", ""),
                "neighbourhood": r.get("neighbourhood", ""),
                "ward": r.get("ward", ""),
                "keywords": r.get("keywords", ""),
                "cats": categories_for(r.get("keywords", "")),
                "total": r.get("total_permits", ""),
                "n_b": r.get("n_building_permits", ""),
                "n_d": r.get("n_development_permits", ""),
                "d0": d0,
                "d1": d1,
                "y0": _yr(d0),  # earliest permit year (int or None)
                "y1": _yr(d1),  # latest permit year (int or None)
                "wc": is_wheelchair,
                "type": ptype,   # "home" or "business"
                "desc": r.get("sample_description", ""),
                "source": r.get("coord_source", ""),
            })
    return points


def popup_html(p):
    """Pre-render the static part of the popup. The Street View element is
    injected client-side (at the __SV__ token) so the API key is read at
    runtime and never baked into this committed file."""
    parts = ["<div style='font:14px sans-serif;max-width:290px;"
             "max-height:360px;overflow:auto'>"]
    parts.append("<b>%s</b><br>" % html.escape(p["address"]))
    type_label = "Business / public place" if p.get("type") == "business" else "Home"
    parts.append("<span style='display:inline-block;background:#eef;color:#334;"
                 "font-size:11px;padding:1px 6px;border-radius:3px;margin:2px 0'>"
                 "%s</span><br>" % type_label)
    loc = p["neighbourhood"]
    if p["ward"]:
        loc += " &middot; Ward %s" % html.escape(p["ward"])
    parts.append("<span style='color:#555'>%s</span><br>" % html.escape(loc))
    parts.append("<hr style='margin:5px 0'>")
    if p["keywords"]:
        parts.append("<b>Keywords:</b> %s<br>" % html.escape(p["keywords"]))
    parts.append("<b>Permits:</b> %s (%s building, %s development)<br>"
                 % (p["total"], p["n_b"], p["n_d"]))
    if p["d0"]:
        span = p["d0"] if p["d0"] == p["d1"] else "%s &ndash; %s" % (p["d0"], p["d1"])
        parts.append("<b>Dates:</b> %s<br>" % span)
    if p["desc"]:
        parts.append("<div style='color:#555;font-size:12.5px;margin-top:4px'>%s</div>"
                     % html.escape(p["desc"]))
    # Honesty (Art. 7): flag pins whose location was matched from the address
    # rather than supplied with the record, so users know it may be approximate.
    if p["source"] == "parcel_geocode":
        parts.append("<div style='color:#888;font-size:11px;margin-top:4px'>"
                     "Approximate location (matched by address)</div>")
    parts.append("__SV__")  # filled in by JS (link, or thumbnail if key present)
    parts.append("</div>")
    return "".join(parts)


def main():
    points = build_points(MERGED_CSV, "home") + build_points(COMMERCIAL_CSV, "business")
    for p in points:
        p["popup"] = popup_html(p)
        p["color"] = SOURCE_COLORS.get(p["source"], "#888")

    data_json = json.dumps(points)
    # Only offer filter categories that actually have mapped properties, so
    # there are no dead checkboxes that filter to zero results.
    present = set()
    for p in points:
        present.update(p["cats"])
    visible_categories = [c for c in CATEGORY_ORDER if c[0] in present]
    categories_json = json.dumps(visible_categories)

    html_doc = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- Send the full page URL as the referrer so cross-origin Street View Static
     <img> requests match an HTTP-referrer-restricted key locked to this path.
     (Default policy sends only the origin, which fails the path restriction.) -->
<meta name="referrer" content="no-referrer-when-downgrade">
<title>Edmonton Accessible Housing &ndash; Permit Locations</title>
<!-- favicon lives at the repo root; the map is one level down in data/ -->
<link rel="icon" type="image/png" href="../favicon.png">
<link rel="apple-touch-icon" href="../favicon.png">
<style>
  html,body{margin:0;height:100%}
  #map{height:100%;background:#e8e8e8}
  .panel{position:absolute;top:10px;right:10px;z-index:5;background:#fff;
    padding:12px 14px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
    font:14px sans-serif;max-width:300px;max-height:90vh;overflow:auto}
  .panel h1{margin:0 0 5px;font-size:17px}
  .panel .sub{margin:0 0 8px;color:#333;font-size:13.5px;line-height:1.4}
  .panel details.about{margin:0 0 8px;font-size:13px;color:#555}
  .panel details.about summary{cursor:pointer;color:#1f78b4;font-size:13px}
  .panel details.about .body{margin-top:6px;line-height:1.5}
  .dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:5px}
  .btn{margin-top:6px;padding:6px 10px;border:1px solid #ccc;border-radius:4px;
    background:#f5f5f5;cursor:pointer;font-size:13px;color:#1a1a1a}
  /* Homes / Businesses / Both segmented toggle. */
  #type-toggle{display:flex;margin-top:8px;border:1px solid #1f78b4;border-radius:6px;
    overflow:hidden}
  #type-toggle button{flex:1;padding:6px 4px;border:0;background:#fff;color:#1f78b4;
    font:600 13px sans-serif;cursor:pointer;border-left:1px solid #1f78b4}
  #type-toggle button:first-child{border-left:0}
  #type-toggle button[aria-pressed="true"]{background:#1f78b4;color:#fff}
  /* Prominent wheelchair-only toggle button (aria-pressed = on/off). */
  #wc-only{display:flex;align-items:center;gap:8px;width:100%;margin-top:10px;
    padding:9px 12px;border:2px solid #1f78b4;border-radius:8px;background:#fff;
    color:#1f78b4;font:600 13.5px sans-serif;cursor:pointer;text-align:left}
  #wc-only svg{flex:0 0 auto}
  #wc-only[aria-pressed="true"]{background:#1f78b4;color:#fff}
  /* link-styled but real, keyboard-operable buttons */
  .linkbtn{background:none;border:0;padding:0;color:#1f78b4;cursor:pointer;
    font:inherit;text-decoration:underline}
  #filter-toggle{margin-top:8px;font-size:13px;text-decoration:none}
  #filter-toggle::before{content:"\\25B8 "}
  #filter-toggle[aria-expanded="true"]::before{content:"\\25BE "}
  #filter-body{margin-top:6px;display:none}
  .filter-head{font-weight:bold;margin:8px 0 3px}
  #filter-boxes label{display:block;margin:4px 0;cursor:pointer}
  #filter-actions{font-weight:normal}
  #filter-actions .linkbtn{margin-left:8px;font-size:12px}
  #year-row{font-size:13px}
  #year-row select{font-size:13px;padding:2px 3px;margin:0 3px}
  /* always-visible focus indicator (WCAG 2.4.7) */
  a:focus-visible,button:focus-visible,select:focus-visible,summary:focus-visible,
  input:focus-visible{outline:3px solid #1a5e9c;outline-offset:2px}
  /* accessible text-list alternative to the visual map */
  #list-view{position:absolute;top:0;left:0;bottom:0;width:380px;max-width:92vw;
    z-index:7;background:#fff;box-shadow:2px 0 10px rgba(0,0,0,.3);overflow:auto;
    padding:14px 16px;font:14px sans-serif}
  #list-view[hidden]{display:none}
  #list-view h2{margin:0 28px 2px 0;font-size:18px}
  #list-view ul{list-style:none;margin:10px 0 0;padding:0}
  #list-view li{padding:10px 0;border-top:1px solid #eee;line-height:1.4}
  #list-view li .addr{font-weight:bold}
  #list-view li .meta{color:#444;font-size:13px}
  #list-close{position:absolute;top:10px;right:12px}
  /* "Aligned with The Open State" badge, bottom-centre so it clears Google's
     logo (bottom-left) and Terms link (bottom-right). */
  #os-badge{position:absolute;bottom:6px;left:50%;transform:translateX(-50%);
    z-index:6;background:rgba(255,255,255,.92);border:1px solid #ddd;
    border-radius:14px;padding:3px 11px;font:12px sans-serif;color:#1f78b4;
    text-decoration:none;box-shadow:0 1px 4px rgba(0,0,0,.25);white-space:nowrap}
  #os-badge:hover{background:#fff;text-decoration:underline}
  /* Mobile toggle: hidden on desktop, where the panel is always visible. */
  #panel-toggle{display:none}
  #panel-close{display:none}
  @media (max-width:640px){
    .panel{display:none;top:10px;left:10px;right:10px;max-width:none;
      max-height:80vh;overflow:auto}
    body.panel-open .panel{display:block}
    #panel-toggle{display:block;position:absolute;top:10px;right:10px;z-index:6;
      padding:9px 13px;border:none;border-radius:6px;background:#1f78b4;color:#fff;
      font:14px sans-serif;box-shadow:0 1px 5px rgba(0,0,0,.35);cursor:pointer}
    body.panel-open #panel-toggle{display:none}
    #panel-close{display:block;position:absolute;top:6px;right:10px;font-size:26px;
      line-height:1;color:#666;cursor:pointer;background:none;border:0}
    .panel h1{padding-right:24px}
    #list-view{width:100vw;max-width:100vw}
  }
</style>
</head>
<body>
<main id="map" role="application"
  aria-label="Interactive map of Edmonton homes and businesses with accessibility permits. This visual map is hard to use with a keyboard or screen reader; use the 'View as list' button for an accessible text version of the same places."></main>
<button id="panel-toggle" aria-expanded="false" aria-controls="info-panel">&#9432; Info &amp; filter</button>
<a id="os-badge" href="https://github.com/JCrossman/the-open-state" target="_blank"
   rel="noopener"
   title="This map is built to The Open State's accessibility and openness principles (it is not a full Civic Access Protocol implementation). Learn more.">Aligned with The Open State &#8599;</a>
<aside id="info-panel" class="panel" aria-label="Information and filters">
  <button id="panel-close" aria-label="Close information panel">&times;</button>
  <h1>Edmonton Accessible Housing Map</h1>
  <div class="sub">A map of Edmonton <b>homes</b> &mdash; and now <b>businesses
    &amp; public places</b> &mdash; whose building permits mention ramps, lifts,
    wheelchair access, or barrier-free features. Use the Homes / Businesses
    toggle below. Click any pin for details and a Street View photo.</div>
  <details class="about" open>
    <summary>Why this exists &amp; data source</summary>
    <div class="body"><b>The problem:</b> there is no central list of which Edmonton
      homes are wheelchair-accessible or barrier-free, so finding accessible
      housing often means checking listings one at a time, with no way to search
      for the features that matter. The City's public building &amp; development
      permits <i>do</i> record this work &mdash; ramps, lifts, barrier-free
      bathrooms &mdash; but it is buried in large datasets not built for this
      purpose.<br><br>
      <b>How this helps:</b> this tool pulls those accessibility-related permits
      (2009&ndash;present) and maps them &mdash; homes by default, with a toggle for
      businesses &amp; public places &mdash; so they can be browsed and filtered. A
      starting point for <b>Spinal Cord Injury Alberta</b> and the people it
      serves to find and track accessible places.<br><br>
      It is an automatically generated draft, so some entries may be false matches
      (for example, a parking-garage &ldquo;ramp&rdquo;) &mdash; check the permit
      description in each popup. Not an official listing.</div>
  </details>
  <div id="count"></div>
  <div id="type-toggle" role="group" aria-label="Show homes, businesses, or both">
    <button type="button" data-type="home" aria-pressed="true">Homes</button>
    <button type="button" data-type="business" aria-pressed="false">Businesses</button>
    <button type="button" data-type="both" aria-pressed="false">Both</button>
  </div>
  <div style="margin-top:6px;line-height:1.7">
    __MARKER_WC_LEGEND__ Mentions wheelchair access<br>
    __MARKER_UNSURE_LEGEND__ Accessibility permit &ndash; wheelchair not confirmed
  </div>
  <button id="wc-only" type="button" aria-pressed="false">
    <svg width="20" height="20" viewBox="0 0 512 512" aria-hidden="true" focusable="false">
      <path fill="currentColor" d="__FA_PATH__"/></svg>
    <span>Show only confirmed wheelchair access</span>
  </button>
  <!-- Filter is collapsed by default to keep the interface uncluttered. -->
  <button id="filter-toggle" class="linkbtn" aria-expanded="false" aria-controls="filter-body">Filter</button>
  <div id="filter-body">
    <div class="filter-head">Feature <span id="filter-actions"><button id="filter-all" class="linkbtn">all</button><button id="filter-none" class="linkbtn">none</button></span></div>
    <div id="filter-boxes"></div>
    <div id="year-section">
      <div class="filter-head">Permit year</div>
      <div id="year-row">
        <label for="year-from">From</label> <select id="year-from"></select>
        <label for="year-to">to</label> <select id="year-to"></select>
      </div>
    </div>
  </div>
  <button id="list-toggle" class="btn" aria-expanded="false" aria-controls="list-view">View as list</button>
  <div id="svnote" style="margin-top:6px;color:#666;font-size:11px"></div>
  <button id="svbtn" class="btn"></button>
</aside>
<!-- Accessible text-list alternative to the visual map (keyboard / screen
     reader friendly). Mirrors the current filters. -->
<section id="list-view" aria-label="Accessible housing list" hidden>
  <button id="list-close" class="btn" aria-label="Close list and return to map">Close</button>
  <h2>Accessible housing &mdash; list</h2>
  <p id="list-count"></p>
  <ul id="list-items"></ul>
</section>
<!-- Optional published key. For a shared GitHub Pages map, set the key in
     config.js (window.GOOGLE_MAPS_KEY). It is a browser key: public by design
     and must be HTTP-referrer + quota restricted in the Google Cloud Console.
     The file is optional; the map falls back to a local in-browser key. -->
<script src="config.js" onerror="window.__noConfig=true"></script>
<script src="https://unpkg.com/@googlemaps/markerclusterer/dist/index.min.js"></script>
<script>
var points = __DATA__;
var CATEGORIES = __CATEGORIES__;  // [[id, label], ...] for the feature filter

// localStorage can throw on file:// (e.g. Safari treats it as a unique
// origin), so guard every access.
function lsGet(k){ try { return localStorage.getItem(k); } catch(e){ return null; } }
function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(e){} }
function lsDel(k){ try { localStorage.removeItem(k); } catch(e){} }

// A published (config.js) key applies to everyone; a personal localStorage key
// only overrides it in your own browser.
var EMBEDDED_KEY = (window.GOOGLE_MAPS_KEY || '').trim();
var KEY = (lsGet('gmap_key') || lsGet('sv_key') || EMBEDDED_KEY).trim();

// Two pins (inline SVG data-URIs, no Map ID): blue wheelchair for listings that
// explicitly mention wheelchair, grey "?" for unconfirmed accessibility permits.
var MARKER_WC_URL = 'data:image/svg+xml;charset=UTF-8,'
  + encodeURIComponent(__MARKER_WC_JS__);
var MARKER_UNSURE_URL = 'data:image/svg+xml;charset=UTF-8,'
  + encodeURIComponent(__MARKER_UNSURE_JS__);

function svImgFail(img){ img.parentNode.innerHTML = '(no Street View image at this spot)'; }
function esc(s){ return String(s == null ? '' : s)
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

// Street View uses the street ADDRESS (not raw coords) so Google places the
// camera on the road in front of the property, not in the rear lane.
function svElement(p){
  var q = encodeURIComponent(p.q || (p.lat + ',' + p.lon));
  var link = 'https://www.google.com/maps/search/?api=1&query=' + q;
  var img = 'https://maps.googleapis.com/maps/api/streetview?size=280x160&fov=80&location='
          + q + '&key=' + KEY;
  var alt = 'Street View of ' + esc(p.address || 'this address');
  return "<a href='" + link + "' target='_blank' rel='noopener' "
       + "title='Open " + esc(p.address || 'this address') + " in Google Maps'>"
       + "<img src='" + img + "' alt='" + alt + "' onerror='svImgFail(this)' "
       + "style='margin-top:6px;border-radius:4px;display:block'></a>";
}

// Google Maps needs the key to load the base map itself, so load the API
// script dynamically once we have a key.
function loadGoogle(){
  var s = document.createElement('script');
  s.src = 'https://maps.googleapis.com/maps/api/js?key=' + encodeURIComponent(KEY)
        + '&callback=initMap&loading=async&v=quarterly';
  s.async = true;
  document.head.appendChild(s);
}

window.initMap = function(){
  var map = new google.maps.Map(document.getElementById('map'), {
    center: {lat: 53.5461, lng: -113.4938}, zoom: 11, mapTypeControl: true,
    streetViewControl: true, fullscreenControl: false
  });
  var info = new google.maps.InfoWindow({maxWidth: 300});
  var bounds = new google.maps.LatLngBounds();
  var allMarkers = points.map(function(p){
    var pos = {lat: p.lat, lng: p.lon};
    bounds.extend(pos);
    // Pin depends on whether wheelchair access is explicit.
    var m = new google.maps.Marker({
      position: pos,
      icon: {url: p.wc ? MARKER_WC_URL : MARKER_UNSURE_URL,
             scaledSize: new google.maps.Size(28, 40),
             anchor: new google.maps.Point(14, 40)}
    });
    m._cats = p.cats || [];
    m._type = p.type;            // "home" or "business"
    m._y0 = p.y0; m._y1 = p.y1;   // earliest / latest permit year (or null)
    m._p = p;                      // backing record for the text list
    m.addListener('click', function(){
      info.setContent(p.popup.replace('__SV__', svElement(p)));
      info.open(map, m);
    });
    return m;
  });
  var cluster = new markerClusterer.MarkerClusterer({map: map, markers: allMarkers});
  if (points.length) { map.fitBounds(bounds); }
  document.getElementById('svnote').textContent = 'Tip: use the Map / Satellite toggle (top-left).';

  // ---- Type (Homes / Businesses / Both), default Homes ----
  var typeFilter = 'home';
  var typeBtns = document.querySelectorAll('#type-toggle button');
  typeBtns.forEach(function(btn){
    btn.addEventListener('click', function(){
      typeFilter = btn.getAttribute('data-type');
      typeBtns.forEach(function(b){
        b.setAttribute('aria-pressed', b === btn ? 'true' : 'false');
      });
      applyFilter();
    });
  });

  // ---- Filter (collapsed by default to avoid clutter) ----
  var active = {};
  CATEGORIES.forEach(function(c){ active[c[0]] = true; });
  // "Confirmed wheelchair only" button state. Turning it on sets the Feature
  // filter to Wheelchair access only; turning it off restores the prior choice.
  var wcBtn = document.getElementById('wc-only');
  var savedFeatures = null;
  function wcButtonOff(){ wcBtn.setAttribute('aria-pressed', 'false'); savedFeatures = null; }
  function syncFeatureCheckboxes(){
    CATEGORIES.forEach(function(c){
      document.getElementById('cat-' + c[0]).checked = active[c[0]];
    });
  }

  // Year range, derived from the data. fromY/toY are the current selection.
  var years = [];
  allMarkers.forEach(function(m){
    if (m._y0 != null) years.push(m._y0);
    if (m._y1 != null) years.push(m._y1);
  });
  var yearMin = years.length ? Math.min.apply(null, years) : null;
  var yearMax = years.length ? Math.max.apply(null, years) : null;
  var fromY = yearMin, toY = yearMax;

  function yearOk(m){
    if (fromY == null) return true;          // no year data at all
    if (m._y1 == null) return true;          // undated permit -> never hidden
    return m._y0 <= toY && m._y1 >= fromY;    // activity overlaps selected range
  }

  function renderList(shown){
    var ul = document.getElementById('list-items');
    var html = shown.map(function(m){
      var p = m._p;
      var q = encodeURIComponent(p.q || (p.lat + ',' + p.lon));
      var maps = 'https://www.google.com/maps/search/?api=1&query=' + q;
      var loc = esc(p.neighbourhood) + (p.ward ? ' &middot; Ward ' + esc(p.ward) : '');
      var yrs = p.y0 ? (p.y0 === p.y1 ? p.y0 : p.y0 + '\\u2013' + p.y1) : '';
      var meta = [];
      if (p.keywords) meta.push('Features: ' + esc(p.keywords));
      meta.push('Permits: ' + esc(p.total));
      if (yrs) meta.push('Years: ' + yrs);
      return "<li><span class='addr'>" + esc(p.address) + "</span><br>"
        + "<span class='meta'>" + loc + "</span><br>"
        + "<span class='meta'>" + meta.join(' &middot; ') + "</span><br>"
        + "<a href='" + maps + "' target='_blank' rel='noopener'>Open in Google Maps / Street View &#8599;</a>"
        + "</li>";
    }).join('');
    ul.innerHTML = html;
    document.getElementById('list-count').textContent =
      shown.length + ' place(s) shown (matching the current filters).';
  }

  function applyFilter(){
    var shown = allMarkers.filter(function(m){
      if (typeFilter !== 'both' && m._type !== typeFilter) return false;
      return m._cats.some(function(c){ return active[c]; }) && yearOk(m);
    });
    cluster.clearMarkers();
    cluster.addMarkers(shown);
    // Denominator = everything of the selected type (ignoring other filters).
    var noun = typeFilter === 'home' ? 'homes'
             : typeFilter === 'business' ? 'businesses / public places' : 'places';
    var typeTotal = allMarkers.filter(function(m){
      return typeFilter === 'both' || m._type === typeFilter; }).length;
    var n = shown.length;
    document.getElementById('count').textContent =
      (n === typeTotal) ? (typeTotal + ' ' + noun)
                        : ('Showing ' + n + ' of ' + typeTotal + ' ' + noun);
    renderList(shown);
  }

  var boxes = document.getElementById('filter-boxes');
  CATEGORIES.forEach(function(c){
    var id = 'cat-' + c[0];
    var lbl = document.createElement('label');
    lbl.innerHTML = "<input type='checkbox' id='" + id + "' checked> " + c[1];
    boxes.appendChild(lbl);
    lbl.querySelector('input').addEventListener('change', function(e){
      active[c[0]] = e.target.checked;
      wcButtonOff();   // a manual feature change exits "wheelchair only"
      applyFilter();
    });
  });
  function setAll(v){
    CATEGORIES.forEach(function(c){
      active[c[0]] = v;
      document.getElementById('cat-' + c[0]).checked = v;
    });
    wcButtonOff();
    applyFilter();
  }
  document.getElementById('filter-all').onclick = function(){ setAll(true); };
  document.getElementById('filter-none').onclick = function(){ setAll(false); };
  // "Show only confirmed wheelchair access": turning it ON sets the Feature
  // filter to Wheelchair access only (checkboxes update to match); turning it
  // OFF restores the prior feature selection. So the filter panel always
  // reflects what the map shows.
  wcBtn.addEventListener('click', function(){
    var turningOn = wcBtn.getAttribute('aria-pressed') !== 'true';
    if (turningOn) {
      savedFeatures = {};
      CATEGORIES.forEach(function(c){
        savedFeatures[c[0]] = active[c[0]];
        active[c[0]] = (c[0] === 'wheelchair');
      });
      wcBtn.setAttribute('aria-pressed', 'true');
    } else {
      CATEGORIES.forEach(function(c){
        active[c[0]] = savedFeatures ? !!savedFeatures[c[0]] : true;
      });
      savedFeatures = null;
      wcBtn.setAttribute('aria-pressed', 'false');
    }
    syncFeatureCheckboxes();
    applyFilter();
  });

  // Year dropdowns (From / To) -- accessible alternative to a range slider.
  var yearSection = document.getElementById('year-section');
  if (yearMin == null) {
    yearSection.style.display = 'none';
  } else {
    var selFrom = document.getElementById('year-from');
    var selTo = document.getElementById('year-to');
    for (var y = yearMin; y <= yearMax; y++) {
      var oF = document.createElement('option'); oF.value = y; oF.text = y; selFrom.appendChild(oF);
      var oT = document.createElement('option'); oT.value = y; oT.text = y; selTo.appendChild(oT);
    }
    selFrom.value = yearMin; selTo.value = yearMax;
    selFrom.addEventListener('change', function(){
      fromY = parseInt(selFrom.value, 10);
      if (fromY > toY) { toY = fromY; selTo.value = toY; }   // keep From <= To
      applyFilter();
    });
    selTo.addEventListener('change', function(){
      toY = parseInt(selTo.value, 10);
      if (toY < fromY) { fromY = toY; selFrom.value = fromY; }
      applyFilter();
    });
  }

  var ft = document.getElementById('filter-toggle');
  ft.onclick = function(){
    var b = document.getElementById('filter-body');
    var open = b.style.display === 'block';
    b.style.display = open ? 'none' : 'block';
    ft.setAttribute('aria-expanded', open ? 'false' : 'true');
  };

  // Accessible list view: open/close, move focus, restore focus on close.
  var listView = document.getElementById('list-view');
  var listToggle = document.getElementById('list-toggle');
  var listClose = document.getElementById('list-close');
  listToggle.onclick = function(){
    listView.hidden = false;
    listToggle.setAttribute('aria-expanded', 'true');
    listClose.focus();
  };
  listClose.onclick = function(){
    listView.hidden = true;
    listToggle.setAttribute('aria-expanded', 'false');
    listToggle.focus();
  };
  listView.addEventListener('keydown', function(e){
    if (e.key === 'Escape') { listClose.click(); }
  });

  applyFilter();  // sets the initial count + builds the list
};

// Shown by Google when the key is missing/unauthorized for Maps JS API.
window.gm_authFailure = function(){
  document.getElementById('count').innerHTML =
    "<b style='color:#b00'>Map could not load.</b> The key needs the "
    + "'Maps JavaScript API' enabled (and billing on). Click the button to re-enter it.";
};

function promptForKey(initial){
  var k = prompt('Paste your Google Maps API key (needs Maps JavaScript API + '
               + 'Street View Static API enabled). Leave blank to clear:', initial || '');
  if (k === null) return false;
  KEY = k.trim();
  if (KEY) { lsSet('gmap_key', KEY); } else { lsDel('gmap_key'); lsDel('sv_key'); }
  return true;
}

// With a published key, viewers don't need the button at all -- hide it.
if (EMBEDDED_KEY) {
  document.getElementById('svbtn').style.display = 'none';
} else {
  document.getElementById('svbtn').textContent = KEY ? 'Change Google key' : 'Enter Google key to load map';
  document.getElementById('svbtn').onclick = function(){
    if (promptForKey(KEY)) { location.reload(); }
  };
}

if (KEY) {
  document.getElementById('svnote').textContent = 'Loading Google map…';
  loadGoogle();
} else {
  document.getElementById('count').textContent = points.length + ' addresses ready.';
  document.getElementById('svnote').textContent =
    'Enter your Google key (button below) to load the map and Street View photos.';
}

// Mobile: the info/filter panel is hidden behind a button so it does not cover
// the map. These controls do nothing on desktop (the panel is always shown).
var panelToggle = document.getElementById('panel-toggle');
var panelClose = document.getElementById('panel-close');
panelToggle.onclick = function(){
  document.body.classList.add('panel-open');
  panelToggle.setAttribute('aria-expanded', 'true');
  panelClose.focus();
};
panelClose.onclick = function(){
  document.body.classList.remove('panel-open');
  panelToggle.setAttribute('aria-expanded', 'false');
  panelToggle.focus();
};
</script>
</body>
</html>
"""
    legend_attrs = (' aria-hidden="true" focusable="false" '
                    'style="vertical-align:middle;margin-right:5px"')
    html_doc = html_doc.replace("__DATA__", data_json)
    html_doc = html_doc.replace("__CATEGORIES__", categories_json)
    html_doc = html_doc.replace("__MARKER_WC_JS__", json.dumps(marker_wheelchair(28, 40)))
    html_doc = html_doc.replace("__MARKER_UNSURE_JS__", json.dumps(marker_unsure(28, 40)))
    html_doc = html_doc.replace("__MARKER_WC_LEGEND__", marker_wheelchair(16, 23, legend_attrs))
    html_doc = html_doc.replace("__MARKER_UNSURE_LEGEND__", marker_unsure(16, 23, legend_attrs))
    html_doc = html_doc.replace("__FA_PATH__", _FA_WHEELCHAIR)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print("Mapped %d located addresses -> %s" % (len(points), OUT_HTML))
    print("Base map: Google Maps JavaScript API. Enter the key via the in-map "
          "button (stored in the browser only). The key needs both the Maps "
          "JavaScript API and Street View Static API enabled.")


if __name__ == "__main__":
    main()
