from datetime import date

from surplus.parsers import get_parser
from surplus.parsers.lee_fl import parse
from surplus.rules import compute_economics
from surplus.scoring import classify_owner, score_case

SAMPLE = """--- page 1 ---
Tax Deed Number Sale Date Balance Balance Date Property Address Parcel ID Owner Name Lienholder Claim Period Expires
2014001930 9/30/2014 508.74 6/26/2019 133 NAVAHO AVE S LEHIGH ACRES 33974 01452704000230050 TAHIR S ANSARI
2014001931 2/10/2015 345.20 6/26/2019 133 NECTAR AVE S LEHIGH ACRES 33974 01452704000240050 TAHIR ANASARI ,  ROSA MONGELLI ESTATE
2016002994 4/11/2017 2,169.32 1/22/2026 21-44-27-07-00027.0160 Faith Divine Esimai
2016002949 4/11/2017 450.42 1/22/2026 26-44-27-08-00029.024B HILTON CAPITAL GROUP LLC
2021002777 4/26/2022 39,570.90 9/8/2026 1921 NE 34TH TER CAPE CORAL FL 33909 20-43-24-C1-05641.0290 ANDRE MONTMAYEUR, CATHERINE VUILLEMARD-MONTMAYEUR 9/9/2022
2021002106 4/26/2022 28,495.75 8/17/2022 4109 CASTILLA CIR FORT MYERS FL 33916 31-44-25-P4-02628.0201 VILLAS AT VENEZIA CONDOMINIUM ASSOCIATION INC 9/9/2022
2022000313 10/25/2022 8,842.26 9/8/2026 1144 NE 3RD AVE CAPE CORAL FL 33909 01-44-23-C1-02437.0790 RALPH THOMPSON, RUTH THOMPSON 3/21/2023
some junk line that is not a row
"""


def test_lee_parser_rows():
    rows = parse(SAMPLE, "https://x/report.pdf")
    assert len(rows) == 7
    by = {r["case_number"]: r for r in rows}
    r = by["2021002777"]
    assert r["surplus_amount"] == 39570.90
    assert r["sale_date"] == "2022-04-26"
    assert r["property_address"] == "1921 NE 34TH TER CAPE CORAL FL 33909"
    assert r["parcel_id"] == "20-43-24-C1-05641.0290"
    assert r["owner_name"] == "ANDRE MONTMAYEUR, CATHERINE VUILLEMARD-MONTMAYEUR"
    assert r["extra"]["lienholder_claim_expires"] == "2022-09-09"
    assert r["notice_date"] == "2022-05-12"
    assert r["holder"].startswith("Clerk")
    # no address, dashed parcel, no expiry
    e = by["2016002994"]
    assert e["property_address"] is None and e["parcel_id"] == "21-44-27-07-00027.0160"
    assert e["owner_name"] == "Faith Divine Esimai" and e["notice_date"] is None
    # 17-digit parcel, estate owner with stray comma
    assert by["2014001931"]["owner_name"] == "TAHIR ANASARI, ROSA MONGELLI ESTATE"
    assert by["2016002949"]["parcel_id"] == "26-44-27-08-00029.024B"
    assert classify_owner(by["2021002106"]["owner_name"]) == "entity"
    assert classify_owner(by["2016002949"]["owner_name"]) == "entity"
    assert classify_owner(by["2022000313"]["owner_name"]) == "multiple"


def test_registry():
    assert get_parser("fl", "Lee") is parse
    assert get_parser("FL", "Pasco") is None


def test_listed_date_overrides_passed_deadline():
    today = date(2026, 9, 26)
    stale = compute_economics("FL", 39570.90, "2022-04-26", notice_date="2022-05-12", today=today)
    assert stale.days_until_deadline < 0
    live = compute_economics("FL", 39570.90, "2022-04-26", notice_date="2022-05-12", today=today,
                             listed_date="2026-09-26")
    assert live.claim_deadline == date(2027, 5, 1)
    assert live.days_until_deadline > 0
    assert any("still held" in w for w in live.warnings)
    s_stale, r1 = score_case({"state": "FL", "sale_type": "tax_deed", "surplus_amount": 39570.90,
                              "sale_date": "2022-04-26", "owner_name": "ANDRE MONTMAYEUR"}, today=today)
    s_live, r2 = score_case({"state": "FL", "sale_type": "tax_deed", "surplus_amount": 39570.90,
                             "sale_date": "2022-04-26", "owner_name": "ANDRE MONTMAYEUR",
                             "extra": {"listed_date": "2026-09-26"}}, today=today)
    assert s_live > s_stale * 5 and r1["deadline"] == "passed"


def test_import_report_from_local_file(env, tmp_path):
    from surplus.cli import import_report
    settings, store = env
    f = tmp_path / "lee.txt"
    f.write_text(SAMPLE)
    res = import_report(settings, store, "FL", "Lee", str(f), min_amount=5000)
    assert res["rows_in_report"] == 7 and res["rows_at_or_above_minimum"] == 3
    cases = store.list_cases(state="FL")
    assert {c["case_number"] for c in cases} == {"2021002777", "2021002106", "2022000313"}
    top = store.get_case(res["case_ids"][0])
    assert top["owner_type"] in ("multiple", "entity")
    assert store.sources("FL", "Lee")
    # importing again updates rather than duplicates
    res2 = import_report(settings, store, "FL", "Lee", str(f), min_amount=5000)
    assert res2["created"] == 0 and res2["updated"] == 3


WRAPPED = """--- page 3 ---
Tax Deed Number Sale Date Balance Balance Date Property Address Parcel ID Owner Name Lienholder Claim Period Expires
2024002473 2/25/2025 39,488.65 10/29/2025 2213 KENDALL AVE S LEHIGH ACRES FL 33973 32-44-26-09-00025.0040
BARBARA J BROWN, DAVID W SCHULTZ, KATHLEEN SCHULTZ, DAVID SCHULTZ, MICHAEL H FLANAGAN,
NANCY FLANAGAN 7/10/2025
2024002737 4/8/2025 62,565.17 10/6/2025 775 KNOTTY PINE CIR NORTH FORT MYERS FL 33917 36-43-24-11-0000A.0460
INGRID STOBBE TRUST UNDER AGREEMENT DATED AUGUST 6, 2014, STOBBE INGRID TR FOR INGRID
2025000125 7/29/2025 142,006.08 8/6/2025 1519 FOUNTAIN AVE FORT MYERS FL 33919 03-45-24-05-0000F.0100 CAROL JO SOFKO TRUST 12/12/2025
2025001675 12/16/2025 65,844.13 9/2/2026 ACCESS UNDETERMINED BONITA SPRINGS FL 29-47-26-B2-00002.0020
ESTATE OF DOLORES LORD, CHARLES T KOMPARE, CHERYL SPRAN, GARY KOMPARE, JOHN SKALA,
--- page 4 ---
Tax Deed Number Sale Date Balance Balance Date Property Address Parcel ID Owner Name Lienholder Claim Period Expires
2025001936 1/13/2026 55,004.69 8/4/2026 7043 NEW POST DR 4 NORTH FORT MYERS FL 33917 36-43-24-20-0000G.0040 MYRNA KIDD, ESTATE OF MYRNA KIDD 5/29/2026
"""


def test_lee_parser_wrapped_owners_and_lettered_parcels():
    by = {r["case_number"]: r for r in parse(WRAPPED, "u")}
    assert len(by) == 5
    r = by["2024002473"]
    assert r["parcel_id"] == "32-44-26-09-00025.0040"
    assert r["owner_name"].startswith("BARBARA J BROWN") and r["owner_name"].endswith("NANCY FLANAGAN")
    assert r["extra"]["lienholder_claim_expires"] == "2025-07-10" and r["notice_date"] == "2025-03-12"
    r = by["2024002737"]
    assert r["parcel_id"] == "36-43-24-11-0000A.0460"
    assert r["property_address"] == "775 KNOTTY PINE CIR NORTH FORT MYERS FL 33917"
    assert r["owner_name"].startswith("INGRID STOBBE TRUST")
    assert classify_owner(r["owner_name"]) == "entity"
    r = by["2025000125"]
    assert r["parcel_id"] == "03-45-24-05-0000F.0100" and r["owner_name"] == "CAROL JO SOFKO TRUST"
    r = by["2025001675"]
    assert r["property_address"] == "ACCESS UNDETERMINED BONITA SPRINGS FL"
    assert r["owner_name"].startswith("ESTATE OF DOLORES LORD") and r["owner_name"].endswith("JOHN SKALA")
    assert classify_owner(r["owner_name"]) == "estate"
    r = by["2025001936"]
    assert r["parcel_id"] == "36-43-24-20-0000G.0040" and r["owner_name"] == "MYRNA KIDD, ESTATE OF MYRNA KIDD"
