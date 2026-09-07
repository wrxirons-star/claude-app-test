"""Deterministic lead scoring. Higher is better. Explains itself.

Score = expected gross to us, discounted for time pressure, ownership
complexity, and lien risk. The number is for ranking only.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .rules import RuleError, compute_economics


ENTITY_MARKERS = (" LLC", " INC", " CORP", " TRUST", " BANK", " ASSOC", " LP", " LTD", " HOA",
                  " MORTGAGE", " CAPITAL", " HOLDINGS", " PROPERTIES", " PARTNERS", " FUND", " N.A.")


def classify_owner(name: str | None) -> str:
    if not name:
        return "unknown"
    n = f" {name.upper().strip()} "
    if any(m + " " in n or n.endswith(m + " ") for m in ENTITY_MARKERS):
        return "entity"
    if "ESTATE OF" in n or "DECEASED" in n or " EST " in n:
        return "estate"
    if " AND " in n or " & " in n or "/" in n:
        return "multiple"
    return "individual"


def score_case(case: dict[str, Any], fee_policy: dict[str, float] | None = None,
               tx_purchase_fraction: float = 0.80, has_attorney: bool = False,
               today: date | None = None) -> tuple[float, dict[str, Any]]:
    today = today or date.today()
    reasons: dict[str, Any] = {}
    amount = float(case.get("surplus_amount") or 0)
    if amount <= 0:
        return 0.0, {"reason": "no surplus amount"}
    try:
        econ = compute_economics(
            case["state"], amount, case.get("sale_date"), case.get("deposit_date"),
            case.get("notice_date"), case.get("sale_type"), fee_policy, tx_purchase_fraction,
            has_attorney, today,
        )
    except RuleError as exc:
        return 0.0, {"reason": str(exc)}
    score = econ.expected_gross
    reasons["expected_gross"] = econ.expected_gross
    reasons["model"] = econ.model

    # Time pressure
    if econ.days_until_deadline is not None:
        d = econ.days_until_deadline
        if d < 0:
            score *= 0.05; reasons["deadline"] = "passed"
        elif d < 30:
            score *= 0.5; reasons["deadline"] = f"{d} days - very tight"
        elif d < 90:
            score *= 0.8; reasons["deadline"] = f"{d} days - tight"
        else:
            reasons["deadline"] = f"{d} days"
    # Not yet actionable
    if econ.earliest_agreement_date and econ.earliest_agreement_date > today:
        wait = (econ.earliest_agreement_date - today).days
        factor = 0.9 if wait < 60 else (0.6 if wait < 365 else 0.3)
        score *= factor
        reasons["waiting"] = f"agreement not allowed for {wait} days"
    # Ownership complexity
    otype = case.get("owner_type") or classify_owner(case.get("owner_name"))
    reasons["owner_type"] = otype
    score *= {"individual": 1.0, "multiple": 0.8, "estate": 0.6, "entity": 0.5, "unknown": 0.7}.get(otype, 0.7)
    # Lien risk: mortgage foreclosure surplus is more often eaten by junior liens
    if (case.get("sale_type") or "") == "mortgage_foreclosure":
        score *= 0.85; reasons["lien_risk"] = "mortgage foreclosure: junior liens paid first"
    # Capital tie-up for Texas purchases
    if econ.capital_required:
        score *= 0.9; reasons["capital_required"] = econ.capital_required
    # Small-dollar floor: below a few hundred dollars of gross it is not worth the postage
    if econ.expected_gross < 300:
        score *= 0.3; reasons["small"] = "gross under $300"
    return round(score, 2), reasons
