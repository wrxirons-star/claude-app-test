import json

import surplus.parcels as parcels


def test_lee_strap_normalization_and_query(monkeypatch):
    seen = {}

    class F:
        text = json.dumps({"features": [{"attributes": {"STRAP": "014423C2024650400", "OWNER": "X"}}]})

    def fake_fetch(url, save_dir=None):
        seen["url"] = url
        return F()

    monkeypatch.setattr(parcels, "fetch", fake_fetch)
    res = parcels.lookup("fl", "Lee", "01-44-23-C2-02465.0400")
    assert res["normalized_id"] == "014423C2024650400"
    assert "STRAP%3D%27014423C2024650400%27" in seen["url"] and "f=json" in seen["url"]
    assert res["matches"] == 1 and res["attributes"][0]["OWNER"] == "X"


def test_unknown_county():
    import pytest
    with pytest.raises(KeyError):
        parcels.lookup("FL", "Pasco", "1")


def test_tool_wiring(env, monkeypatch):
    from surplus.tools import make_tools
    tools = {t.name: t for t in make_tools(*env)}
    assert "parcel_lookup" in tools
    assert "Error" in tools["parcel_lookup"].call({"county": "Pasco", "parcel_id": "1"})
    out = tools["economics"].call({"state": "FL", "surplus_amount": 1000, "sale_date": "2022-04-26",
                                   "listed_date": "2026-09-26"})
    assert "still held" in out
    cid, _ = env[1].upsert_case(state="FL", county="Lee", case_number="Z", owner_type="individual")
    tools["update_case"].call({"case_id": cid, "owner_type": "estate"})
    assert env[1].get_case(cid)["owner_type"] == "estate"
