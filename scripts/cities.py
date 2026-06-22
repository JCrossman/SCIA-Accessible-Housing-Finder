#!/usr/bin/env python3
"""Per-city configuration for the accessibility-permit pipeline.

Each city is a plain dict describing everything that varies between
municipalities: the open-data domain, the building/development dataset ids, a
mapping from this pipeline's canonical field names to that city's real column
names, the residential-vs-commercial classification rule, and whether the
permits already carry coordinates (so the parcel-geocoding step can be skipped).

Everything else -- the accessibility keyword list, the keyword helpers, address
de-duplication, the marker/popup/filter logic, and the map UI -- is shared and
city-agnostic. Each city declares a "platform" (socrata | opendatasoft); the
query step has a fetch adapter per platform. Adding another city on an
already-supported platform is just another entry here.
"""

CITIES = {
    "edmonton": {
        "display_name": "Edmonton",
        "platform": "socrata",
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
        "platform": "socrata",
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
            # Personal/identifying columns the pipeline never uses -- dropped
            # before writing CSVs so no names are republished (minimization).
            "drop_fields": ["applicantname", "contractorname"],
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
            "drop_fields": ["applicant"],
        },
        "residential": {
            "kind": "calgary",
            "building_class_field": "permitclassmapped",
            "building_residential_values": {"Residential"},
            "development_district_field": "landusedistrict",
        },
        "geocode": {"needed": False},  # permits already carry coordinates
    },

    "vancouver": {
        "display_name": "Vancouver",
        "platform": "opendatasoft",
        "domain": "https://opendata.vancouver.ca",
        "streetview_suffix": ", Vancouver, BC, Canada",
        "map_center": {"lat": 49.2606, "lon": -123.1140},  # fallback only
        "building": {
            "dataset": "issued-building-permits",
            "text_fields": ["projectdescription"],
            "address_field": "address",
            "neighbourhood_field": "geolocalarea",
            "date_field": "issuedate",
            "id_field": "permitnumber",
            # Coords are normalized from geo_point_2d into latitude/longitude by
            # the OpenDataSoft fetch adapter, so the rest of the pipeline is uniform.
            "lat_field": "latitude", "lon_field": "longitude",
            # Names AND addresses of applicants/contractors the pipeline never
            # uses -- dropped before writing CSVs (minimization).
            "drop_fields": ["applicant", "applicantaddress",
                            "buildingcontractor", "buildingcontractoraddress"],
        },
        # Vancouver's issued building permits are a standalone dataset; there is
        # no separate development-permit dataset to merge.
        "development": None,
        "residential": {
            "kind": "vancouver",
            "building_class_field": "propertyuse",
            "building_residential_values": {"Dwelling Uses", "Live-Work Uses"},
        },
        "geocode": {"needed": False},  # coords included in the permits
    },

    "toronto": {
        "display_name": "Toronto",
        "platform": "ckan",
        # CKAN API host (the open.toronto.ca portal is a front end for this).
        "domain": "https://ckan0.cf.opendata.inter.prod-toronto.ca",
        "streetview_suffix": ", Toronto, ON, Canada",
        "map_center": {"lat": 43.6532, "lon": -79.3832},  # fallback only
        "building": {
            # CKAN resource ids. Active = open/in-progress permits; Cleared =
            # completed permits (they move out of Active once closed). Query both
            # datastore resources via `q`, plus the pre-2017 flat CSV (not
            # datastore-active) via download-and-filter.
            "dataset": [
                "6d0229af-bc54-46de-9c2b-26759b01dd05",  # Active Permits
                "a96c0ba4-3026-402b-b09d-5b1268b8f810",  # Cleared Permits (since 2017)
                {"id": "c647bdae-0127-425e-86e6-2d88ff0e2adf",  # Cleared 2000-2016
                 "download": True},
            ],
            "text_fields": ["DESCRIPTION"],   # free-text work description
            "address_field": "address",       # synthesized by the CKAN adapter
            "address_compose": ["STREET_NUM", "STREET_NAME", "STREET_TYPE", "STREET_DIRECTION"],
            "neighbourhood_field": "neighbourhood",  # absent -> blank (no nbhd field)
            "date_field": "ISSUED_DATE",
            "id_field": "PERMIT_NUM",
            "lat_field": None, "lon_field": None,   # no coords -> needs geocoding
            "drop_fields": ["BUILDER_NAME"],        # unused name -> minimization
        },
        "development": None,   # Toronto building permits only
        "residential": {
            # RESIDENTIAL is square-metres of residential occupancy; > 0 -> home.
            "kind": "toronto",
            "building_residential_numeric_field": "RESIDENTIAL",
        },
        "geocode": {
            "needed": True,
            "platform": "ckan",
            "domain": "https://ckan0.cf.opendata.inter.prod-toronto.ca",
            # Address Points (Municipal) - Toronto One Address Repository.
            "dataset": "0b3756af-9caf-4f0f-ac28-9c6617adede4",
            "number_field": "ADDRESS_NUMBER",
            "name_field": "LINEAR_NAME_FULL",
        },
    },

    # --- ArcGIS REST cities (building permits with free-text descriptions and
    # geometry; coords come from the layer, so no geocoding). ---

    "mississauga": {
        "display_name": "Mississauga",
        "platform": "arcgis",
        "domain": "https://services6.arcgis.com/hM5ymMLbxIyWTjn2",
        "streetview_suffix": ", Mississauga, ON, Canada",
        "map_center": {"lat": 43.589, "lon": -79.644},  # fallback only
        "building": {
            "dataset": "https://services6.arcgis.com/hM5ymMLbxIyWTjn2/arcgis/rest/"
                       "services/Issued_Building_Permits/FeatureServer/0",
            "text_fields": ["DESCRIPTION"],
            "address_field": "ADDRESS",
            "neighbourhood_field": "",   # no neighbourhood name field
            "date_field": "ISSUE_DATE",
            "id_field": "BP_NO",
            "lat_field": "latitude", "lon_field": "longitude",  # from geometry
        },
        "development": None,
        "residential": {
            "kind": "textscan",
            "residential_text_fields": ["FILE_TYPE", "BLDG_TYPE", "DESCRIPTION"],
        },
        "geocode": {"needed": False},
    },

    "markham": {
        "display_name": "Markham",
        "platform": "arcgis",
        "domain": "https://services5.arcgis.com/QJebCdoMf4PF8fJP",
        "streetview_suffix": ", Markham, ON, Canada",
        "map_center": {"lat": 43.857, "lon": -79.337},  # fallback only
        "building": {
            "dataset": "https://services5.arcgis.com/QJebCdoMf4PF8fJP/arcgis/rest/"
                       "services/Building_Permits/FeatureServer/0",
            "text_fields": ["FOLDERDESCRIPTION"],
            "address_field": "FOLDERNAME",   # e.g. "3308 Lakeshore Road W"
            "neighbourhood_field": "",
            "date_field": "ISSUEDATE",
            "id_field": "CUSTOMFOLDERNUMBER",
            "lat_field": "latitude", "lon_field": "longitude",
        },
        "development": None,
        "residential": {
            "kind": "textscan",
            "residential_text_fields": ["Subtype", "FOLDERDESCRIPTION"],
        },
        "geocode": {"needed": False},
    },

    "ottawa": {
        "display_name": "Ottawa",
        "platform": "excel",
        "domain": "https://www.arcgis.com",
        "streetview_suffix": ", Ottawa, ON, Canada",
        "map_center": {"lat": 45.4215, "lon": -75.6972},  # fallback only
        "building": {
            # Ottawa publishes permits as yearly Excel files (ArcGIS item ids).
            # The excel adapter normalizes columns to: description, blg_type,
            # ward, address (ST# + ROAD); the file's year stamps permit_date.
            "dataset": [
                {"id": "89846cecb39749b7b7db7ba74fb9d31d", "year": 2011},  # Jul-Dec
                {"id": "aba9890bf3334eeb8e31501e92f2f83c", "year": 2012},
                {"id": "9e821ceff81d468aa68111ae9d624b87", "year": 2013},
                {"id": "60c0f061232749e1ae7ea5263b61f25f", "year": 2014},  # .xls
                {"id": "a2b214f0d407491793359af4af32e7ba", "year": 2015},  # .xls
                {"id": "273b520c126f40cfbf24a7689902fb85", "year": 2016},
                {"id": "8dcbb960cc9f452e852494fb181ea91a", "year": 2017},
                {"id": "c42cca39b76d456e9c22e8343a55b802", "year": 2018},
                {"id": "a457ac00a07647e0a913a28052df4d85", "year": 2019},
                {"id": "54afabbba45a4607a420c6f9d7b88842", "year": 2020},  # .xls
                {"id": "dc3eecea58054e0e90ed25d8988495e1", "year": 2021},  # .xls
                {"id": "6b99841340444f83ba2595190e4e143b", "year": 2022},  # .xls
                {"id": "0c19879709c14d008d078f2ae3007e07", "year": 2023},
                {"id": "05046d836248455d92cbc0543ce4c022", "year": 2024},  # 2024-25
                {"id": "429ea52d2ff040c799afde2b40b90f68", "year": 2026},
            ],
            "text_fields": ["description"],
            "address_field": "address",
            "neighbourhood_field": "ward",
            "date_field": "permit_date",
            "id_field": "",   # the spreadsheets carry no permit number
            "lat_field": "latitude", "lon_field": "longitude",  # filled by geocoder
        },
        "development": None,
        "residential": {
            "kind": "textscan",
            "residential_text_fields": ["blg_type", "description"],
        },
        "geocode": {
            "needed": True,
            "platform": "arcgis",
            "layer": "https://maps.ottawa.ca/arcgis/rest/services/"
                     "Address_Information/MapServer/0",
            "number_field": "ADDRNUM",
            "road_field": "FULL_ROADNAME_EN",
        },
    },

    "montreal": {
        "display_name": "Montréal",
        "platform": "ckan",
        "domain": "https://donnees.montreal.ca",
        "streetview_suffix": ", Montréal, QC, Canada",
        "map_center": {"lat": 45.5089, "lon": -73.5617},  # fallback only
        "building": {
            # CKAN datastore resource: "Permis de construction, transformation
            # et démolition". Free-text French nature_travaux + coords.
            "dataset": "5232a72d-235a-48eb-ae20-bb9d501300ad",
            "text_fields": ["nature_travaux"],   # free-text French description
            "address_field": "emplacement",       # single field; no compose
            "neighbourhood_field": "arrondissement",
            "date_field": "date_emission",
            "id_field": "id_permis",
            "lat_field": "latitude", "lon_field": "longitude",  # present in records
        },
        "development": None,
        "residential": {
            "kind": "textscan",
            "residential_text_fields": ["description_categorie_batiment",
                                        "description_type_batiment", "nature_travaux"],
        },
        "geocode": {"needed": False},  # coordinates included in the permits
    },
}


def get_city(slug):
    """Return the config for a city slug, with a clear error if unknown."""
    try:
        return CITIES[slug]
    except KeyError:
        raise SystemExit("Unknown city '%s'. Known: %s"
                         % (slug, ", ".join(sorted(CITIES))))
