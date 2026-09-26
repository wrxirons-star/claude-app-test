"""Prior-year tax roll index (Florida NAL files).

Florida's Department of Revenue publishes every county's real property roll
each year as a comma-delimited "NAL" (Name-Address-Legal) file. The roll for
the year BEFORE a tax deed sale carries the former owner's name and the
mailing address the tax bills went to. That is the best fully automated
skip-trace source for a Florida tax deed case: no forms, no bot protection.

Load once per county-year with `surplus roll load`, then look parcels up.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Iterator

from .fetch import fetch
from .store import Store

# Column names as published by DOR (case-insensitive; a few counties vary).
COLS = {
    "parcel": ("PARCEL_ID", "PARCEL", "STRAP", "PARCELID"),
    "owner": ("OWN_NAME", "OWNER", "OWNER_NAME"),
    "addr1": ("OWN_ADDR1", "OWN_ADDR", "OWNER_ADDR1"),
    "addr2": ("OWN_ADDR2", "OWNER_ADDR2"),
    "city": ("OWN_CITY", "OWNER_CITY"),
    "state": ("OWN_STATE", "OWNER_STATE"),
    "zip": ("OWN_ZIPCD", "OWN_ZIP", "OWNER_ZIP"),
    "phy_addr": ("PHY_ADDR1", "PHY_ADDR", "SITE_ADDR"),
    "phy_city": ("PHY_CITY",),
    "sale_prc1": ("SALE_PRC1",),
    "sale_yr1": ("SALE_YR1",),
    "sale_mo1": ("SALE_MO1",),
}


def normalize_parcel(p: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", p or "").upper()


def _open_csv_bytes(data: bytes, name_hint: str = "") -> Iterator[tuple[str, io.TextIOBase]]:
    """Yield (name, text stream) for each CSV inside a zip, or the file itself."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.filename.lower().endswith((".csv", ".txt")):
                    yield info.filename, io.TextIOWrapper(zf.open(info), encoding="latin-1", newline="")
    else:
        yield name_hint or "roll.csv", io.TextIOWrapper(io.BytesIO(data), encoding="latin-1", newline="")


def _pick(header: list[str]) -> dict[str, int]:
    up = [h.strip().upper() for h in header]
    idx: dict[str, int] = {}
    for key, names in COLS.items():
        for n in names:
            if n in up:
                idx[key] = up.index(n)
                break
    return idx


def load_roll(store: Store, source: str, state: str, county: str, year: int,
              save_dir: Path | None = None, batch: int = 5000) -> dict[str, Any]:
    """Download or open a NAL file (zip or csv) and index it. Returns counts."""
    local = Path(source[7:] if source.startswith("file://") else source).expanduser()
    if local.exists():
        raw, saved = local.read_bytes(), str(local)
    else:
        f = fetch(source, save_dir=save_dir, max_bytes=4 * 1024 ** 3, timeout=900)
        if f.kind == "html":
            raise ValueError("That URL returned a web page, not a roll file. Pass the direct .zip or .csv link "
                             "or a downloaded file path.")
        raw, saved = Path(f.saved_to).read_bytes(), f.saved_to
    total = 0
    files = 0
    store.conn.execute("DELETE FROM roll WHERE state=? AND county=? AND year=?", (state.upper(), county, year))
    for name, stream in _open_csv_bytes(raw, source):
        reader = csv.reader(stream)
        header = next(reader, None)
        if not header:
            continue
        idx = _pick(header)
        if "parcel" not in idx or "owner" not in idx:
            continue
        files += 1
        rows: list[tuple] = []

        def get(r: list[str], k: str) -> str | None:
            i = idx.get(k)
            return (r[i].strip() if i is not None and i < len(r) else None) or None

        for r in reader:
            parcel = get(r, "parcel")
            if not parcel:
                continue
            rows.append((state.upper(), county, year, normalize_parcel(parcel), parcel, get(r, "owner"),
                         get(r, "addr1"), get(r, "addr2"), get(r, "city"), get(r, "state"), get(r, "zip"),
                         get(r, "phy_addr"), get(r, "phy_city"), get(r, "sale_prc1"), get(r, "sale_yr1"),
                         get(r, "sale_mo1")))
            if len(rows) >= batch:
                store.insert_roll_rows(rows); total += len(rows); rows = []
        if rows:
            store.insert_roll_rows(rows); total += len(rows)
    store.conn.commit()
    if files == 0:
        raise ValueError("No CSV with PARCEL_ID and OWN_NAME columns found in that file.")
    store.audit("cli", "roll_load", None, f"{state} {county} {year}: {total} rows from {source}")
    return {"state": state.upper(), "county": county, "year": year, "rows": total, "files": files,
            "saved_file": saved}
