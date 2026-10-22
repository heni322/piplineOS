"""Sending-domain DNS check (SPF, DKIM, DMARC) against fake DNS answers: no network."""

from __future__ import annotations

from app.domain.deliverability.dns_check import check_domain
from app.domain.deliverability.warmup import WarmupPlan

D = "outreach.example.co.uk"
M365 = {
    (D, "TXT"): ["v=spf1 include:spf.protection.outlook.com -all", "MS=ms123456"],
    (f"selector1._domainkey.{D}", "CNAME"): ["selector1-outreach._domainkey.contoso.onmicrosoft.com"],
    (f"selector2._domainkey.{D}", "CNAME"): ["selector2-outreach._domainkey.contoso.onmicrosoft.com"],
    (f"_dmarc.{D}", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:dmarc@example.co.uk"],
}


def dns(records: dict[tuple[str, str], list[str]]):  # type: ignore[no-untyped-def]
    return lambda name, rdtype: records.get((name, rdtype), [])


def test_a_correct_microsoft_365_domain_passes() -> None:
    r = check_domain(D, {"msgraph"}, dns(M365))
    assert (r.spf_ok, r.dkim_ok, r.dmarc_ok, r.dmarc_policy, r.issues) == (True, True, True, "quarantine", [])


def test_a_correct_google_domain_passes_with_a_monitoring_note() -> None:
    g = {
        (D, "TXT"): ["v=spf1 include:_spf.google.com ~all"],
        (f"google._domainkey.{D}", "TXT"): ["v=DKIM1; k=rsa; p=MIIBIjANBgkqh"],
        (f"_dmarc.{D}", "TXT"): ["v=DMARC1; p=none"],
    }
    r = check_domain(D.upper() + ".", {"google"}, dns(g))
    assert r.ok and r.dmarc_policy == "none"
    assert r.issues == [
        "DMARC is in monitoring mode (p=none): accepted, but move to quarantine once reports look clean."
    ]


def test_each_problem_is_named_in_plain_words() -> None:
    bad = {
        **M365,
        (D, "TXT"): ["v=spf1 include:_spf.google.com -all"],  # wrong provider
        (f"selector2._domainkey.{D}", "CNAME"): [],  # half of DKIM
        (f"_dmarc.{D}", "TXT"): [],
    }
    r = check_domain(D, {"msgraph"}, dns(bad))
    assert not (r.spf_ok or r.dkim_ok or r.dmarc_ok)
    assert any("doesn't include spf.protection.outlook.com" in i for i in r.issues)
    assert any(f"selector2._domainkey.{D}" in i for i in r.issues)
    assert any(f"_dmarc.{D}" in i for i in r.issues)


def test_dangerous_or_ambiguous_spf_is_rejected() -> None:
    for rec, words in (
        (["v=spf1 include:spf.protection.outlook.com +all"], "+all"),
        (["v=spf1 include:spf.protection.outlook.com"], "-all or ~all"),
        (["v=spf1 -all", "v=spf1 include:spf.protection.outlook.com -all"], "exactly one"),
        ([], "No SPF record"),
    ):
        r = check_domain(D, {"msgraph"}, dns({**M365, (D, "TXT"): rec}))
        assert not r.spf_ok and any(words in i for i in r.issues), (rec, r.issues)
    assert not check_domain(D, {"msgraph"}, dns({**M365, (f"_dmarc.{D}", "TXT"): ["v=DMARC1; rua=x"]})).dmarc_ok


def test_unknown_provider_accepts_either_dkim_style() -> None:
    r = check_domain(D, {"mock"}, dns(M365))
    assert r.ok


def test_warmup_caps_rise_to_the_target() -> None:
    p = WarmupPlan(start_cap=10, daily_increase=2, target_cap=40, days_to_active=21)
    assert [p.cap_for(d) for d in (0, 1, 5, 15, 30)] == [10, 12, 20, 40, 40]


def test_a_shared_hosting_domain_passes_with_its_own_dkim_selector() -> None:
    # LWS (and most cPanel/Plesk hosts) publish DKIM under the "dkim" or "default" selector, not M365's or Google's.
    lws = {
        ("vayz-pro.fr", "TXT"): ["v=spf1 mx:vayz-pro.fr a:mail.vayz-pro.fr a:mailphp.lws-hosting.com -all"],
        ("dkim._domainkey.vayz-pro.fr", "TXT"): ["v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3"],
        ("_dmarc.vayz-pro.fr", "TXT"): ["v=DMARC1; p=quarantine; rua=mailto:contact@vayz-pro.fr"],
    }
    r = check_domain("vayz-pro.fr", set(), dns(lws))
    assert r.ok and r.issues == []
