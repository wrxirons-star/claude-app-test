"""Tools the agent can call. Each is a thin, validated wrapper over the store,
rules, documents, and packet modules so the model can act without being able to
bypass the compliance logic.
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import beta_tool

from .config import Settings
from .documents import DOC_TYPES, render
from .fetch import fetch
from .packet import build_packet
from .rules import RuleError, check_agreement, compute_economics, load_rules, normalize_state
from .scoring import classify_owner, score_case
from .store import CASE_STATUSES, Store

ACTOR = "agent"


def _j(obj: Any) -> str:
    return json.dumps(obj, indent=1, default=str)


def make_tools(settings: Settings, store: Store) -> list:
    op = settings.operator

    @beta_tool
    def get_state_rules(state: str) -> str:
        """Return the full rulebook for a state as JSON. Call this before recommending a fee,
        a contact channel, a deadline, or an agreement in that state.

        Args:
            state: Two-letter state code: FL, TX, or GA.
        """
        return _j(load_rules(state))

    @beta_tool
    def economics(state: str, surplus_amount: float, sale_date: str | None = None,
                  deposit_date: str | None = None, notice_date: str | None = None,
                  sale_type: str | None = None) -> str:
        """Compute what a lead is worth and when it can be acted on: fee cap, expected gross,
        capital required (Texas purchases), earliest lawful agreement date, claim deadline,
        and warnings. Call this for every lead before recommending it.

        Args:
            state: FL, TX, or GA.
            surplus_amount: Dollar amount the holder is holding.
            sale_date: ISO date (YYYY-MM-DD) of the sale, if known.
            deposit_date: ISO date the funds hit the registry or escrow, if different from the sale date.
            notice_date: Florida tax deed only: date the clerk mailed the Notice of Surplus.
            sale_type: mortgage_foreclosure, tax_deed, or tax_sale. Defaults per state.
        """
        try:
            e = compute_economics(state, surplus_amount, sale_date, deposit_date, notice_date, sale_type,
                                  op.fee_policy, op.tx_purchase_fraction, bool(op.attorney_name))
        except RuleError as exc:
            return f"Error: {exc}"
        return _j(e.to_dict())

    @beta_tool
    def compliance_check(state: str, fee_fraction: float | None, surplus_amount: float,
                         agreement_date: str | None = None, deposit_date: str | None = None,
                         contact_channel: str | None = None, sale_type: str | None = None) -> str:
        """Check whether a proposed agreement would be lawful before promising anything to an owner.
        Returns ok/errors/warnings.

        Args:
            state: FL, TX, or GA.
            fee_fraction: Proposed fee as a fraction (0.12 = 12%). Use null for a Texas claim purchase.
            surplus_amount: Dollar amount held.
            agreement_date: ISO date the agreement would be signed (default today).
            deposit_date: ISO date funds were deposited/escrowed (default: sale date if known).
            contact_channel: How the owner was first solicited: mail, email, phone, or in_person.
            sale_type: mortgage_foreclosure, tax_deed, or tax_sale.
        """
        try:
            res = check_agreement(state, sale_type, fee_fraction, surplus_amount, agreement_date, deposit_date,
                                  contact_channel, bool(op.attorney_name),
                                  op.tx_purchase_fraction if fee_fraction is None else None)
        except RuleError as exc:
            return f"Error: {exc}"
        return _j(res.to_dict())

    @beta_tool
    def save_lead(state: str, county: str, case_number: str, owner_name: str, surplus_amount: float,
                  source_url: str, sale_type: str | None = None, property_address: str | None = None,
                  parcel_id: str | None = None, sale_date: str | None = None, deposit_date: str | None = None,
                  notice_date: str | None = None, sale_price: float | None = None,
                  judgment_amount: float | None = None, holder: str | None = None,
                  notes: str | None = None) -> str:
        """Save or update a surplus funds lead found in a public record. Only pass values you actually
        read from the source; omit anything unknown. Returns the case id and score.

        Args:
            state: FL, TX, or GA.
            county: County name without the word County.
            case_number: Case, cause, or tax deed number exactly as printed by the holder.
            owner_name: Former owner name exactly as printed.
            surplus_amount: Dollar amount held.
            source_url: URL of the page or file where this row appears.
            sale_type: mortgage_foreclosure, tax_deed, or tax_sale.
            property_address: Street address if shown.
            parcel_id: Parcel or account number if shown.
            sale_date: ISO date of the sale.
            deposit_date: ISO date the funds were deposited, if shown.
            notice_date: Florida tax deed Notice of Surplus mail date, if shown.
            sale_price: Winning bid if shown.
            judgment_amount: Judgment or tax debt if shown.
            holder: Office holding the funds if the list says.
            notes: Anything else useful from the row.
        """
        state = normalize_state(state)
        fields = dict(state=state, county=county, case_number=case_number, owner_name=owner_name,
                      owner_type=classify_owner(owner_name), surplus_amount=surplus_amount,
                      source_url=source_url, sale_type=sale_type, property_address=property_address,
                      parcel_id=parcel_id, sale_date=sale_date, deposit_date=deposit_date,
                      notice_date=notice_date, sale_price=sale_price, judgment_amount=judgment_amount,
                      holder=holder)
        case_id, created = store.upsert_case(actor=ACTOR, **fields)
        case = store.get_case(case_id)
        score, reasons = score_case(case, op.fee_policy, op.tx_purchase_fraction, bool(op.attorney_name))
        store.update_case(case_id, actor=ACTOR, score=score,
                          status="qualified" if score >= 300 and case["status"] == "new" else case["status"])
        if notes:
            store.add_note(case_id, notes, kind="lead", source_url=source_url, actor=ACTOR)
        return _j({"case_id": case_id, "created": created, "score": score, "reasons": reasons})

    @beta_tool
    def list_cases(state: str | None = None, status: str | None = None, county: str | None = None,
                   min_amount: float | None = None, limit: int = 25) -> str:
        """List saved cases, best score first.

        Args:
            state: Filter by FL, TX, or GA.
            status: Filter by status: new, qualified, locating, contacting, engaged, signed, filed, paid, closed.
            county: Filter by county.
            min_amount: Only cases with at least this surplus amount.
            limit: Maximum rows (default 25).
        """
        rows = store.list_cases(state, status, county, min_amount, limit)
        slim = [{k: r.get(k) for k in ("id", "state", "county", "case_number", "owner_name", "surplus_amount",
                                        "sale_date", "sale_type", "status", "score", "next_action")} for r in rows]
        return _j(slim)

    @beta_tool
    def get_case(case_id: int) -> str:
        """Return one case with its contacts, notes, and documents.

        Args:
            case_id: The case id.
        """
        try:
            case = store.get_case(case_id)
        except KeyError as exc:
            return f"Error: {exc}"
        return _j({"case": case, "contacts": store.contacts(case_id), "notes": store.notes(case_id),
                   "documents": store.documents(case_id)})

    @beta_tool
    def update_case(case_id: int, status: str | None = None, next_action: str | None = None,
                    next_action_date: str | None = None, sale_date: str | None = None,
                    deposit_date: str | None = None, notice_date: str | None = None,
                    surplus_amount: float | None = None, property_address: str | None = None,
                    holder: str | None = None, extra: dict[str, Any] | None = None) -> str:
        """Update fields on a case. Use extra for claimant details used in documents:
        claimant_name, claimant_address, claimant_phone, claimant_email, recipient_name,
        recipient_address, holder_address, court_name, contact_channel, agreement_date.

        Args:
            case_id: The case id.
            status: new, qualified, locating, contacting, engaged, signed, filed, paid, or closed.
            next_action: One sentence describing the next thing the operator should do.
            next_action_date: ISO date for that action.
            sale_date: ISO date.
            deposit_date: ISO date.
            notice_date: ISO date.
            surplus_amount: Corrected amount if the holder's records changed.
            property_address: Corrected address.
            holder: Office holding the funds.
            extra: Dict of extra fields merged into the case's extra JSON.
        """
        if status and status not in CASE_STATUSES:
            return f"Error: invalid status {status!r}; choose from {', '.join(CASE_STATUSES)}"
        fields = {k: v for k, v in dict(status=status, next_action=next_action, next_action_date=next_action_date,
                                        sale_date=sale_date, deposit_date=deposit_date, notice_date=notice_date,
                                        surplus_amount=surplus_amount, property_address=property_address,
                                        holder=holder, extra=extra).items() if v is not None}
        try:
            case = store.update_case(case_id, actor=ACTOR, **fields)
        except (KeyError, ValueError) as exc:
            return f"Error: {exc}"
        score, _ = score_case(case, op.fee_policy, op.tx_purchase_fraction, bool(op.attorney_name))
        store.update_case(case_id, actor=ACTOR, score=score)
        return _j(store.get_case(case_id))

    @beta_tool
    def add_note(case_id: int, text: str, source_url: str | None = None, kind: str = "research") -> str:
        """Attach a dated note to a case: a finding, a risk, a county requirement, or a rule change.

        Args:
            case_id: The case id.
            text: The note.
            source_url: Where the information came from, if any.
            kind: research, risk, contact_attempt, county_requirement, or rule_change.
        """
        try:
            nid = store.add_note(case_id, text, kind=kind, source_url=source_url, actor=ACTOR)
        except Exception as exc:  # sqlite constraint on unknown case
            return f"Error: {exc}"
        return _j({"note_id": nid})

    @beta_tool
    def add_contact(case_id: int, name: str, relationship: str, confidence: float, source_url: str,
                    address: str | None = None, phone: str | None = None, email: str | None = None,
                    notes: str | None = None) -> str:
        """Save a located person for a case: the owner, an heir, a personal representative, or an
        attorney of record. Only save data you actually read at source_url.

        Args:
            case_id: The case id.
            name: Full name as found.
            relationship: owner, co_owner, heir, personal_representative, attorney, registered_agent, or other.
            confidence: 0 to 1, how sure you are this is the right person and the data is current.
            source_url: URL where the data was found.
            address: Mailing address if found.
            phone: Phone if found (do not save for Texas cases; calls are not permitted).
            email: Email if found.
            notes: Why you think this is the right person; dates seen on the record.
        """
        case = store.get_case(case_id)
        if case["state"] == "TX" and phone:
            phone = None
            notes = (notes or "") + " [phone withheld: Texas prohibits telephone solicitation]"
        cid = store.add_contact(case_id, actor=ACTOR, name=name, relationship=relationship, address=address,
                                phone=phone, email=email, confidence=max(0.0, min(1.0, confidence)),
                                source_url=source_url, notes=notes)
        return _j({"contact_id": cid})

    @beta_tool
    def add_source(state: str, url: str, kind: str, county: str | None = None, notes: str | None = None) -> str:
        """Remember a public-record source so future runs start there: a surplus list, a search portal,
        a docket, a property appraiser, a claim form page.

        Args:
            state: FL, TX, or GA.
            url: The URL.
            kind: list, portal, docket, appraiser, recorder, forms, or other.
            county: County name, or omit for statewide sources.
            notes: What it is and how to use it (login needed, PDF vs table, update cadence).
        """
        sid = store.add_source(state, url, kind, county, notes, actor=ACTOR)
        return _j({"source_id": sid})

    @beta_tool
    def list_sources(state: str, county: str | None = None) -> str:
        """List known public-record sources for a state or county.

        Args:
            state: FL, TX, or GA.
            county: Optional county filter.
        """
        return _j(store.sources(state, county))

    @beta_tool
    def render_document(case_id: int, doc_type: str, overrides: dict[str, Any] | None = None) -> str:
        """Render a compliance-gated Markdown document for a case and save it. Returns the path and text.
        The fee and disclosures come from the rulebook and operator profile and cannot be overridden.

        Args:
            case_id: The case id.
            doc_type: intro_letter, phone_script, agreement, packet_checklist, or claim_cover_letter.
            overrides: Claimant/recipient details to merge into the case's extra JSON first, such as
                claimant_name, claimant_address, recipient_name, recipient_address, holder_address, court_name.
        """
        if doc_type not in DOC_TYPES:
            return f"Error: doc_type must be one of {', '.join(DOC_TYPES)}"
        try:
            res = render(settings, store, case_id, doc_type, overrides, actor=ACTOR)
        except (RuleError, KeyError) as exc:
            return f"Error: {exc}"
        return _j({"path": res["path"], "compliance": res["compliance"], "text": res["text"]})

    @beta_tool
    def build_claim_packet(case_id: int) -> str:
        """Assemble the claim packet folder (checklist, cover letter, agreement, compliance record,
        case summary) for a case. Returns the folder path and any documents the compliance gate blocked.

        Args:
            case_id: The case id.
        """
        try:
            return _j(build_packet(settings, store, case_id, actor=ACTOR))
        except KeyError as exc:
            return f"Error: {exc}"

    @beta_tool
    def fetch_url(url: str, offset: int = 0, max_chars: int = 40000) -> str:
        """Download a URL from the operator's own computer and return its text. Use this for county
        PDF reports, spreadsheets, 'showpublisheddocument' links, RealTDM/RealForeclose portals, and
        any page web_fetch reports as not accessible. PDFs are converted to text page by page; HTML
        returns the visible text plus a list of links so you can find the report file on a landing
        page. Long documents are paged: call again with a larger offset to read more.

        Args:
            url: The full URL to download.
            offset: Character offset to start from (for paging through long documents).
            max_chars: Maximum characters to return in this call (default 40000).
        """
        try:
            f = fetch(url, save_dir=settings.home / "downloads")
        except Exception as exc:
            return f"Error fetching {url}: {exc}"
        body = f.text[offset: offset + max_chars]
        more = len(f.text) - (offset + max_chars)
        out = [f"URL: {f.final_url}", f"Type: {f.kind} ({f.content_type})", f"Saved: {f.saved_to}",
               f"Characters: {len(f.text)} (showing {offset}-{offset + len(body)})", "", body]
        if more > 0:
            out.append(f"\n[{more} more characters; call again with offset={offset + max_chars}]")
        if f.links:
            interesting = [(l, u) for l, u in f.links
                           if any(k in (l + u).lower() for k in ("surplus", "report", "document", "pdf", "excess",
                                                                   "unclaimed", "tax deed", "realtdm", "list", "claim", "form"))]
            shown = interesting[:60] or f.links[:60]
            out.append("\nLinks:")
            out += [f"- {l or '(no label)'}: {u}" for l, u in shown]
        return "\n".join(out)

    @beta_tool
    def import_report(county: str, source_url: str, min_amount: float = 0, state: str = "FL") -> str:
        """Fetch a county's surplus report and import every row with the county-specific parser in one
        call. Use this FIRST for any county that has a parser (currently: FL Lee) instead of reading the
        report and calling save_lead row by row. Returns counts and the case ids created or updated.

        Args:
            county: County name, e.g. Lee.
            source_url: URL of the report file (PDF) or landing page link to it.
            min_amount: Skip rows below this surplus amount.
            state: Two-letter state code (default FL).
        """
        from .cli import import_report as _import_report
        try:
            res = _import_report(settings, store, state, county, source_url, min_amount)
        except RuleError as exc:
            return f"Error: {exc}"
        except Exception as exc:
            return f"Error fetching or parsing {source_url}: {exc}"
        for cid in res["case_ids"]:
            case = store.get_case(cid)
            score, _ = score_case(case, op.fee_policy, op.tx_purchase_fraction, bool(op.attorney_name))
            store.update_case(cid, actor=ACTOR, score=score,
                              status="qualified" if score >= 300 and case["status"] == "new" else case["status"])
        return _j(res)

    return [get_state_rules, economics, compliance_check, save_lead, list_cases, get_case, update_case,
            add_note, add_contact, add_source, list_sources, render_document, build_claim_packet, fetch_url,
            import_report]


SERVER_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": 30},
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 30, "max_content_tokens": 60000},
]
