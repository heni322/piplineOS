"""Contract tests for published business contacts from the company's own website: proof of ownership by company
number, generic addresses only, robots.txt, redirects and private addresses, Wikidata lookup, MX, and the verifier.
In-process httpx.MockTransport only; synthetic sites on .example-style names."""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.enrichment.base import EnrichedPerson
from app.adapters.website_contacts import (
    WebsiteContacts,
    WebsiteFinder,
    candidate_domains,
    generic_addresses,
    states_number,
)

NUMBER = "01234567"
FOOTER = "Lakeside Networks Ltd. Registered in England and Wales, company number 01234567."
CONTACT = (
    """<html><body><h1>Contact</h1>
<p>New projects: <a href="mailto:sales@lakesidenetworks.co.uk">sales@lakesidenetworks.co.uk</a></p>
<p>Data protection: privacy@lakesidenetworks.co.uk. Jobs: careers@lakesidenetworks.co.uk</p>
<p>Dana Reyes, Operations Director: dana.reyes@lakesidenetworks.co.uk</p>
<p>Our partner: info@otherfirm.example</p><footer>"""
    + FOOTER
    + "</footer></body></html>"
)


def site(pages: dict[str, Any], seen: list[str] | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(req.url))
        body = pages.get(f"{req.url.host}{req.url.path}")
        if body is None:
            return httpx.Response(404, text="not found")
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler)


def finder(pages: dict[str, Any], seen: list[str] | None = None, **kw: Any) -> WebsiteFinder:
    no_wikidata = httpx.MockTransport(lambda r: httpx.Response(200, json={"results": {"bindings": []}}))
    return WebsiteFinder(
        transport=site(pages, seen),
        wikidata_transport=kw.pop("wikidata", no_wikidata),
        public_host=kw.pop("public_host", lambda h: True),
        mx=kw.pop("mx", lambda d: True),
    )


def test_helpers() -> None:
    assert candidate_domains("Lakeside Networks Ltd") == [
        "lakesidenetworks.co.uk",
        "lakeside-networks.co.uk",
        "lakesidenetworks.com",
        "lakeside-networks.com",
        "lakesidenetworks.uk",
        "lakeside-networks.uk",
    ]
    assert candidate_domains("Ltd") == []
    assert generic_addresses(CONTACT, "lakesidenetworks.co.uk") == ["sales@lakesidenetworks.co.uk"]
    assert states_number("Company No. 1234567", NUMBER) and states_number(FOOTER, NUMBER)
    assert not states_number("Ref 101234567", NUMBER) and not states_number("nothing", NUMBER)


def test_a_site_stating_the_number_gives_its_generic_address_only() -> None:
    seen: list[str] = []
    f = finder({"lakesidenetworks.co.uk/": "<p>Welcome</p>", "lakesidenetworks.co.uk/contact": CONTACT}, seen)
    found = f.find(NUMBER, "Lakeside Networks Ltd", None)
    assert found is not None
    assert (found.domain, found.address) == ("lakesidenetworks.co.uk", "sales@lakesidenetworks.co.uk")
    assert found.page == "https://lakesidenetworks.co.uk/contact"
    assert seen[0] == "https://lakesidenetworks.co.uk/robots.txt"
    assert "dana.reyes" not in repr(found)  # personal addresses are never taken
    assert not any("/about" in u for u in seen)  # stops as soon as it has both


def test_without_the_number_the_site_is_not_accepted() -> None:
    impostor = CONTACT.replace("01234567", "07777777")
    f = finder({"lakesidenetworks.co.uk/contact": impostor, "lakeside-networks.co.uk/contact": impostor})
    assert f.find(NUMBER, "Lakeside Networks Ltd", None) is None


def test_robots_txt_is_respected() -> None:
    pages = {
        "lakesidenetworks.co.uk/robots.txt": "User-agent: *\nDisallow: /contact\n",
        "lakesidenetworks.co.uk/contact": CONTACT,
        "lakesidenetworks.co.uk/": "<footer>" + FOOTER + "</footer>",
    }
    seen: list[str] = []
    found = finder(pages, seen).check_site("lakesidenetworks.co.uk", NUMBER)
    assert not any(u.endswith("/contact") for u in seen)
    assert found is not None and found.address is None  # verified, but the address page is off limits
    blocked = {**pages, "lakesidenetworks.co.uk/robots.txt": httpx.Response(403)}
    seen2: list[str] = []
    assert finder(blocked, seen2).check_site("lakesidenetworks.co.uk", NUMBER) is None
    assert seen2 == ["https://lakesidenetworks.co.uk/robots.txt"]


def test_redirects_stay_on_the_domain_and_private_hosts_are_refused() -> None:
    pages = {
        "lakesidenetworks.co.uk/contact": httpx.Response(
            301, headers={"location": "https://www.lakesidenetworks.co.uk/contact"}
        ),
        "www.lakesidenetworks.co.uk/contact": CONTACT,
        "lakesidenetworks.co.uk/about": httpx.Response(302, headers={"location": "https://evil.example/about"}),
    }
    found = finder(pages).check_site("lakesidenetworks.co.uk", NUMBER)
    assert found is not None and found.address == "sales@lakesidenetworks.co.uk"
    seen: list[str] = []
    internal = finder({"lakesidenetworks.co.uk/contact": CONTACT}, seen, public_host=lambda h: False)
    assert internal.check_site("lakesidenetworks.co.uk", NUMBER) is None and seen == []
    assert finder({}).check_site("not a domain", NUMBER) is None


def test_wikidata_website_is_tried_first_and_no_mx_means_no_address() -> None:
    wd_seen: list[httpx.Request] = []

    def wd(req: httpx.Request) -> httpx.Response:
        wd_seen.append(req)
        return httpx.Response(
            200, json={"results": {"bindings": [{"w": {"value": "https://www.lakeside-net.example/"}}]}}
        )

    contact = CONTACT.replace("lakesidenetworks.co.uk", "lakeside-net.example")
    f = finder({"lakeside-net.example/contact": contact}, wikidata=httpx.MockTransport(wd))
    found = f.find(NUMBER, "Lakeside Networks Ltd", None)
    assert found is not None and found.domain == "lakeside-net.example"
    assert '"01234567"' in wd_seen[0].url.params["query"] and "P2622" in wd_seen[0].url.params["query"]
    assert "PipelineOS" in wd_seen[0].headers["user-agent"]
    no_mx = finder({"lakeside-net.example/contact": contact}, wikidata=httpx.MockTransport(wd), mx=lambda d: False)
    found2 = no_mx.find(NUMBER, "Lakeside Networks Ltd", None)
    assert found2 is not None and found2.address is None


class Officers:
    name = "companies_house.officers"

    def find_people(
        self,
        company_domain: str,
        persona_titles: list[str],
        company_name: str | None = None,
        *,
        company_number: str | None = None,
    ) -> list[EnrichedPerson]:
        return [
            EnrichedPerson(
                "Dana Reyes",
                "Operations Director",
                company_domain,
                "GB",
                "England",
                None,
                None,
                f"companies_house:officers:{NUMBER}",
                0.9,
            )
        ]


def test_enrichment_adds_the_published_address_and_only_it_verifies() -> None:
    wc = WebsiteContacts(Officers(), finder({"lakesidenetworks.co.uk/contact": CONTACT}))
    [p] = wc.find_people("", ["Operations Director"], "Lakeside Networks Ltd", company_number=NUMBER)
    assert p.professional_email == "sales@lakesidenetworks.co.uk" and p.company_domain == "lakesidenetworks.co.uk"
    assert p.person_name == "Dana Reyes"
    assert p.data_source.startswith(f"companies_house:officers:{NUMBER}; email published at https://")
    assert wc.verify("SALES@lakesidenetworks.co.uk") == "valid"
    assert wc.verify("dana.reyes@lakesidenetworks.co.uk") == "unknown"  # never seen published as generic
    nothing = WebsiteContacts(Officers(), finder({}))
    [q] = nothing.find_people("", ["x"], "Lakeside Networks Ltd", company_number=NUMBER)
    assert q.professional_email is None  # no site found: the human "published contact" task, as before


def test_a_huge_page_is_read_only_up_to_the_cap() -> None:
    from app.adapters.website_contacts import MAX_BYTES

    huge = "<p>" + "x" * (MAX_BYTES * 3) + "</p><footer>" + FOOTER + " sales@lakesidenetworks.co.uk</footer>"
    found = finder({"lakesidenetworks.co.uk/": huge}).check_site("lakesidenetworks.co.uk", NUMBER)
    assert found is None  # the number sits beyond the cap: never read, so the site is not accepted


def test_a_published_address_only_verifies_if_its_domain_takes_email() -> None:
    from app.adapters.website_contacts import mail_domain_status

    dns = {
        ("ok.example", "MX"): ["mx1.ok.example"],
        ("nullmx.example", "MX"): [""],  # RFC 7505: explicitly accepts no mail
        ("aonly.example", "A"): ["192.0.2.1"],
    }

    def lookup(name: str, rdtype: str) -> list[str]:
        return dns.get((name, rdtype), [])

    assert mail_domain_status("ok.example", lookup) == "valid"
    assert mail_domain_status("nullmx.example", lookup) == "invalid"
    assert mail_domain_status("aonly.example", lookup) == "risky"  # the gate only sends to "valid"
    assert mail_domain_status("gone.example", lookup) == "invalid"
    wc = WebsiteContacts(Officers(), finder({"lakesidenetworks.co.uk/contact": CONTACT}), lookup)
    wc.find_people("", ["Operations Director"], "Lakeside Networks Ltd", company_number=NUMBER)
    assert wc.verify("sales@lakesidenetworks.co.uk") == "invalid"  # published, but the domain takes no mail
    dns[("lakesidenetworks.co.uk", "MX")] = ["mail.lakesidenetworks.co.uk"]
    assert wc.verify("Sales@LakesideNetworks.co.uk") == "valid"
