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
OUT_HTML = os.path.join(DATA_DIR, "edmonton_accessibility_map.html")

SOURCE_COLORS = {
    "development": "#1f78b4",     # blue
    "parcel_geocode": "#33a02c",  # green
}

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


def build_points():
    points = []
    with open(MERGED_CSV, encoding="utf-8") as f:
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
                "d0": r.get("earliest_permit_date", "")[:10],
                "d1": r.get("latest_permit_date", "")[:10],
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
    parts.append("__SV__")  # filled in by JS (link, or thumbnail if key present)
    parts.append("</div>")
    return "".join(parts)


def main():
    points = build_points()
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
<style>
  html,body{margin:0;height:100%}
  #map{height:100%;background:#e8e8e8}
  .panel{position:absolute;top:10px;right:10px;z-index:5;background:#fff;
    padding:12px 14px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
    font:14px sans-serif;max-width:300px;max-height:90vh;overflow:auto}
  .panel h3{margin:0 0 5px;font-size:17px}
  .panel .sub{margin:0 0 8px;color:#333;font-size:13.5px;line-height:1.4}
  .panel details.about{margin:0 0 8px;font-size:13px;color:#555}
  .panel details.about summary{cursor:pointer;color:#1f78b4;outline:none;font-size:13px}
  .panel details.about .body{margin-top:6px;line-height:1.5}
  .dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:5px}
  .btn{margin-top:6px;padding:5px 9px;border:1px solid #ccc;border-radius:4px;
    background:#f5f5f5;cursor:pointer;font-size:13px}
  #filter-toggle{margin-top:8px;cursor:pointer;color:#1f78b4;user-select:none;font-size:13px}
  #filter-body{margin-top:6px;display:none}
  #filter-body label{display:block;margin:4px 0;cursor:pointer}
  #filter-actions a{color:#1f78b4;cursor:pointer;margin-right:8px;font-size:12px}
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
    #panel-close{display:block;position:absolute;top:8px;right:12px;font-size:24px;
      line-height:1;color:#888;cursor:pointer}
    .panel h3{padding-right:24px}
  }
</style>
</head>
<body>
<div id="map"></div>
<button id="panel-toggle">&#9432; Info &amp; filter</button>
<div class="panel">
  <span id="panel-close" title="Close">&times;</span>
  <h3>Edmonton Accessible Housing Map</h3>
  <div class="sub">A map to help find accessible housing in Edmonton &mdash; homes
    whose building permits mention ramps, lifts, wheelchair access, or
    barrier-free features. Click any dot for details and a Street View photo.</div>
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
      (2009&ndash;present), narrows them to homes, and maps them so they can be
      browsed and filtered &mdash; a starting point for <b>Spinal Cord Injury
      Alberta</b> and the people it serves to find and track accessible housing.<br><br>
      It is an automatically generated draft, so some entries may be false matches
      (for example, a parking-garage &ldquo;ramp&rdquo;) &mdash; check the permit
      description in each popup. Not an official listing.</div>
  </details>
  <div id="count"></div>
  <div style="margin-top:6px">
    <span class="dot" style="background:#1f78b4"></span>From development permit<br>
    <span class="dot" style="background:#33a02c"></span>Geocoded (parcel)
  </div>
  <!-- Filter is collapsed by default to keep the interface uncluttered. -->
  <div id="filter-toggle">&#9656; Filter by feature</div>
  <div id="filter-body">
    <div id="filter-actions"><a id="filter-all">All</a><a id="filter-none">None</a></div>
    <div id="filter-boxes"></div>
  </div>
  <div id="svnote" style="margin-top:6px;color:#666;font-size:11px"></div>
  <button id="svbtn" class="btn"></button>
</div>
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

function svImgFail(img){ img.parentNode.innerHTML = '(no Street View image at this spot)'; }

// Street View uses the street ADDRESS (not raw coords) so Google places the
// camera on the road in front of the property, not in the rear lane.
function svElement(p){
  var q = encodeURIComponent(p.q || (p.lat + ',' + p.lon));
  var link = 'https://www.google.com/maps/search/?api=1&query=' + q;
  var img = 'https://maps.googleapis.com/maps/api/streetview?size=280x160&fov=80&location='
          + q + '&key=' + KEY;
  return "<a href='" + link + "' target='_blank' title='Open in Google Maps'>"
       + "<img src='" + img + "' alt='Street View' onerror='svImgFail(this)' "
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
    var m = new google.maps.Marker({
      position: pos,
      icon: {path: google.maps.SymbolPath.CIRCLE, scale: 7,
             fillColor: p.color, fillOpacity: 0.95,
             strokeColor: '#fff', strokeWeight: 1.5}
    });
    m._cats = p.cats || [];
    m.addListener('click', function(){
      info.setContent(p.popup.replace('__SV__', svElement(p)));
      info.open(map, m);
    });
    return m;
  });
  var cluster = new markerClusterer.MarkerClusterer({map: map, markers: allMarkers});
  if (points.length) { map.fitBounds(bounds); }
  document.getElementById('svnote').textContent = 'Tip: use the Map / Satellite toggle (top-left).';

  // ---- Feature filter (collapsed by default to avoid clutter) ----
  var active = {};
  CATEGORIES.forEach(function(c){ active[c[0]] = true; });

  function applyFilter(){
    var shown = allMarkers.filter(function(m){
      return m._cats.some(function(c){ return active[c]; });
    });
    cluster.clearMarkers();
    cluster.addMarkers(shown);
    var n = shown.length, total = allMarkers.length;
    document.getElementById('count').textContent =
      (n === total) ? (total + ' located addresses')
                    : ('Showing ' + n + ' of ' + total + ' addresses');
  }

  var boxes = document.getElementById('filter-boxes');
  CATEGORIES.forEach(function(c){
    var id = 'cat-' + c[0];
    var lbl = document.createElement('label');
    lbl.innerHTML = "<input type='checkbox' id='" + id + "' checked> " + c[1];
    boxes.appendChild(lbl);
    lbl.querySelector('input').addEventListener('change', function(e){
      active[c[0]] = e.target.checked; applyFilter();
    });
  });
  function setAll(v){
    CATEGORIES.forEach(function(c){
      active[c[0]] = v;
      document.getElementById('cat-' + c[0]).checked = v;
    });
    applyFilter();
  }
  document.getElementById('filter-all').onclick = function(){ setAll(true); };
  document.getElementById('filter-none').onclick = function(){ setAll(false); };
  var ft = document.getElementById('filter-toggle');
  ft.onclick = function(){
    var b = document.getElementById('filter-body');
    var open = b.style.display === 'block';
    b.style.display = open ? 'none' : 'block';
    ft.innerHTML = (open ? '\\u25B8' : '\\u25BE') + ' Filter by feature';
  };

  applyFilter();  // sets the initial count
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
document.getElementById('panel-toggle').onclick = function(){
  document.body.classList.add('panel-open');
};
document.getElementById('panel-close').onclick = function(){
  document.body.classList.remove('panel-open');
};
</script>
</body>
</html>
"""
    html_doc = html_doc.replace("__DATA__", data_json)
    html_doc = html_doc.replace("__CATEGORIES__", categories_json)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print("Mapped %d located addresses -> %s" % (len(points), OUT_HTML))
    print("Base map: Google Maps JavaScript API. Enter the key via the in-map "
          "button (stored in the browser only). The key needs both the Maps "
          "JavaScript API and Street View Static API enabled.")


if __name__ == "__main__":
    main()
