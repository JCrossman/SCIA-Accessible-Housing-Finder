#!/usr/bin/env python3
"""Per-city configuration for the accessibility-permit pipeline.

Each city is a plain dict describing everything that varies between
municipalities: the open-data domain, the building/development dataset ids, a
mapping from this pipeline's canonical field names to that city's real column
names, the residential-vs-commercial classification rule, and whether the
permits already carry coordinates (so the parcel-geocoding step can be skipped).

Everything else -- the accessibility keyword list, the SoQL helpers, address
de-duplication, the marker/popup/filter logic, and the map UI -- is shared and
city-agnostic. Adding another *Socrata* city should be just another entry here.
(ArcGIS-based cities would need a second fetch adapter and are not yet covered.)
"""

CITIES = {
    "edmonton": {
        "display_name": "Edmonton",
        "domain": "https://data.edmonton.ca",
        "streetview_suffix": ", Edmonton, AB, Canada",
        "map_center": {"lat": 53.5461, "lon": -113.4938},  # fallback only
        "building": {
            "dataset": "24uj-dj8v",
            "text_fields": ["job_description", "job_category"],
            "address_field": "address",
            "neighbourhood_field": "neighbourhood",
            "date_field": "permit_date",
            "id_field": "row_id",
            "lat_field": None, "lon_field": None,   # building permits lack coords
        },
        "development": {
            "dataset": "2ccn-pwtu",
            "text_fields": ["description_of_development"],
            "address_field": "address",
            "neighbourhood_field": "neighbourhood",
            "ward_field": "ward",
            "date_field": "permit_date",
            "id_field": "city_file_number",
            "lat_field": "latitude", "lon_field": "longitude",
        },
        "residential": {"kind": "edmonton"},
        "geocode": {
            "needed": True,
            "dataset": "ut27-nrpn",
            "house_field": "house_number",
            "street_field": "street_name",
            "lat_field": "latitude",
            "lon_field": "longitude",
        },
    },

    "calgary": {
        "display_name": "Calgary",
        "domain": "https://data.calgary.ca",
        "streetview_suffix": ", Calgary, AB, Canada",
        "map_center": {"lat": 51.0447, "lon": -114.0719},  # fallback only
        "building": {
            "dataset": "c2es-76ed",
            "text_fields": ["description"],
            "address_field": "originaladdress",
            "neighbourhood_field": "communityname",
            "date_field": "issueddate",
            "id_field": "permitnum",
            "lat_field": "latitude", "lon_field": "longitude",  # coords present
        },
        "development": {
            "dataset": "6933-unw5",
            "text_fields": ["description", "proposedusedescription"],
            "address_field": "address",
            "neighbourhood_field": "communityname",
            "ward_field": None,
            "date_field": "applieddate",
            "id_field": "permitnum",
            "lat_field": "latitude", "lon_field": "longitude",
        },
        "residential": {
            "kind": "calgary",
            "building_class_field": "permitclassmapped",
            "building_residential_values": {"Residential"},
            "development_district_field": "landusedistrict",
        },
        "geocode": {"needed": False},  # permits already carry coordinates
    },
}


def get_city(slug):
    """Return the config for a city slug, with a clear error if unknown."""
    try:
        return CITIES[slug]
    except KeyError:
        raise SystemExit("Unknown city '%s'. Known: %s"
                         % (slug, ", ".join(sorted(CITIES))))
