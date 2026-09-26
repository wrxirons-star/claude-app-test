"""Command-line entry point.

    surplus init                      create the data dir and config.json
    surplus rules FL                  print the rulebook summary
    surplus fee TX 25000 --sale-date 2025-06-01
    surplus import leads.csv          bulk-load a county list you downloaded
    surplus score                     re-score every open case
    surplus cases [--state FL] [--status qualified]
    surplus show 12
    surplus note 12 "spoke to clerk; amount confirmed"
    surplus set 12 status=contacting next_action="mail letter"
    surplus draft 12 intro_letter     render a document (no API call)
    surplus packet 12                 build the packet folder (no API call)
    surplus find FL --county Sumter   agent: discover and save leads
    surplus locate 12                 agent: skip-trace the owner
    surplus prepare 12                agent: research county forms, then build packet
    surplus run "free-form task"      agent: anything else
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from .config import load_settings, write_default_config
from .documents import DOC_TYPES, render
from .packet import build_packet
from .rules import RuleError, compute_economics, rules_summary
from .scoring import classify_owner, score_case
from .store import CASE_STATUSES, Store

CSV_ALIASES = {
    "case": "case_number", "case no": "case_number", "case number": "case_number", "cause": "case_number",
    "cause no": "case_number", "cause number": "case_number", "tax deed": "case_number", "td": "case_number",
    "tax deed no": "case_number", "td no": "case_number", "file no": "case_number", "case id": "case_number",
    "name": "owner_name", "owner": "owner_name", "defendant": "owner_name", "former owner": "owner_name",
    "amount": "surplus_amount", "surplus": "surplus_amount", "excess": "surplus_amount",
    "excess proceeds": "surplus_amount", "balance": "surplus_amount", "funds": "surplus_amount",
    "address": "property_address", "property": "property_address", "situs": "property_address",
    "parcel": "parcel_id", "parcel id": "parcel_id", "account": "parcel_id", "apn": "parcel_id",
    "sale": "sale_date", "sale date": "sale_date", "date of sale": "sale_date", "date": "sale_date",
    "surplus amount": "surplus_amount", "excess amount": "surplus_amount", "amount held": "surplus_amount",
    "property address": "property_address", "owner name": "owner_name", "parcel no": "parcel_id",
    "url": "source_url", "source": "source_url",
}


def _money(s: str) -> float | None:
    s = (s or "").replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cmd_init(args, settings, store):
    path = write_default_config(settings, overwrite=args.force)
    seeded = sum(store.seed_sources(s) for s in settings.operator.active_states)
    print(f"Data dir: {settings.home}\nDatabase: {settings.db_path}\nConfig:   {path}")
    print(f"Seeded {seeded} known public-record sources for {', '.join(settings.operator.active_states)}.")
    print("Edit config.json with your business details before drafting documents.")


def cmd_rules(args, settings, store):
    print(rules_summary(args.state))


def cmd_fee(args, settings, store):
    op = settings.operator
    e = compute_economics(args.state, args.amount, args.sale_date, args.deposit_date, args.notice_date,
                          args.sale_type, op.fee_policy, op.tx_purchase_fraction, bool(op.attorney_name))
    print(json.dumps(e.to_dict(), indent=2))


def cmd_import(args, settings, store):
    _require_active(settings, [args.state.upper()])
    path = Path(args.file)
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for raw in reader:
            row = {}
            for k, v in raw.items():
                if k is None:
                    continue
                norm = k.strip().lower().replace("#", " no").replace(".", "").replace("_", " ")
                norm = " ".join(norm.split())
                key = CSV_ALIASES.get(norm, norm.replace(" ", "_"))
                row[key] = (v or "").strip()
            if not row.get("case_number") and not row.get("owner_name"):
                continue
            rows.append({
                "state": args.state, "county": args.county, "sale_type": args.sale_type,
                "case_number": row.get("case_number") or None,
                "owner_name": row.get("owner_name") or None,
                "owner_type": classify_owner(row.get("owner_name")),
                "surplus_amount": _money(row.get("surplus_amount", "")),
                "property_address": row.get("property_address") or None,
                "parcel_id": row.get("parcel_id") or None,
                "sale_date": row.get("sale_date") or None,
                "source_url": row.get("source_url") or args.source_url,
                "extra": {"import_file": str(path)},
            })
    created, updated = store.import_rows(rows)
    print(f"Imported {created} new, {updated} updated from {path}")
    _rescore(settings, store)


def _rescore(settings, store):
    op = settings.operator
    n = 0
    for case in store.list_cases(limit=100000, order="id"):
        if case["status"] in ("paid", "closed"):
            continue
        score, _ = score_case(case, op.fee_policy, op.tx_purchase_fraction, bool(op.attorney_name))
        fields = {"score": score}
        if case["status"] == "new" and score >= 300:
            fields["status"] = "qualified"
        store.update_case(case["id"], **fields)
        n += 1
    print(f"Scored {n} cases")


def cmd_score(args, settings, store):
    _rescore(settings, store)


def cmd_cases(args, settings, store):
    rows = store.list_cases(args.state, args.status, args.county, args.min_amount, args.limit)
    if not rows:
        print("No cases.")
        return
    print(f"{'id':>4} {'st':2} {'county':<12} {'case':<18} {'owner':<28} {'amount':>12} {'sale':<10} {'status':<11} {'score':>8}")
    for r in rows:
        print(f"{r['id']:>4} {r['state']:2} {(r.get('county') or '')[:12]:<12} {(r.get('case_number') or '')[:18]:<18} "
              f"{(r.get('owner_name') or '')[:28]:<28} {float(r.get('surplus_amount') or 0):>12,.2f} "
              f"{(r.get('sale_date') or '')[:10]:<10} {r['status']:<11} {float(r.get('score') or 0):>8.0f}")


def cmd_sources(args, settings, store):
    rows = store.sources(args.state or settings.operator.active_states[0], args.county)
    for r in rows:
        print(f"{r['state']} {(r.get('county') or '-'):<10} {r['kind']:<8} {r['url']}\n    {r.get('notes') or ''}")
    if not rows:
        print("No sources. Run `surplus init` to seed.")


def cmd_show(args, settings, store):
    case = store.get_case(args.case_id)
    print(json.dumps({"case": case, "contacts": store.contacts(args.case_id), "notes": store.notes(args.case_id),
                      "documents": store.documents(args.case_id)}, indent=2, default=str))


def cmd_note(args, settings, store):
    nid = store.add_note(args.case_id, args.text, kind=args.kind, source_url=args.source_url)
    print(f"note {nid} added to case {args.case_id}")


def cmd_set(args, settings, store):
    fields = {}
    extra = {}
    for pair in args.pairs:
        if "=" not in pair:
            raise SystemExit(f"expected key=value, got {pair!r}")
        k, v = pair.split("=", 1)
        if k.startswith("extra."):
            extra[k[6:]] = v
        else:
            fields[k] = float(v) if k in ("surplus_amount", "sale_price", "judgment_amount", "score") else v
    if extra:
        fields["extra"] = extra
    case = store.update_case(args.case_id, **fields)
    print(json.dumps(case, indent=2, default=str))


def cmd_draft(args, settings, store):
    overrides = json.loads(args.overrides) if args.overrides else None
    try:
        res = render(settings, store, args.case_id, args.doc_type, overrides)
    except RuleError as exc:
        raise SystemExit(f"Blocked: {exc}")
    print(res["text"] if args.print else f"Wrote {res['path']}")
    if res["compliance"]["warnings"]:
        print("Warnings:", *res["compliance"]["warnings"], sep="\n  - ", file=sys.stderr)


def cmd_packet(args, settings, store):
    res = build_packet(settings, store, args.case_id)
    print(json.dumps(res, indent=2))


def _require_active(settings, states: list[str]) -> None:
    inactive = [s for s in states if s not in settings.operator.active_states]
    if inactive:
        raise SystemExit(
            f"{', '.join(inactive)} is switched off. Active states: {', '.join(settings.operator.active_states)}. "
            f"Edit active_states in {settings.config_path} to change this."
        )


def _agent(settings, store, task: str, states: list[str], quiet: bool) -> None:
    import anthropic
    _require_active(settings, states)
    from .agent import run_agent  # imported lazily so non-API commands never touch the SDK
    try:
        print(run_agent(settings, store, task, states, quiet=quiet))
    except TypeError as exc:
        if "authentication" in str(exc).lower():
            raise SystemExit("No Anthropic credentials found. Set ANTHROPIC_API_KEY or run `ant auth login`.")
        raise
    except anthropic.AuthenticationError:
        raise SystemExit("Anthropic rejected the API key. Check ANTHROPIC_API_KEY.")
    except anthropic.RateLimitError as exc:
        raise SystemExit(f"Rate limited; retry after {exc.response.headers.get('retry-after', '60')}s.")
    except anthropic.APIConnectionError as exc:
        raise SystemExit(f"Could not reach the Anthropic API: {exc}")
    except anthropic.APIStatusError as exc:
        raise SystemExit(f"Anthropic API error {exc.status_code}: {exc.message}")


def cmd_find(args, settings, store):
    from .prompts import find_prompt
    state = args.state.upper()
    _require_active(settings, [state])
    task = find_prompt(state, args.county, args.min_amount, args.max_leads, store.sources(state, args.county))
    _agent(settings, store, task, [state], args.quiet)


def cmd_locate(args, settings, store):
    from .prompts import locate_prompt
    case = store.get_case(args.case_id)
    _agent(settings, store, locate_prompt(case, store.contacts(args.case_id), store.notes(args.case_id)),
           [case["state"]], args.quiet)


def cmd_prepare(args, settings, store):
    from .prompts import packet_prompt
    case = store.get_case(args.case_id)
    _agent(settings, store, packet_prompt(case), [case["state"]], args.quiet)


def cmd_run(args, settings, store):
    states = [s.upper() for s in args.states.split(",")] if args.states else list(settings.operator.active_states)
    _agent(settings, store, args.task, states, args.quiet)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="surplus", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create data dir and config"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("rules", help="print a state's rulebook"); s.add_argument("state"); s.set_defaults(fn=cmd_rules)
    s = sub.add_parser("fee", help="compute economics for an amount")
    s.add_argument("state"); s.add_argument("amount", type=float)
    s.add_argument("--sale-date"); s.add_argument("--deposit-date"); s.add_argument("--notice-date"); s.add_argument("--sale-type")
    s.set_defaults(fn=cmd_fee)
    s = sub.add_parser("import", help="import a CSV list of surplus rows")
    s.add_argument("file"); s.add_argument("--state", required=True); s.add_argument("--county", required=True)
    s.add_argument("--sale-type", required=True, choices=["mortgage_foreclosure", "tax_deed", "tax_sale"])
    s.add_argument("--source-url"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("score", help="re-score open cases"); s.set_defaults(fn=cmd_score)
    s = sub.add_parser("cases", help="list cases")
    s.add_argument("--state"); s.add_argument("--status", choices=CASE_STATUSES); s.add_argument("--county")
    s.add_argument("--min-amount", type=float); s.add_argument("--limit", type=int, default=50); s.set_defaults(fn=cmd_cases)
    s = sub.add_parser("sources", help="list known public-record sources"); s.add_argument("--state"); s.add_argument("--county")
    s.set_defaults(fn=cmd_sources)
    s = sub.add_parser("show", help="show one case"); s.add_argument("case_id", type=int); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("note", help="add a note"); s.add_argument("case_id", type=int); s.add_argument("text")
    s.add_argument("--kind", default="note"); s.add_argument("--source-url"); s.set_defaults(fn=cmd_note)
    s = sub.add_parser("set", help="set case fields: key=value (extra.key=value for document fields)")
    s.add_argument("case_id", type=int); s.add_argument("pairs", nargs="+"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("draft", help="render a document"); s.add_argument("case_id", type=int)
    s.add_argument("doc_type", choices=DOC_TYPES); s.add_argument("--overrides", help="JSON dict")
    s.add_argument("--print", action="store_true"); s.set_defaults(fn=cmd_draft)
    s = sub.add_parser("packet", help="build the claim packet folder"); s.add_argument("case_id", type=int); s.set_defaults(fn=cmd_packet)

    for name, fn, help_ in (("find", cmd_find, "agent: find leads"), ("locate", cmd_locate, "agent: skip-trace"),
                            ("prepare", cmd_prepare, "agent: research forms and build packet"),
                            ("run", cmd_run, "agent: free-form task")):
        s = sub.add_parser(name, help=help_)
        if name == "find":
            s.add_argument("state"); s.add_argument("--county"); s.add_argument("--min-amount", type=float, default=5000)
            s.add_argument("--max-leads", type=int, default=25)
        elif name == "run":
            s.add_argument("task"); s.add_argument("--states", help="comma list, default all")
        else:
            s.add_argument("case_id", type=int)
        s.add_argument("--quiet", action="store_true", help="hide the tool trace")
        s.set_defaults(fn=fn)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = load_settings()
    store = Store(settings.db_path)
    try:
        args.fn(args, settings, store)
    except (RuleError, KeyError, ValueError) as exc:
        raise SystemExit(f"error: {exc}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
