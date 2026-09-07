"""State rules: load the JSON rulebooks, compute deadlines and economics, and
gate documents for compliance.

Everything here is deterministic and unit-tested. The agent calls into it via
tools so the model never has to remember a fee cap.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SUPPORTED_STATES = ("FL", "TX", "GA")

# Default sale type per state when the lead does not say. Florida defaults to the
# tax deed lane: a clerk form rather than a court motion, so no attorney is needed.
DEFAULT_SALE_TYPE = {"FL": "tax_deed", "TX": "tax_sale", "GA": "tax_sale"}


class RuleError(ValueError):
    """Raised when a requested action would violate a state rule."""


def load_rules(state: str) -> dict[str, Any]:
    state = normalize_state(state)
    path = Path(__file__).parent / "statutes" / f"{state.lower()}.json"
    return json.loads(path.read_text())


def normalize_state(state: str) -> str:
    s = (state or "").strip().upper()
    if s not in SUPPORTED_STATES:
        raise RuleError(f"Unsupported state {state!r}; supported: {', '.join(SUPPORTED_STATES)}")
    return s


def sale_rules(state: str, sale_type: str | None = None) -> dict[str, Any]:
    rules = load_rules(state)
    st = sale_type or DEFAULT_SALE_TYPE[normalize_state(state)]
    try:
        return rules["sale_types"][st]
    except KeyError as exc:
        raise RuleError(
            f"Unknown sale_type {st!r} for {state}; options: {', '.join(rules['sale_types'])}"
        ) from exc


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass
class Economics:
    state: str
    sale_type: str
    surplus_amount: float
    model: str  # "contingency_fee" | "claim_purchase" | "attorney_referral" | "blocked"
    fee_cap_fraction: float | None
    fee_fraction_applied: float
    expected_gross: float
    capital_required: float
    earliest_agreement_date: date | None
    claim_deadline: date | None
    days_until_deadline: int | None
    unclaimed_transfer_date: date | None
    warnings: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("earliest_agreement_date", "claim_deadline", "unclaimed_transfer_date"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


def compute_economics(
    state: str,
    surplus_amount: float,
    sale_date: str | date | None = None,
    deposit_date: str | date | None = None,
    notice_date: str | date | None = None,
    sale_type: str | None = None,
    fee_policy: dict[str, float] | None = None,
    tx_purchase_fraction: float = 0.80,
    has_attorney: bool = False,
    today: date | None = None,
) -> Economics:
    """Work out what a lead is worth and when it can be acted on.

    ``deposit_date`` is when the funds hit the registry/escrow (defaults to the
    sale date). ``notice_date`` is the Florida tax-deed Notice of Surplus mail
    date, which starts that 120-day clock.
    """
    state = normalize_state(state)
    sale_type = sale_type or DEFAULT_SALE_TYPE[state]
    r = sale_rules(state, sale_type)
    today = today or date.today()
    sale = parse_date(sale_date)
    deposit = parse_date(deposit_date) or sale
    notice = parse_date(notice_date)
    amount = float(surplus_amount or 0)
    fee_policy = fee_policy or {}
    warnings: list[str] = []

    cap = r.get("fee_cap_fraction")
    earliest: date | None = None
    deadline: date | None = None
    unclaimed: date | None = None
    model = "contingency_fee"
    fee_fraction = 0.0
    gross = 0.0
    capital = 0.0

    if state == "TX" and sale_type == "tax_sale":
        if sale:
            deadline = sale + timedelta(days=r["petition_deadline_days_after_sale"])
        if deposit:
            earliest = deposit + timedelta(days=r["assignment_waiting_days_after_deposit"])
        if has_attorney:
            model = "attorney_referral"
            fee_fraction = min(r["attorney_fee_cap_fraction"], r["attorney_fee_cap_dollars"] / amount) if amount else 0
            gross = min(amount * r["attorney_fee_cap_fraction"], r["attorney_fee_cap_dollars"])
            warnings.append(
                "Attorney fee is capped at the lesser of 25% or $1,000 and belongs to the attorney; "
                "a non-attorney may not share it."
            )
        else:
            model = "claim_purchase"
            frac = max(float(tx_purchase_fraction), r["purchase_min_fraction"])
            if tx_purchase_fraction < r["purchase_min_fraction"]:
                warnings.append("Purchase fraction raised to the statutory minimum of 80%.")
            capital = amount * frac
            recovery = min(amount, capital * r["assignee_recovery_cap_multiple"])
            gross = recovery - capital
            fee_fraction = gross / amount if amount else 0
            warnings.append("Texas: no fee may be charged by a non-attorney; this is a claim purchase, "
                            "contact by mail or email only, pay at signing.")
        cap = None if model == "claim_purchase" else r["attorney_fee_cap_fraction"]
    else:
        policy = fee_policy.get(state)
        fee_fraction = float(policy) if policy is not None else float(cap or 0)
        if cap is not None and fee_fraction > cap + 1e-9:
            raise RuleError(
                f"{state} fee policy {fee_fraction:.0%} exceeds the statutory cap of {cap:.0%} "
                f"({r.get('fee_cap_basis', '')})"
            )
        gross = amount * fee_fraction

        if state == "GA":
            months = r.get("agreement_unenforceable_months", 0)
            if deposit and months:
                earliest = _add_months(deposit, months)
            if sale:
                deadline = sale + timedelta(days=r["turnover_to_state_days_after_sale"])
                unclaimed = deadline
            warnings.append("Georgia: funds must be paid directly to the claimant; invoice after payment.")
        elif state == "FL":
            if sale_type == "mortgage_foreclosure":
                if sale:
                    unclaimed = sale + timedelta(days=r["unclaimed_after_days"])
                    deadline = unclaimed
                    owner_priority_end = sale + timedelta(days=r["owner_priority_days_after_sale"])
                    if today <= owner_priority_end:
                        warnings.append(
                            f"Owner priority window open until {owner_priority_end.isoformat()}: "
                            "an uncontested owner claim can be paid without a hearing."
                        )
            elif sale_type == "tax_deed":
                if sale:
                    unclaimed = sale + timedelta(days=r["unclaimed_after_days"])
                    deadline = unclaimed
                if notice:
                    lien_bar = notice + timedelta(days=r["claim_window_days_from_notice"])
                    deadline = unclaimed or lien_bar
                    if today > lien_bar:
                        warnings.append(f"Lienholder window closed {lien_bar.isoformat()}: if no claims were filed, "
                                        "the titleholder is conclusively presumed entitled (197.582).")
                    else:
                        warnings.append(f"Lienholder window open until {lien_bar.isoformat()}; competing claims "
                                        "may still be filed. The owner should file now regardless.")
                else:
                    warnings.append("No Notice of Surplus date known; ask the clerk for the mail date to place "
                                    "this lead on the 120-day clock.")

    if earliest and earliest > today:
        warnings.append(f"Do not sign an agreement before {earliest.isoformat()}.")
    if deadline and deadline < today:
        warnings.append(f"Deadline {deadline.isoformat()} has passed; funds may have been forfeited or remitted to the state.")
        if state == "FL":
            warnings.append("If remitted to Florida DFS, only a registered claimant's representative may act (20% cap).")

    return Economics(
        state=state,
        sale_type=sale_type,
        surplus_amount=amount,
        model=model,
        fee_cap_fraction=cap,
        fee_fraction_applied=round(fee_fraction, 4),
        expected_gross=round(gross, 2),
        capital_required=round(capital, 2),
        earliest_agreement_date=earliest,
        claim_deadline=deadline,
        days_until_deadline=(deadline - today).days if deadline else None,
        unclaimed_transfer_date=unclaimed,
        warnings=warnings,
        citations=list(r.get("citations", [])),
    )


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


@dataclass
class ComplianceResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_agreement(
    state: str,
    sale_type: str | None,
    fee_fraction: float | None,
    surplus_amount: float,
    agreement_date: str | date | None,
    deposit_date: str | date | None,
    contact_channel: str | None = None,
    has_attorney: bool = False,
    purchase_fraction: float | None = None,
    today: date | None = None,
) -> ComplianceResult:
    """Gate an agreement before it is rendered. Errors block; warnings annotate."""
    state = normalize_state(state)
    sale_type = sale_type or DEFAULT_SALE_TYPE[state]
    r = sale_rules(state, sale_type)
    today = today or date.today()
    agreement = parse_date(agreement_date) or today
    deposit = parse_date(deposit_date)
    res = ComplianceResult(ok=True)

    if state == "TX" and sale_type == "tax_sale":
        if not has_attorney and (fee_fraction or 0) > 0:
            res.errors.append("Texas Tax Code 34.04(i): a non-attorney may not charge a fee to obtain excess proceeds. "
                              "Use the claim purchase (assignment) model instead.")
        if has_attorney and fee_fraction is not None and surplus_amount:
            max_fee = min(surplus_amount * r["attorney_fee_cap_fraction"], r["attorney_fee_cap_dollars"])
            if fee_fraction * surplus_amount > max_fee + 0.005:
                res.errors.append(f"Attorney fee exceeds the cap (lesser of 25% or $1,000 = ${max_fee:,.2f}).")
        if purchase_fraction is not None and purchase_fraction < r["purchase_min_fraction"] - 1e-9:
            res.errors.append("Assignee must pay at least 80% of the claim on the date of assignment (34.04).")
        if deposit:
            earliest = deposit + timedelta(days=r["assignment_waiting_days_after_deposit"])
            if agreement < earliest:
                res.errors.append(f"Assignment may not be taken before {earliest.isoformat()} (36 days after deposit).")
        else:
            res.warnings.append("Deposit date unknown; confirm the 36-day waiting period has run.")
        if contact_channel in ("phone", "in_person"):
            res.errors.append("Assignment resulting from telephone or in-person solicitation is invalid under 34.04.")
    else:
        cap = r.get("fee_cap_fraction")
        if cap is not None and fee_fraction is not None and fee_fraction > cap + 1e-9:
            res.errors.append(f"{state} fee {fee_fraction:.1%} exceeds the {cap:.0%} cap ({r.get('fee_cap_basis', '')}).")
        if state == "GA":
            months = r.get("agreement_unenforceable_months")
            if deposit and months:
                earliest = _add_months(deposit, months)
                if agreement < earliest:
                    res.errors.append(
                        f"Georgia: agreement dated before {earliest.isoformat()} is unenforceable "
                        f"(24 months after escrow)."
                    )
            elif months:
                res.warnings.append("Escrow date unknown; confirm 24 months have passed since the funds were escrowed.")
    if contact_channel == "phone" and "phone" not in load_rules(state).get("outreach", {}).get("allowed_channels", []):
        res.errors.append(f"{state}: telephone solicitation is not an allowed channel.")
    res.ok = not res.errors
    return res


def rules_summary(state: str) -> str:
    """Human-readable summary for the CLI and for the agent's context."""
    rules = load_rules(state)
    lines = [f"{rules['name']} ({rules['state']}) - reviewed {rules['last_reviewed']}", rules["disclaimer"], ""]
    for key, r in rules["sale_types"].items():
        lines.append(f"## {r['label']}  [{key}]")
        lines.append(f"Held by: {r.get('holder', '?')}")
        lines.append(f"Citations: {', '.join(r.get('citations', []))}")
        lines.append(f"Presumed claimant: {r.get('presumed_claimant', '?')}")
        if r.get("fee_cap_fraction") is not None:
            lines.append(f"Fee cap: {r['fee_cap_fraction']:.0%} - {r.get('fee_cap_basis', '')}")
        if r.get("non_attorney_fee_allowed") is False:
            lines.append(f"NON-ATTORNEY FEES PROHIBITED: {r['non_attorney_fee_rule']}")
            lines.append(f"Business model: {r.get('business_model', '')}")
        for k in ("claim_window_rule", "petition_deadline_rule", "agreement_unenforceable_rule",
                  "turnover_rule", "unclaimed_destination", "redemption_note", "note"):
            if r.get(k):
                lines.append(f"{k.replace('_', ' ').capitalize()}: {r[k]}")
        if r.get("assignment_requirements"):
            lines.append("Agreement/assignment requirements:")
            lines += [f"  - {x}" for x in r["assignment_requirements"]]
        if r.get("solicitation_rules"):
            lines.append("Solicitation rules:")
            lines += [f"  - {x}" for x in r["solicitation_rules"]]
        if r.get("where_to_find"):
            lines.append("Where to find lists:")
            lines += [f"  - {x}" for x in r["where_to_find"]]
        lines.append("")
    up = rules.get("unclaimed_property")
    if up:
        lines.append(f"## {up['label']}")
        lines.append(f"Citations: {', '.join(up.get('citations', []))}")
        for k in ("who_may_represent", "fee_cap_basis", "note"):
            if up.get(k):
                lines.append(up[k])
        lines.append("")
    out = rules.get("outreach", {})
    lines.append(f"Outreach channels allowed: {', '.join(out.get('allowed_channels', []))}")
    lines += [f"  - {x}" for x in out.get("notes", [])]
    return "\n".join(lines)
