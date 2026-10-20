"""Recruitee, Workable and SmartRecruiters job boards (public careers-page endpoints, no key), in the shapes seen live
on 2026-10-19 with fictional companies: the right endpoint per board (Recruitee on the employer's own sub-domain),
new roles since the date matched by the hiring keywords, unpublished or private postings left out, and board names
that could change the host refused."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.adapters.signals.job_boards import JobBoardSource, WatchedBoard, parse_board

SINCE = datetime(2026, 10, 1, tzinfo=UTC)

RECRUITEE = {
    "offers": [
        {
            "id": 1,
            "title": "Technicien support informatique",
            "department": "IT",
            "location": "Lyon, France",
            "published_at": "2026-10-15 09:31:36 UTC",
            "status": "published",
            "careers_url": "https://careers.exemple.fr/o/1",
        },
        {
            "id": 2,
            "title": "Technicien réseau (brouillon)",
            "department": "IT",
            "published_at": "2026-10-16 09:00:00 UTC",
            "status": "draft",
        },
        {
            "id": 3,
            "title": "Technicien support niveau 2",
            "department": "IT",
            "published_at": "2026-09-01 09:00:00 UTC",
            "status": "published",
        },
    ]
}
WORKABLE = {
    "name": "Exemple Cloud",
    "jobs": [
        {
            "title": "Ingénieur DevOps",
            "shortcode": "AB12",
            "department": "Infrastructure",
            "city": "Paris",
            "country": "France",
            "published_on": "2026-10-12",
            "url": "https://apply.workable.com/j/AB12",
        },
        {"title": "Office Manager", "shortcode": "CD34", "published_on": "2026-10-12"},
    ],
}
SMART = {"content": [
    {"id": "7001", "name": "Service Desk Analyst", "company": {"identifier": "ExempleGroup", "name": "Exemple Group"},
     "releasedDate": "2026-10-19T17:16:58.488Z", "location": {"fullLocation": "Leeds, , United Kingdom"},
     "department": {}, "function": {"label": "Information Technology"}, "visibility": "PUBLIC"},
    {"id": "7002", "name": "Service Desk Lead (internal)", "company": {"identifier": "ExempleGroup"},
     "releasedDate": "2026-10-19T10:00:00Z", "visibility": "INTERNAL"},
]}  # fmt: skip


def test_three_more_boards_give_hiring_signals() -> None:
    seen: list[str] = []

    def answer(data: object):  # type: ignore[no-untyped-def]
        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(str(req.url))
            return httpx.Response(200, json=data)

        return httpx.MockTransport(handler)

    src = JobBoardSource(
        [
            WatchedBoard("Exemple Infogérance", "recruitee:exemple-infogerance", country_hint="France"),
            WatchedBoard("Exemple Cloud", "workable:exemple-cloud"),
            WatchedBoard("Exemple Group", "smartrecruiters:ExempleGroup"),
        ],
        transports={"recruitee": answer(RECRUITEE), "workable": answer(WORKABLE), "smartrecruiters": answer(SMART)},
    )
    out = {s.company_name: s for s in src.fetch("technicien; devops; service desk", SINCE)}
    assert seen == [
        "https://exemple-infogerance.recruitee.com/api/offers/",
        "https://apply.workable.com/api/v1/widget/accounts/exemple-cloud",
        "https://api.smartrecruiters.com/v1/companies/ExempleGroup/postings?limit=100",
    ]
    r = out["Exemple Infogérance"]
    assert "Technicien support informatique (IT), Lyon, France" in r.text and "brouillon" not in r.text
    assert "niveau 2" not in r.text  # published before the date
    assert r.source_url == "https://careers.exemple.fr/o/1" and r.detected_at == datetime(2026, 10, 15, 9, 31, 36,
                                                                                         tzinfo=UTC)  # fmt: skip
    w = out["Exemple Cloud"]
    assert "Ingénieur DevOps (Infrastructure), Paris, France" in w.text and "Office Manager" not in w.text
    s = out["Exemple Group"]
    assert "Service Desk Analyst (Information Technology)" in s.text and "internal" not in s.text
    assert s.source_url == "https://jobs.smartrecruiters.com/ExempleGroup/7001"


def test_board_names_that_could_change_the_host_are_refused() -> None:
    assert parse_board("recruitee:exemple-infogerance") == ("recruitee", "exemple-infogerance")
    assert parse_board("workable:huggingface") == ("workable", "huggingface")
    for bad in ("recruitee:evil.example", "recruitee:a_b", "recruitee:é", "smartrecruiters:../x"):
        assert parse_board(bad) is None
