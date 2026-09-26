import json
from datetime import date
from pathlib import Path

import pytest

from surplus.cli import main
from surplus.documents import render
from surplus.packet import build_packet
from surplus.rules import RuleError

TODAY = date(2026, 9, 7)


def _fl(store):
    cid, _ = store.upsert_case(state="FL", county="Sumter", sale_type="mortgage_foreclosure", case_number="2025-CA-1",
                               owner_name="JANE Q DOE", property_address="123 Main St", sale_date="2026-08-20",
                               surplus_amount=41250.55)
    return cid


def test_florida_letter_and_agreement(env):
    settings, store = env
    cid = _fl(store)
    letter = render(settings, store, cid, "intro_letter", today=TODAY)["text"]
    assert "at no charge" in letter and "not affiliated" in letter and "12 percent" in letter
    agr = render(settings, store, cid, "agreement", {"claimant_address": "PO Box 1"}, today=TODAY)
    assert "12%" in agr["text"] and "$4,950.07" in agr["text"] and "PO Box 1" in agr["text"]
    assert agr["compliance"]["ok"]
    assert store.get_case(cid)["extra"]["claimant_address"] == "PO Box 1"
    assert Path(agr["path"]).exists()


def test_texas_agreement_is_purchase_and_phone_script_blocked(env):
    settings, store = env
    cid, _ = store.upsert_case(state="TX", county="Dallas", sale_type="tax_sale", case_number="TX-1",
                               owner_name="JOHN SMITH", sale_date="2025-06-03", surplus_amount=18000)
    agr = render(settings, store, cid, "agreement", today=TODAY)["text"]
    assert "ASSIGNMENT OF CLAIM" in agr and "$14,400.00" in agr and "125 percent" in agr
    letter = render(settings, store, cid, "intro_letter", today=TODAY)["text"]
    assert "We do not charge a fee" in letter and "will not call you" in letter
    with pytest.raises(RuleError):
        render(settings, store, cid, "phone_script", today=TODAY)


def test_texas_phone_solicited_assignment_blocked(env):
    settings, store = env
    cid, _ = store.upsert_case(state="TX", county="Dallas", sale_type="tax_sale", case_number="TX-2",
                               owner_name="A", sale_date="2025-06-03", surplus_amount=18000,
                               extra={"contact_channel": "phone"})
    with pytest.raises(RuleError):
        render(settings, store, cid, "agreement", today=TODAY)


def test_georgia_agreement_blocked_inside_24_months_then_allowed(env):
    settings, store = env
    cid, _ = store.upsert_case(state="GA", county="Gwinnett", sale_type="tax_sale", case_number="GA-1",
                               owner_name="B", sale_date="2025-05-01", surplus_amount=9000)
    with pytest.raises(RuleError):
        render(settings, store, cid, "agreement", today=TODAY)
    res = build_packet(settings, store, cid, today=TODAY)
    assert res["problems"] and "agreement" in res["problems"][0]
    store.update_case(cid, sale_date="2024-05-01")
    text = render(settings, store, cid, "agreement", today=TODAY)["text"]
    assert "10%" in text and "directly to the Claimant" in text


def test_packet_contents(env):
    settings, store = env
    cid = _fl(store)
    store.add_note(cid, "clerk confirmed amount")
    res = build_packet(settings, store, cid, today=TODAY)
    p = Path(res["path"])
    assert not res["problems"]
    assert (p / "compliance.json").exists() and (p / "case_summary.json").exists()
    checklist = (p / "packet_checklist.md").read_text()
    assert "clerk confirmed amount" in checklist and "45.033" in checklist
    assert json.loads((p / "economics.json").read_text())["expected_gross"] == 4950.07


def test_cli_roundtrip(env, tmp_path, capsys):
    settings, store = env
    csv_path = tmp_path / "list.csv"
    csv_path.write_text("Case No,Defendant,Surplus,Sale Date\n2025-CA-9,DOE JANE,\"$12,000.50\",2026-07-01\n"
                        "2025-CA-10,ACME LLC,300,2026-07-01\n")
    main(["import", str(csv_path), "--state", "FL", "--county", "Lee", "--sale-type", "mortgage_foreclosure"])
    out = capsys.readouterr().out
    assert "Imported 2 new" in out
    main(["cases", "--state", "FL"])
    out = capsys.readouterr().out
    assert "DOE JANE" in out and "qualified" in out and "2025-CA-9" in out
    main(["fee", "TX", "25000", "--sale-date", "2025-06-01"])
    assert json.loads(capsys.readouterr().out)["model"] == "claim_purchase"
    main(["set", "1", "status=contacting", "extra.claimant_address=1 Elm St"])
    before = json.loads(capsys.readouterr().out)
    assert before["extra"]["claimant_address"] == "1 Elm St"
    main(["set", "1", "owner_type=estate"])
    after = json.loads(capsys.readouterr().out)
    assert after["score"] < before["score"]
    main(["draft", "1", "intro_letter", "--print"])
    assert "1 Elm St" in capsys.readouterr().out
    main(["rules", "GA"])
    assert "10%" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["rules", "CA"])


def test_inactive_state_is_refused(env, tmp_path, capsys):
    settings, store = env
    assert settings.operator.active_states == ["FL"]
    csv_path = tmp_path / "tx.csv"
    csv_path.write_text("Case No,Owner,Amount\n1,A,100\n")
    with pytest.raises(SystemExit) as exc:
        main(["import", str(csv_path), "--state", "TX", "--county", "Dallas", "--sale-type", "tax_sale"])
    assert "switched off" in str(exc.value)
    with pytest.raises(SystemExit) as exc:
        main(["find", "GA", "--county", "Cobb"])
    assert "switched off" in str(exc.value)
