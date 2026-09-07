from datetime import date

import pytest

from surplus.rules import RuleError, check_agreement, compute_economics, load_rules, rules_summary

TODAY = date(2026, 9, 7)


def test_rulebooks_load_and_summarize():
    for s in ("FL", "TX", "GA"):
        assert load_rules(s)["state"] == s
        assert "Citations" in rules_summary(s)


def test_unknown_state():
    with pytest.raises(RuleError):
        compute_economics("CA", 1000)


def test_florida_defaults_to_tax_deed():
    e = compute_economics("FL", 20000, "2026-08-20", today=TODAY)
    assert e.sale_type == "tax_deed"
    assert e.claim_deadline == date(2027, 8, 20)


def test_florida_foreclosure_economics():
    e = compute_economics("FL", 20000, "2026-08-20", sale_type="mortgage_foreclosure", today=TODAY)
    assert e.model == "contingency_fee"
    assert e.fee_cap_fraction == 0.12
    assert e.expected_gross == 2400.0
    assert e.claim_deadline == date(2027, 8, 20)
    assert any("Owner priority" in w for w in e.warnings)


def test_florida_fee_policy_over_cap_raises():
    with pytest.raises(RuleError):
        compute_economics("FL", 20000, fee_policy={"FL": 0.20}, today=TODAY)


def test_florida_tax_deed_notice_date_drives_warnings():
    open_window = compute_economics("FL", 10000, "2026-07-01", notice_date="2026-08-01", today=TODAY)
    assert any("open until 2026-11-29" in w for w in open_window.warnings)
    closed = compute_economics("FL", 10000, "2026-01-10", notice_date="2026-02-01", today=TODAY)
    assert any("conclusively presumed" in w for w in closed.warnings)
    assert closed.claim_deadline == date(2027, 1, 10)
    no_notice = compute_economics("FL", 10000, "2026-01-10", today=TODAY)
    assert any("No Notice of Surplus" in w for w in no_notice.warnings)


def test_texas_claim_purchase_economics():
    e = compute_economics("TX", 20000, "2025-01-15", today=TODAY)
    assert e.model == "claim_purchase"
    assert e.capital_required == 16000.0
    assert e.expected_gross == 4000.0  # min(20000, 16000*1.25) - 16000
    assert e.earliest_agreement_date == date(2025, 2, 20)
    assert e.claim_deadline == date(2027, 1, 15)


def test_texas_purchase_fraction_below_minimum_is_raised():
    e = compute_economics("TX", 10000, "2025-01-15", tx_purchase_fraction=0.5, today=TODAY)
    assert e.capital_required == 8000.0
    assert any("raised" in w for w in e.warnings)


def test_texas_attorney_referral_is_capped():
    e = compute_economics("TX", 20000, "2025-01-15", has_attorney=True, today=TODAY)
    assert e.model == "attorney_referral"
    assert e.expected_gross == 1000.0


def test_georgia_economics_and_waiting_period():
    e = compute_economics("GA", 20000, "2025-03-01", today=TODAY)
    assert e.fee_cap_fraction == 0.10
    assert e.expected_gross == 2000.0
    assert e.earliest_agreement_date == date(2027, 3, 1)
    assert e.claim_deadline == date(2030, 3, 1)
    assert any("Do not sign" in w for w in e.warnings)


def test_past_deadline_warns():
    e = compute_economics("TX", 5000, "2020-01-01", today=TODAY)
    assert e.days_until_deadline < 0
    assert any("passed" in w for w in e.warnings)


def test_texas_gate_blocks_fee_and_phone():
    res = check_agreement("TX", "tax_sale", 0.25, 20000, "2026-09-07", "2025-01-15", contact_channel="phone", today=TODAY)
    assert not res.ok
    joined = " ".join(res.errors)
    assert "non-attorney" in joined and "telephone" in joined


def test_texas_gate_allows_compliant_purchase():
    res = check_agreement("TX", "tax_sale", None, 20000, "2026-09-07", "2025-01-15",
                          contact_channel="mail", purchase_fraction=0.8, today=TODAY)
    assert res.ok, res.errors


def test_texas_gate_blocks_early_assignment():
    res = check_agreement("TX", "tax_sale", None, 20000, "2026-09-07", "2026-08-20", purchase_fraction=0.8, today=TODAY)
    assert not res.ok and "36 days" in res.errors[0]


def test_georgia_gate_blocks_before_24_months_and_over_cap():
    res = check_agreement("GA", "tax_sale", 0.15, 20000, "2026-09-07", "2025-01-15", today=TODAY)
    assert not res.ok
    assert any("10%" in e for e in res.errors)
    assert any("24 months" in e for e in res.errors)


def test_georgia_gate_ok_after_24_months():
    res = check_agreement("GA", "tax_sale", 0.10, 20000, "2026-09-07", "2024-01-15", today=TODAY)
    assert res.ok


def test_florida_gate_caps_at_12():
    assert not check_agreement("FL", "mortgage_foreclosure", 0.13, 20000, None, None, today=TODAY).ok
    assert check_agreement("FL", "mortgage_foreclosure", 0.12, 20000, None, None, today=TODAY).ok
    assert not check_agreement("FL", None, 0.13, 20000, None, None, today=TODAY).ok  # tax deed default
