"""Assemble a claim packet folder for a case: checklist, agreement, cover letter,
compliance record, and a case summary. Pure filesystem work; the agent adds
county-specific form links via notes before this runs.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from .config import Settings
from .documents import render
from .rules import RuleError
from .store import Store


def build_packet(settings: Settings, store: Store, case_id: int, today: date | None = None,
                 actor: str = "cli") -> dict[str, Any]:
    today = today or date.today()
    case = store.get_case(case_id)
    out = settings.packets_dir / f"case-{case_id}-{today.isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    produced: dict[str, str] = {}
    problems: list[str] = []

    for doc_type in ("packet_checklist", "claim_cover_letter", "agreement"):
        try:
            result = render(settings, store, case_id, doc_type, today=today, actor=actor)
        except RuleError as exc:
            problems.append(f"{doc_type}: {exc}")
            continue
        dest = out / f"{doc_type}.md"
        shutil.copyfile(result["path"], dest)
        produced[doc_type] = str(dest)
        if doc_type == "agreement":
            (out / "compliance.json").write_text(json.dumps(result["compliance"], indent=2))
            (out / "economics.json").write_text(json.dumps(result["economics"], indent=2))

    summary = {
        "case": case,
        "contacts": store.contacts(case_id),
        "notes": store.notes(case_id),
        "sources": store.sources(case["state"], case.get("county")),
        "documents": produced,
        "problems": problems,
        "built": today.isoformat(),
    }
    (out / "case_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    store.add_note(case_id, f"Packet built at {out}" + (f" with problems: {problems}" if problems else ""),
                   kind="packet", actor=actor)
    if not problems and case.get("status") in ("new", "qualified", "locating", "contacting", "engaged"):
        store.update_case(case_id, actor=actor, next_action="review packet and send to claimant for signature")
    return {"path": str(out), "documents": produced, "problems": problems}
