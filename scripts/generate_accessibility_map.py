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
    parts = ["<div style='font:13px sans-serif;max-width:280px;"
             "max-height:340px;overflow:auto'>"]
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
        parts.append("<div style='color:#666;font-size:11px;margin-top:4px'>%s</div>"
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

    html_doc = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Edmonton Accessible Housing &ndash; Permit Locations</title>
<style>
  html,body{margin:0;height:100%}
  #map{height:100%;background:#e8e8e8}
  .panel{position:absolute;top:10px;right:10px;z-index:5;background:#fff;
    padding:10px 12px;border-radius:6px;box-shadow:0 1px 5px rgba(0,0,0,.3);
    font:13px sans-serif;max-width:250px}
  .panel h3{margin:0 0 6px;font-size:14px}
  .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}
  .btn{margin-top:6px;padding:4px 8px;border:1px solid #ccc;border-radius:4px;
    background:#f5f5f5;cursor:pointer;font-size:12px}
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <h3>Accessible Housing Permits</h3>
  <div id="count"></div>
  <div style="margin-top:6px">
    <span class="dot" style="background:#1f78b4"></span>From development permit<br>
    <span class="dot" style="background:#33a02c"></span>Geocoded (parcel)
  </div>
  <div id="svnote" style="margin-top:6px;color:#666;font-size:11px"></div>
  <button id="svbtn" class="btn"></button>
</div>
<script src="https://unpkg.com/@googlemaps/markerclusterer/dist/index.min.js"></script>
<script>
var points = __DATA__;

// localStorage can throw on file:// (e.g. Safari treats it as a unique
// origin), so guard every access.
function lsGet(k){ try { return localStorage.getItem(k); } catch(e){ return null; } }
function lsSet(k,v){ try { localStorage.setItem(k,v); } catch(e){} }
function lsDel(k){ try { localStorage.removeItem(k); } catch(e){} }

var KEY = (lsGet('gmap_key') || lsGet('sv_key') || (window.GOOGLE_MAPS_KEY || '')).trim();

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
    streetViewControl: true
  });
  var info = new google.maps.InfoWindow({maxWidth: 300});
  var bounds = new google.maps.LatLngBounds();
  var markers = points.map(function(p){
    var pos = {lat: p.lat, lng: p.lon};
    bounds.extend(pos);
    var m = new google.maps.Marker({
      position: pos,
      icon: {path: google.maps.SymbolPath.CIRCLE, scale: 7,
             fillColor: p.color, fillOpacity: 0.95,
             strokeColor: '#fff', strokeWeight: 1.5}
    });
    m.addListener('click', function(){
      info.setContent(p.popup.replace('__SV__', svElement(p)));
      info.open(map, m);
    });
    return m;
  });
  new markerClusterer.MarkerClusterer({map: map, markers: markers});
  if (points.length) { map.fitBounds(bounds); }
  document.getElementById('count').textContent = points.length + ' located addresses';
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

document.getElementById('svbtn').textContent = KEY ? 'Change Google key' : 'Enter Google key to load map';
document.getElementById('svbtn').onclick = function(){
  if (promptForKey(KEY)) { location.reload(); }
};

if (KEY) {
  document.getElementById('svnote').textContent = 'Loading Google map…';
  loadGoogle();
} else {
  document.getElementById('count').textContent = points.length + ' addresses ready.';
  document.getElementById('svnote').textContent =
    'Enter your Google key (button below) to load the map and Street View photos.';
}
</script>
</body>
</html>
"""
    html_doc = html_doc.replace("__DATA__", data_json)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print("Mapped %d located addresses -> %s" % (len(points), OUT_HTML))
    print("Base map: Google Maps JavaScript API. Enter the key via the in-map "
          "button (stored in the browser only). The key needs both the Maps "
          "JavaScript API and Street View Static API enabled.")


if __name__ == "__main__":
    main()
