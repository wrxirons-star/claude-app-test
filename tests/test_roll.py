import io
import zipfile

from surplus.roll import load_roll, normalize_parcel

CSV = (
    "CO_NO,PARCEL_ID,OWN_NAME,OWN_ADDR1,OWN_ADDR2,OWN_CITY,OWN_STATE,OWN_ZIPCD,PHY_ADDR1,PHY_CITY,SALE_PRC1,SALE_YR1,SALE_MO1\r\n"
    "36,304324C2010600090,WEST MARTIN,PO BOX 123,,CAPE CORAL,FL,33915,1388 WEEPING WILLOW CT,CAPE CORAL,92000,2008,4\r\n"
    "36,014423C2024650400,VINGIANO PATRICIA,12 ELM ST,,WEST HAVEN,CT,06516,1317 NE 5TH PL,CAPE CORAL,193000,2011,9\r\n"
    "36,999999999999999999,WEST MARTIN + JANE,44 OAK AVE,,FORT MYERS,FL,33901,44 OAK AVE,FORT MYERS,,,\r\n"
)


def _zip(tmp_path):
    p = tmp_path / "NAL36F202501.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("NAL36F202501.csv", CSV)
    return p


def test_load_zip_lookup_and_owner_search(env, tmp_path):
    settings, store = env
    res = load_roll(store, str(_zip(tmp_path)), "FL", "Lee", 2025)
    assert res["rows"] == 3 and res["files"] == 1
    rows = store.roll_lookup("FL", "Lee", normalize_parcel("30-43-24-C2-01060.0090"))
    assert rows[0]["owner"] == "WEST MARTIN" and rows[0]["addr1"] == "PO BOX 123" and rows[0]["year"] == 2025
    assert store.roll_years("FL", "Lee")[0]["rows"] == 3
    assert len(store.roll_owner_search("FL", "Lee", "west martin")) == 2
    # reload replaces, does not duplicate
    load_roll(store, str(_zip(tmp_path)), "FL", "Lee", 2025)
    assert store.roll_years("FL", "Lee")[0]["rows"] == 3


def test_load_plain_csv_and_tools(env, tmp_path):
    settings, store = env
    p = tmp_path / "roll.csv"
    p.write_text(CSV)
    from surplus.tools import make_tools
    tools = {t.name: t for t in make_tools(settings, store)}
    assert "No tax roll loaded" in tools["prior_roll_address"].call({"county": "Lee", "parcel_id": "x"})
    load_roll(store, str(p), "FL", "Lee", 2024)
    out = tools["prior_roll_address"].call({"county": "Lee", "parcel_id": "01-44-23-C2-02465.0400"})
    assert "WEST HAVEN" in out and "12 ELM ST" in out
    assert '"matches": 0' in tools["prior_roll_address"].call({"county": "Lee", "parcel_id": "00-00"})
    assert "44 OAK AVE" in tools["roll_owner_search"].call({"county": "Lee", "name_fragment": "WEST MARTIN"})


def test_html_source_rejected(env, tmp_path):
    import pytest
    settings, store = env
    p = tmp_path / "page.html"
    p.write_text("<html><body>not a roll</body></html>")
    with pytest.raises(ValueError):
        load_roll(store, str(p), "FL", "Lee", 2025)


LEE_CSV = (
    '"CountyNumber","Strap","RollType","RollYear","Name","Address1","Address2","City","State","ZipCode","DomicileState",'
    '"FiduciaryName","FiduciaryAddress1","FiduciaryAddress2","FiduciaryCity","FiduciaryState","FiduciaryZip","FiduciaryType",'
    '"Legal","SiteAddress1","SiteAddress2","SiteCity","SiteZip"\r\n'
    '"46","304324C2010600090","R","2025","WEST MARTIN","PO BOX 9","","CAPE CORAL","FL","33915","FL","","","","","","","",'
    '"LOT 9","1388 WEEPING WILLOW CT","","CAPE CORAL","33909"\r\n'
    '"46","014423C2024650400","R","2025","VINGIANO PATRICIA EST","","","","","","","SMITH JOHN PR","5 MAIN ST","","WEST HAVEN","CT","06516","PR",'
    '"LOT 4","1317 NE 5TH PL","","CAPE CORAL","33909"\r\n'
)


def test_lee_county_header_variant_and_fiduciary(env, tmp_path):
    settings, store = env
    p = tmp_path / "2025_NAL12D8.txt"
    p.write_text(LEE_CSV)
    res = load_roll(store, str(p), "FL", "Lee", 2025)
    assert res["rows"] == 2
    w = store.roll_lookup("FL", "Lee", normalize_parcel("30-43-24-C2-01060.0090"))[0]
    assert w["owner"] == "WEST MARTIN" and w["addr1"] == "PO BOX 9" and w["phy_addr"] == "1388 WEEPING WILLOW CT"
    v = store.roll_lookup("FL", "Lee", normalize_parcel("01-44-23-C2-02465.0400"))[0]
    assert v["fid_name"] == "SMITH JOHN PR" and v["fid_city"] == "WEST HAVEN"
    from surplus.tools import make_tools
    tools = {t.name: t for t in make_tools(settings, store)}
    assert "SMITH JOHN PR" in tools["prior_roll_address"].call({"county": "Lee", "parcel_id": "01-44-23-C2-02465.0400"})


def test_zip_with_txt_and_pdfs_picks_the_roll(env, tmp_path):
    settings, store = env
    p = tmp_path / "2025 Tax Roll NAL12D8.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("2025_NAL12D8.txt", LEE_CSV)
        zf.writestr("DOR_NAL_Field_Info_2025.pdf", b"%PDF-1.4 junk")
        zf.writestr("Field_List_NAL12D8_2025.txt", "Field list\nStrap\nName\n")
    res = load_roll(store, str(p), "FL", "Lee", 2025)
    assert res["rows"] == 2 and res["files"] == 1
