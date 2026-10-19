"""France's BOAMP award notices (open data, no key): the request asks only for award notices that name a winner, since
the date; keywords match the French titles without regard to accents; each winner becomes one signal with a link to
BOAMP's own notice page; paging stops at the last page. Record shape as seen live on 2026-10-19, with fictional
companies. In-process transport only."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.adapters.signals.boamp import BoampAwardSource, fold

SINCE = datetime(2026, 10, 5, tzinfo=UTC)


def record(idweb: str, objet: str, winners: object, url: str | None = None) -> dict[str, object]:
    return {
        "idweb": idweb,
        "famille": "JOUE",
        "objet": objet,
        "nomacheteur": "Ville de Lyon",
        "dateparution": "2026-10-19",
        "titulaire": winners,
        "url_avis": url if url is not None else f"https://www.boamp.fr/pages/avis/?q=idweb:{idweb}",
    }


RECORDS = [
    record("26-100001", "Sécurisation du système d'information", ["EXEMPLE INFOGERANCE SAS", "AUTRE CONSEIL SARL",
                                                                   "EXEMPLE INFOGERANCE SAS"]),
    record("26-100002", "Nettoyage des bus", ["PROPRETE FICTIVE"]),
    record("26-100003", "Maintenance informatique des écoles - lot 2 imprimantes", ["IMPRIM FICTIF"]),
    record("26-100004", "Infogérance du parc informatique", "SEUL TITULAIRE SAS", url="javascript:alert(1)"),
]  # fmt: skip


def source(records: list[dict[str, object]], seen: list[httpx.Request], page_size: int = 100) -> BoampAwardSource:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        offset = int(req.url.params["offset"])
        return httpx.Response(200, json={"total_count": len(records), "results": records[offset : offset + page_size]})

    return BoampAwardSource(transport=httpx.MockTransport(handler), page_size=page_size)


def test_request_asks_only_for_awards_with_a_winner_since_the_date() -> None:
    seen: list[httpx.Request] = []
    source(RECORDS, seen).fetch("informatique", SINCE)
    p = seen[0].url.params
    assert seen[0].url.host == "boamp-datadila.opendatasoft.com" and seen[0].url.path.endswith("/boamp/records")
    assert p["where"] == "nature=\"ATTRIBUTION\" and dateparution>=date'2026-10-05' and titulaire is not null"
    assert p["order_by"] == "dateparution desc" and p["limit"] == "100" and p["offset"] == "0"
    assert "titulaire" in p["select"] and "contact" not in p["select"]  # no contact points requested


def test_keywords_match_french_titles_without_accents_and_each_winner_is_a_signal() -> None:
    sigs = source(RECORDS, []).fetch("securite; systeme d'information; informatique; -imprimantes", SINCE)
    assert [s.company_name for s in sigs] == ["EXEMPLE INFOGERANCE SAS", "AUTRE CONSEIL SARL", "SEUL TITULAIRE SAS"]
    first = sigs[0]
    assert first.signal_type == "tender" and first.country_hint == "France"
    assert first.title == "Awarded: Sécurisation du système d'information"
    assert first.text == ("Ville de Lyon awarded the contract 'Sécurisation du système d'information' to "
                          "EXEMPLE INFOGERANCE SAS (BOAMP notice 26-100001).")  # fmt: skip
    assert first.source_url == "https://www.boamp.fr/pages/avis/?q=idweb:26-100001"
    assert first.detected_at == datetime(2026, 10, 19, tzinfo=UTC)
    assert [s.source_record_id for s in sigs] == ["boamp:26-100001:0", "boamp:26-100001:1", "boamp:26-100004:0"]
    assert sigs[2].source_url == "https://www.boamp.fr/pages/avis/?q=idweb:26-100004"  # never a javascript: link


def test_paging_stops_at_the_last_page_and_no_keywords_means_no_request() -> None:
    seen: list[httpx.Request] = []
    sigs = source(RECORDS, seen, page_size=3).fetch("informatique", SINCE)
    assert [r.url.params["offset"] for r in seen] == ["0", "3"]  # 4 records, pages of 3: the second is the last
    assert [s.company_name for s in sigs] == ["IMPRIM FICTIF", "SEUL TITULAIRE SAS"]
    seen.clear()
    assert source(RECORDS, seen).fetch("-informatique", SINCE) == [] and seen == []


def test_fold() -> None:
    assert fold("Sécurité Informatique") == "securite informatique" and fold("ÉCOLE") == "ecole"
