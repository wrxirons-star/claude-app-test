from datetime import date

import pytest

from surplus.scoring import classify_owner, score_case
from surplus.store import Store

TODAY = date(2026, 9, 7)


def test_upsert_is_idempotent_on_case_number(env):
    _, store = env
    cid, created = store.upsert_case(state="fl", county="Lee", case_number="A1", owner_name="X", surplus_amount=100)
    cid2, created2 = store.upsert_case(state="FL", county="Lee", case_number="A1", surplus_amount=200)
    assert cid == cid2 and created and not created2
    assert store.get_case(cid)["surplus_amount"] == 200
    assert store.get_case(cid)["owner_name"] == "X"


def test_extra_merges(env):
    _, store = env
    cid, _ = store.upsert_case(state="GA", county="Cobb", case_number="B2")
    store.update_case(cid, extra={"a": 1})
    store.update_case(cid, extra={"b": 2})
    assert store.get_case(cid)["extra"] == {"a": 1, "b": 2}


def test_invalid_status_rejected(env):
    _, store = env
    cid, _ = store.upsert_case(state="TX", county="Dallas", case_number="C3")
    with pytest.raises(ValueError):
        store.update_case(cid, status="bogus")


def test_contacts_notes_sources_documents_and_audit(env):
    _, store = env
    cid, _ = store.upsert_case(state="TX", county="Dallas", case_number="D4")
    store.add_contact(cid, name="Pat", relationship="owner", confidence=0.7, source_url="https://x")
    store.add_note(cid, "hello", kind="research")
    store.add_source("TX", "https://list", "list", county="Dallas", notes="pdf")
    store.add_source("TX", "https://list", "list", county="Dallas", notes="pdf updated")  # upsert
    store.add_document(cid, "intro_letter", "/tmp/x.md", {"ok": True})
    assert store.contacts(cid)[0]["name"] == "Pat"
    assert store.notes(cid)[0]["text"] == "hello"
    assert len(store.sources("TX", "Dallas")) == 1 and store.sources("TX")[0]["notes"] == "pdf updated"
    assert store.documents(cid)[0]["doc_type"] == "intro_letter"
    actions = [r["action"] for r in store.conn.execute("SELECT action FROM audit").fetchall()]
    assert "create_case" in actions and "add_contact" in actions


def test_list_filters(env):
    _, store = env
    store.upsert_case(state="FL", county="Lee", case_number="1", surplus_amount=1000, status="new")
    store.upsert_case(state="FL", county="Lee", case_number="2", surplus_amount=9000, status="qualified")
    store.upsert_case(state="GA", county="Cobb", case_number="3", surplus_amount=5000)
    assert len(store.list_cases(state="FL")) == 2
    assert len(store.list_cases(min_amount=4000)) == 2
    assert store.list_cases(status="qualified")[0]["case_number"] == "2"


def test_classify_owner():
    assert classify_owner("ACME HOLDINGS LLC") == "entity"
    assert classify_owner("ESTATE OF JOHN DOE") == "estate"
    assert classify_owner("JOHN AND JANE DOE") == "multiple"
    assert classify_owner("JANE DOE") == "individual"
    assert classify_owner(None) == "unknown"


def test_scoring_prefers_individuals_and_penalizes_waiting():
    base = {"state": "GA", "sale_type": "tax_sale", "surplus_amount": 20000, "sale_date": "2024-01-15"}
    ind, r1 = score_case({**base, "owner_name": "JANE DOE"}, today=TODAY)
    ent, r2 = score_case({**base, "owner_name": "ACME LLC"}, today=TODAY)
    assert ind > ent and r1["owner_type"] == "individual"
    waiting, r3 = score_case({**base, "owner_name": "JANE DOE", "sale_date": "2026-01-15"}, today=TODAY)
    assert waiting < ind and "waiting" in r3


def test_scoring_zero_without_amount():
    assert score_case({"state": "FL"}, today=TODAY)[0] == 0.0


def test_florida_tax_deed_sweet_spot_bonus():
    base = {"state": "FL", "sale_type": "tax_deed", "surplus_amount": 20000, "owner_name": "JANE DOE",
            "sale_date": "2026-01-10"}
    plain, _ = score_case(base, today=TODAY)
    sweet, r = score_case({**base, "notice_date": "2026-02-01"}, today=TODAY)   # 218 days old
    early, r2 = score_case({**base, "notice_date": "2026-08-15"}, today=TODAY)  # 23 days old
    assert sweet > plain and "sweet_spot" in r
    assert early == plain and "lien_window" in r2


def test_seed_sources_is_idempotent(env):
    _, store = env
    n1 = store.seed_sources("FL")
    n2 = store.seed_sources("FL")
    assert n1 == n2 > 5
    assert len(store.sources("FL")) == n1
    assert any("leeclerk" in s["url"] for s in store.sources("FL", "Lee"))
    assert store.seed_sources("TX") == 0
