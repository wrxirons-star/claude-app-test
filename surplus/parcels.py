"""County parcel lookups via public GIS feature services.

Property appraiser web pages often block automation, but many counties also
publish the same roll data through an ArcGIS FeatureServer that returns JSON.
Register endpoints per (state, county). Each entry says how to normalize the
parcel id and which field holds it.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from .fetch import fetch

ENDPOINTS: dict[tuple[str, str], dict[str, Any]] = {
    ("FL", "lee"): {
        "url": "https://services2.arcgis.com/LvWGAAhHwbCJ2GMP/arcgis/rest/services/Lee_County_Parcels/FeatureServer/0/query",
        "field": "STRAP",
        "normalize": lambda s: re.sub(r"[^0-9A-Za-z]", "", s).upper(),
        "notes": "Lee County parcels layer. STRAP stored without punctuation (01-44-23-C2-02465.0400 -> 014423C2024650400). "
                 "Returns owner and mailing address on the current roll, plus sale history (S_1..S_4 as epoch ms).",
    },
}


def available() -> list[str]:
    return [f"{s} {c.title()}" for s, c in ENDPOINTS]


def lookup(state: str, county: str, parcel_id: str) -> dict[str, Any]:
    key = (state.upper(), (county or "").strip().lower())
    ep = ENDPOINTS.get(key)
    if ep is None:
        raise KeyError(f"No parcel endpoint for {state} {county}; available: {', '.join(available())}")
    value = ep["normalize"](parcel_id)
    where = quote(f"{ep['field']}='{value}'")
    url = f"{ep['url']}?where={where}&outFields=*&returnGeometry=false&f=json"
    f = fetch(url)
    data = json.loads(f.text)
    feats = data.get("features") or []
    return {"query_url": url, "normalized_id": value, "matches": len(feats),
            "attributes": [ft.get("attributes", {}) for ft in feats[:5]], "notes": ep["notes"]}
