#!/usr/bin/env python3
"""Accessibility keyword list + matching, shared by the fetch adapters
(`fetchers.py`, which builds the per-platform server-side prefilters) and the
Python `classify_keywords` source-of-truth pass.

Stdlib only (`re`).
"""
import re

# Accessibility keywords. Each entry is the canonical label; the list of
# variants are the substrings actually matched (case-insensitive). French
# variants (for Montreal) are accessibility-specific on purpose -- generic words
# like porte/escalier/acces are deliberately excluded (measured as noise). Note
# also that bare "rampe" (= balcony/stair RAILING in Quebecois French) and "main
# courante" (= stair handrail) are NOT used: in Montreal data they overwhelmingly
# describe railings, not wheelchair ramps, so we match only "rampe d'acces".
KEYWORDS = {
    "ramp": ["ramp", "rampe d'accès", "rampe d'acces"],
    "wheelchair": ["wheelchair", "wheel chair", "fauteuil roulant"],
    "accessible": ["accessible", "accessibility",
                   "accessibilité", "accessibilite"],
    "barrier-free": ["barrier-free", "barrier free", "sans obstacle"],
    "grab bar": ["grab bar", "barre d'appui", "barre d appui"],
    "lift": ["lift", "elevator", "ascenseur", "élévateur", "elevateur",
             "plate-forme élévatrice", "plateforme elevatrice"],
    "mobility": ["mobility", "mobilité réduite", "mobilite reduite"],
    "handicap": ["handicap", "handicapé", "handicape"],
    "universal design": ["universal design", "design universel",
                         "conception universelle"],
    "ada": ["ada compliant", "ada-compliant"],
    # High-value additions, especially for older years (2009-2015) that used
    # plainer construction wording rather than "barrier-free"/"accessible".
    "handrail": ["handrail", "hand rail"],
    "step-free entry": ["no-step", "no step", "step-free", "step free",
                        "level entry", "zero threshold", "no threshold",
                        "curbless"],
    "accessible bathroom": ["roll-in shower", "roll in shower", "walk-in tub",
                            "walk in tub", "curbless shower"],
    "wider doorway": ["widen door", "door widening", "wider door",
                      "widened door", "doorway widening"],
    "adaptable/visitable": ["adaptable", "visitable", "visitability",
                            "logement adaptable"],
    "aging in place": ["aging in place", "age in place"],
    "automatic door": ["automatic door", "power door operator",
                       "powered door", "auto door operator",
                       "porte automatique", "ouvre-porte"],
}

# Matching is plain case-insensitive substring, EXCEPT for a few short English
# tokens that collide with foreign words as substrings. The English token "ramp"
# is a substring of French "rampe" (= a balcony/stair RAILING), which would flood
# Montreal with thousands of railing permits. For those tokens we use a regex
# instead; every other token stays a fast substring test, so English-city results
# are unchanged. (French wheelchair ramps are still caught by the explicit
# "rampe d'acces" variants below.)
_KEYWORD_REGEX = {
    # ramp / ramps / wheelchair ramp, but NOT French "rampe" (railing).
    "ramp": re.compile(r"ramp(?!e)"),
}


def _variant_matches(variant, blob):
    """True if `variant` is present in `blob` (already lowercased)."""
    rx = _KEYWORD_REGEX.get(variant)
    if rx is not None:
        return rx.search(blob) is not None
    return variant in blob


def classify_keywords(row, text_fields):
    """Return the set of canonical keyword labels present in this row."""
    blob = " ".join(str(row.get(f, "")) for f in text_fields).lower()
    hits = set()
    for label, variants in KEYWORDS.items():
        if any(_variant_matches(v, blob) for v in variants):
            hits.add(label)
    return hits
