"""Lee County, Florida: Tax Deed Surplus Weekly Report (clerk PDF).

Columns as printed:
  Tax Deed Number | Sale Date | Balance | Balance Date | Property Address |
  Parcel ID | Owner Name | Lienholder Claim Period Expires

Property Address and the Expires date are sometimes blank. "Lienholder Claim
Period Expires" is 120 days after the clerk mailed the Notice of Surplus
(Fla. Stat. 197.582), so notice_date = expires - 120 days.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

ROW = re.compile(
    r"^(?P<tdn>\d{10})\s+(?P<sale>\d{1,2}/\d{1,2}/\d{4})\s+(?P<bal>[\d,]+\.\d{2})\s+"
    r"(?P<bdate>\d{1,2}/\d{1,2}/\d{4})\s+(?P<rest>.*)$"
)
PARCEL = re.compile(r"(?P<parcel>\d{2}-\d{2}-\d{2}-[A-Z0-9]{2}-[0-9A-Z]{5}\.[0-9A-Z]{3,4}|\b\d{17}\b)")
SKIP = re.compile(r"^(--- page \d+ ---|Tax Deed Number Sale Date|Page \d+\b|\s*$)", re.I)
TRAILING_DATE = re.compile(r"\s*(?P<exp>\d{1,2}/\d{1,2}/\d{4})\s*$")
HOLDER = "Clerk of the Circuit Court, Tax Deed Department"
CLAIM_WINDOW_DAYS = 120


def _iso(mdy: str) -> str:
    return datetime.strptime(mdy, "%m/%d/%Y").date().isoformat()


def _finish(row: dict, tail: str) -> None:
    """Split the text after the parcel into owner and optional expiry date."""
    expires = None
    tm = TRAILING_DATE.search(tail)
    if tm:
        expires = _iso(tm.group("exp"))
        tail = tail[: tm.start()].strip()
    owner = re.sub(r"\s+,", ",", tail).strip(" ,") or None
    row["owner_name"] = owner
    row["notice_date"] = ((date.fromisoformat(expires) - timedelta(days=CLAIM_WINDOW_DAYS)).isoformat()
                          if expires else None)
    row["extra"]["lienholder_claim_expires"] = expires


def parse(text: str, source_url: str) -> list[dict]:
    rows: list[dict] = []
    tails: list[str] = []  # owner text per row, may accumulate across wrapped lines
    for raw in text.splitlines():
        line = " ".join(raw.split())
        m = ROW.match(line)
        if not m:
            # A wrapped owner line belongs to the previous row: the PDF breaks
            # long owner lists after the parcel or mid-list.
            if rows and line and not SKIP.match(line):
                tails[-1] = (tails[-1] + " " + line).strip()
                _finish(rows[-1], tails[-1])
            continue
        rest = m.group("rest")
        pm = PARCEL.search(rest)
        if pm:
            address = rest[: pm.start()].strip() or None
            parcel = pm.group("parcel")
            tail = rest[pm.end():].strip()
        else:
            address, parcel, tail = None, None, rest
        row = {
            "state": "FL",
            "county": "Lee",
            "sale_type": "tax_deed",
            "case_number": m.group("tdn"),
            "sale_date": _iso(m.group("sale")),
            "surplus_amount": float(m.group("bal").replace(",", "")),
            "property_address": address,
            "parcel_id": parcel,
            "holder": HOLDER,
            "source_url": source_url,
            "extra": {
                "balance_date": _iso(m.group("bdate")),
                "listed_date": date.today().isoformat(),
            },
        }
        _finish(row, tail)
        rows.append(row)
        tails.append(tail)
    return rows
