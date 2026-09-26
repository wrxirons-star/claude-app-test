"""County report parsers. Each parser turns the text of a county's surplus
report into lead dicts ready for Store.upsert_case. Register new counties in
PARSERS keyed by (state, county lowercased)."""
from __future__ import annotations

from typing import Callable

from . import lee_fl

Parser = Callable[[str, str], list[dict]]

PARSERS: dict[tuple[str, str], Parser] = {
    ("FL", "lee"): lee_fl.parse,
}


def get_parser(state: str, county: str) -> Parser | None:
    return PARSERS.get((state.upper(), (county or "").strip().lower()))


def available() -> list[str]:
    return [f"{s} {c.title()}" for s, c in PARSERS]
