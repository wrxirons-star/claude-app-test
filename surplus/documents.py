"""Render Markdown documents from templates, with the compliance gate in front.

The renderer is deterministic. The agent may ask for a document, but it cannot
change the fee, skip the gate, or remove the disclosures.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from .config import Settings
from .rules import (DEFAULT_SALE_TYPE, RuleError, check_agreement, compute_economics,
                    load_rules, parse_date, sale_rules)
from .store import Store

TEMPLATES = Path(__file__).parent / "templates"

DOC_TYPES = ("intro_letter", "phone_script", "agreement", "packet_checklist", "claim_cover_letter")

SALE_KIND = {
    "mortgage_foreclosure": "foreclosure sale",
    "tax_deed": "tax deed sale",
    "tax_sale": "tax sale",
}
CLAIM_REF = {
    "mortgage_foreclosure": "case number",
    "tax_deed": "tax deed number",
    "tax_sale": "cause number",
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def money(x: float | None) -> str:
    return f"${float(x or 0):,.2f}"


def long_date(value: str | date | None) -> str:
    d = parse_date(value)
    return d.strftime("%B %d, %Y").replace(" 0", " ") if d else "[DATE UNKNOWN]"


def _template(name: str) -> str:
    return (TEMPLATES / f"{name}.md").read_text()


def base_context(settings: Settings, case: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    op = settings.operator
    state = case["state"]
    sale_type = case.get("sale_type") or DEFAULT_SALE_TYPE[state]
    r = sale_rules(state, sale_type)
    rules = load_rules(state)
    extra = case.get("extra") or {}
    claimant_name = extra.get("claimant_name") or case.get("owner_name") or "[CLAIMANT NAME]"
    return _Safe(
        case_id=case.get("id"),
        state=state,
        state_name=rules["name"],
        county=case.get("county") or "[COUNTY]",
        sale_type=sale_type,
        sale_type_label=r["label"],
        sale_kind=SALE_KIND.get(sale_type, "sale"),
        claim_reference_label=CLAIM_REF.get(sale_type, "reference number"),
        claim_reference_label_cap=CLAIM_REF.get(sale_type, "reference number").capitalize(),
        case_number=case.get("case_number") or "[CASE NUMBER]",
        parcel_id=case.get("parcel_id") or "[PARCEL]",
        property_address=case.get("property_address") or "[PROPERTY ADDRESS]",
        owner_name=case.get("owner_name") or "[OWNER NAME]",
        holder=case.get("holder") or r.get("holder", "holder of the funds"),
        holder_address=extra.get("holder_address") or "[HOLDER MAILING ADDRESS]",
        court_name=extra.get("court_name") or "[COURT]",
        sale_date_long=long_date(case.get("sale_date")),
        deposit_date_long=long_date(case.get("deposit_date") or case.get("sale_date")),
        surplus_display=money(case.get("surplus_amount")),
        today_long=long_date(today),
        business_name=op.business_name,
        business_address=op.address,
        business_phone=op.phone,
        business_email=op.email,
        signer_name=op.signer_name,
        signer_title=op.signer_title,
        claimant_name=claimant_name,
        claimant_address=extra.get("claimant_address") or "[CLAIMANT MAILING ADDRESS]",
        claimant_phone=extra.get("claimant_phone") or "[PHONE]",
        claimant_email=extra.get("claimant_email") or "[EMAIL]",
        claimant_role=extra.get("claimant_role") or "former owner of record",
        recipient_name=extra.get("recipient_name") or claimant_name,
        recipient_salutation=extra.get("recipient_salutation") or claimant_name,
        recipient_address=extra.get("recipient_address") or extra.get("claimant_address") or "[MAILING ADDRESS]",
    )


def fee_sentences(settings: Settings, case: dict[str, Any], econ) -> dict[str, str]:
    state = case["state"]
    if econ.model == "claim_purchase":
        fee = ("We do not charge a fee. Texas law does not allow a non-attorney to charge a fee for this. "
               "Instead, if you prefer not to wait for the court process, we can purchase your claim and pay you "
               "at least 80 percent of it at signing, in cash, and take on the court process ourselves.")
        para = ("Texas law also lets you hire an attorney to file the petition; the attorney's fee is limited to "
                "the lesser of 25 percent or $1,000. The deadline to petition the court is "
                f"{long_date(econ.claim_deadline)}. We contact people only by mail and email; we will not call you.")
        reply = "Please reply by mail or email; we do not take assignments by phone."
    elif econ.model == "attorney_referral":
        fee = ("We do not charge you a fee. If you wish, we can introduce you to a Texas attorney who files these "
               "petitions; by law the attorney's fee cannot exceed the lesser of 25 percent or $1,000.")
        para = f"The deadline to petition the court is {long_date(econ.claim_deadline)}."
        reply = "Please reply by mail or email."
    else:
        pct = f"{econ.fee_fraction_applied:.0%}"
        fee = (f"If you choose to work with us, our fee is {pct} of what you actually receive, and nothing if you "
               f"receive nothing. {'Florida law caps this fee at 12 percent.' if state == 'FL' else ''}"
               f"{'Georgia law caps this fee at 10 percent.' if state == 'GA' else ''}").strip()
        if state == "GA":
            para = ("Under Georgia law the county pays the funds directly to you, never to us, and we invoice you "
                    "afterward. Unclaimed funds are turned over to the State of Georgia five years after the sale"
                    f"{', on or about ' + long_date(econ.claim_deadline) if econ.claim_deadline else ''}.")
        else:
            para = ("Other lienholders may also have claims, which are paid first. Unclaimed money is eventually "
                    "sent to the State of Florida as unclaimed property"
                    f"{', on or about ' + long_date(econ.unclaimed_transfer_date) if econ.unclaimed_transfer_date else ''}.")
        reply = "You are also welcome to call us at the number above."
    return {"fee_sentence": fee, "state_specific_paragraph": para, "reply_channel_sentence": reply}


def compliance_footer(state: str, econ, check) -> str:
    lines = ["<!-- compliance -->", f"Prepared {date.today().isoformat()} under {state} rules "
             f"({', '.join(econ.citations)}). Draft for attorney review before first use in each county."]
    for w in econ.warnings + check.warnings:
        lines.append(f"Note: {w}")
    return "\n".join(lines)


def render(settings: Settings, store: Store, case_id: int, doc_type: str,
           overrides: dict[str, Any] | None = None, today: date | None = None,
           actor: str = "cli") -> dict[str, Any]:
    """Render one document, save it under documents/<case_id>/, record it, return path + text."""
    if doc_type not in DOC_TYPES:
        raise RuleError(f"Unknown doc_type {doc_type!r}; choose from {', '.join(DOC_TYPES)}")
    today = today or date.today()
    case = store.get_case(case_id)
    if overrides:
        # Overrides live in extra so they persist for the next document.
        case = store.update_case(case_id, actor=actor, extra=overrides)
    op = settings.operator
    state = case["state"]
    sale_type = case.get("sale_type") or DEFAULT_SALE_TYPE[state]
    has_attorney = bool(op.attorney_name)
    econ = compute_economics(state, case.get("surplus_amount") or 0, case.get("sale_date"),
                             case.get("deposit_date"), case.get("notice_date"), sale_type,
                             op.fee_policy, op.tx_purchase_fraction, has_attorney, today,
                             (case.get("extra") or {}).get("listed_date"))
    channel = (case.get("extra") or {}).get("contact_channel")
    fee_for_check = None if econ.model == "claim_purchase" else econ.fee_fraction_applied
    check = check_agreement(state, sale_type, fee_for_check, econ.surplus_amount,
                            (case.get("extra") or {}).get("agreement_date") or today,
                            case.get("deposit_date") or case.get("sale_date"), channel,
                            has_attorney, op.tx_purchase_fraction if econ.model == "claim_purchase" else None,
                            today)
    if doc_type == "agreement" and not check.ok:
        raise RuleError("Agreement blocked by compliance gate: " + " | ".join(check.errors))
    if doc_type == "phone_script" and "phone" not in load_rules(state)["outreach"]["allowed_channels"]:
        raise RuleError(f"{state} does not allow telephone solicitation; use intro_letter instead.")

    ctx = base_context(settings, case, today)
    ctx.update(fee_sentences(settings, case, econ))
    ctx["compliance_footer"] = compliance_footer(state, econ, check)
    ctx["agreement_date_long"] = long_date((case.get("extra") or {}).get("agreement_date") or today)
    ctx["fee_percent"] = f"{econ.fee_fraction_applied:.0%}"
    ctx["owner_percent"] = f"{1 - econ.fee_fraction_applied:.0%}"
    ctx["fee_display"] = money(econ.expected_gross)
    ctx["owner_display"] = money(econ.surplus_amount - econ.expected_gross)
    ctx["purchase_display"] = money(econ.capital_required)
    ctx["purchase_percent"] = f"{max(op.tx_purchase_fraction, 0.8):.0%}"
    ctx["max_recovery_display"] = money(econ.capital_required * 1.25)
    ctx["petition_deadline_long"] = long_date(econ.claim_deadline)
    ctx["deadline_long"] = long_date(econ.claim_deadline)
    ctx["earliest_agreement_long"] = long_date(econ.earliest_agreement_date)
    ctx["expected_gross_display"] = money(econ.expected_gross)
    ctx["model_label"] = econ.model.replace("_", " ")
    ctx["attorney_sentence"] = (f"Filings requiring an attorney will be handled by {op.attorney_name}, {op.attorney_firm}."
                                if op.attorney_name else
                                "If an attorney is required, the Claimant will engage one; the Recovery Agent's fee is unchanged.")
    ctx["channel_warning"] = ("" if "phone" in load_rules(state)["outreach"]["allowed_channels"]
                              else "DO NOT CALL: telephone solicitation is prohibited in this state.")
    contacts = store.contacts(case_id)
    best = next((c for c in contacts if c.get("phone")), None)
    ctx["best_phone"] = best["phone"] if best else "[NONE ON FILE]"
    ctx["best_phone_source"] = best["source_url"] if best else "-"

    template_name = f"agreement_{state.lower()}" if doc_type == "agreement" else doc_type
    if doc_type == "packet_checklist":
        ctx.update(_checklist_context(state, sale_type, case, econ, store))
    if doc_type == "claim_cover_letter":
        ctx["enclosures"] = _enclosures(state, sale_type)
        ctx["cc_line"] = f"cc: {op.business_name}" if econ.model != "claim_purchase" else ""

    text = _template(template_name).format_map(ctx)
    out_dir = settings.documents_dir / str(case_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{doc_type}_{today.isoformat()}.md"
    path.write_text(text)
    store.add_document(case_id, doc_type, str(path), {**check.to_dict(), "economics": econ.to_dict()}, actor=actor)
    return {"path": str(path), "text": text, "compliance": check.to_dict(), "economics": econ.to_dict()}


def _claimant_docs(state: str, sale_type: str) -> list[str]:
    common = ["Government photo ID (front and back)", "Proof of current mailing address",
              "Signed and notarized claim form supplied by the holder"]
    if state == "FL" and sale_type == "mortgage_foreclosure":
        return common + ["Copy of the deed showing ownership when the lis pendens was filed",
                         "Final judgment and certificate of disbursements from the docket",
                         "Signed and notarized recovery agreement / partial assignment (45.033)"]
    if state == "FL":
        return common + ["Deed or property appraiser record showing title at the time of the tax deed sale",
                         "Clerk's Tax Deed Surplus claim form, notarized",
                         "Signed and notarized recovery agreement"]
    if state == "TX":
        return common + ["Deed showing ownership at the time of the tax sale",
                         "Notarized assignment of claim (34.04) with proof of the 80 percent payment",
                         "Certificate of service on all taxing units that were parties"]
    return common + ["Deed or security deed showing the recorded interest at the time of the tax sale",
                     "County excess funds affidavit and indemnification agreement, notarized",
                     "Lien releases or payoff letters for any recorded liens, if available",
                     "Signed recovery assistance agreement dated after the 24-month period"]


def _our_docs(state: str, sale_type: str, econ) -> list[str]:
    docs = ["Claim cover letter", "Packet checklist (this file)", "Compliance record (compliance.json)"]
    if econ.model == "claim_purchase":
        docs += ["Assignment of claim to excess proceeds", "Petition for release of excess proceeds (attorney-reviewed form)",
                 "Proof of payment to assignor"]
    else:
        docs += ["Recovery agreement", "Motion or claim form filled from the county's template"]
    return docs


def _filing_steps(state: str, sale_type: str, econ) -> list[str]:
    if state == "TX":
        return ["File the petition and assignment in the original tax suit (same cause number) with the district clerk.",
                "Serve every taxing unit that was a party; wait the notice period set by the court.",
                "Attend the hearing; obtain the order directing the district clerk to disburse.",
                f"All of this must be complete before {long_date(econ.claim_deadline)}."]
    if state == "GA":
        return ["Confirm the county's current claim form and whether it accepts third-party filings.",
                "Submit the notarized claim package to the tax commissioner (or through the claimant/attorney).",
                "If a competing claim exists, expect an interpleader in superior court; calendar the hearing.",
                "Funds are paid to the claimant; invoice the fee within 10 days of payment."]
    if sale_type == "tax_deed":
        return ["Submit the clerk's tax deed surplus claim form and supporting documents.",
                "Track the 120-day window from the mailed Notice of Surplus for competing lien claims.",
                "Follow up with the clerk's tax deed department every 14 days."]
    return ["File the claim/motion for disbursement in the foreclosure case with the recovery agreement attached.",
            "If within 60 days of the sale and uncontested, request an order without hearing; otherwise set a hearing.",
            "Serve the plaintiff and any subordinate lienholders who filed claims.",
            "After the order, confirm the clerk's disbursement date."]


def _checklist_context(state, sale_type, case, econ, store) -> dict[str, str]:
    notes = store.notes(case["id"])
    return {
        "claimant_documents": "\n".join(f"- [ ] {d}" for d in _claimant_docs(state, sale_type)),
        "our_documents": "\n".join(f"- [ ] {d}" for d in _our_docs(state, sale_type, econ)),
        "filing_steps": "\n".join(f"- [ ] {s}" for s in _filing_steps(state, sale_type, econ)),
        "post_payment_step": ("Send the fee invoice to the claimant (Georgia pays the claimant directly)."
                              if state == "GA" else
                              "Confirm the split was paid per the agreement and send a closing letter."),
        "notes": "\n".join(f"- {n['created_at'][:10]}: {n['text']}" for n in notes) or "-",
    }


def _enclosures(state: str, sale_type: str) -> str:
    return "\n".join(f"- {d}" for d in _claimant_docs(state, sale_type))
