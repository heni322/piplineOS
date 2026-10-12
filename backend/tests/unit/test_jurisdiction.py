from __future__ import annotations

import pytest

from app.domain.compliance.jurisdiction import normalise_country_code, parse_location


@pytest.mark.parametrize(
    ("text", "country", "sub"),
    [
        ("Belfast, Northern Ireland", "GB", "GB-NIR"),
        ("Derry/Londonderry", "GB", "GB-NIR"),
        ("Lisburn, Co. Antrim, UK", "GB", "GB-NIR"),
        ("Belfast, NI", "GB", "GB-NIR"),
        ("N. Ireland", "GB", "GB-NIR"),
        ("Dublin", "IE", None),
        ("Cork, Republic of Ireland", "IE", None),
        ("Galway, Éire", "IE", None),
        ("London, United Kingdom", "GB", "GB-ENG"),
        ("Edinburgh", "GB", "GB-SCT"),
        ("Cardiff, Wales", "GB", "GB-WLS"),
        ("Remote, UK", "GB", None),
        ("München, Deutschland", "DE", None),
        ("Wien", "AT", None),
        ("Paris, France", "FR", None),
        ("GB-NIR", "GB", "GB-NIR"),
        ("ie", "IE", None),
        ("UK", "GB", None),
    ],
)
def test_known_locations(text: str, country: str, sub: str | None) -> None:
    r = parse_location(text)
    assert (r.country, r.subdivision, r.status) == (country, sub, "ok")


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("Ireland", "ambiguous"),  # island of Ireland: could be IE or GB-NIR -> never guess
        ("Bangor", "ambiguous"),  # Bangor, Co. Down (NI) vs Bangor, Gwynedd (Wales)
        ("Dublin and Belfast", "conflict"),
        ("London / Berlin", "conflict"),
        ("Dublin, NI", "conflict"),
        ("", "unknown"),
        (None, "unknown"),
        ("Atlantis", "unknown"),
    ],
)
def test_unsafe_locations_resolve_to_no_country(text: str | None, status: str) -> None:
    r = parse_location(text)
    assert r.country is None and r.status == status


def test_ireland_and_northern_ireland_are_never_the_same_jurisdiction() -> None:
    assert parse_location("Belfast").country == "GB"
    assert parse_location("Dublin").country == "IE"


def test_country_code_normalisation() -> None:
    assert normalise_country_code("uk") == ("GB", None)
    assert normalise_country_code("GB-SCT") == ("GB", "GB-SCT")
    assert normalise_country_code("XX") == (None, None)
    assert normalise_country_code(None) == (None, None)
