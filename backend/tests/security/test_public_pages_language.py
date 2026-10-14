"""Public pages (unsubscribe, reschedule) speak the visitor's language: French for a French browser, British English
otherwise. The English wording is unchanged. Escaping is applied once."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_unsubscribe_page_language_follows_the_browser() -> None:
    from app.main import create_app

    c = TestClient(create_app())
    en = c.get("/u/not-a-valid-token")
    assert en.status_code == 400 and '<html lang="en-GB">' in en.text
    assert (
        b"This unsubscribe link is not valid. Reply to the email with \\u201cunsubscribe\\u201d".decode(
            "unicode_escape"
        )
        in en.text
    )
    fr = c.get("/u/not-a-valid-token", headers={"Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5"})
    assert fr.status_code == 400 and '<html lang="fr-FR">' in fr.text
    assert "Lien non valide" in fr.text and "Ce lien de désinscription n&#x27;est pas valide" in fr.text
    assert "&amp;#x27;" not in fr.text  # escaped once, not twice
    r = c.get("/r/not-a-valid-token", headers={"Accept-Language": "fr"})
    assert r.status_code == 400 and "Ce lien n&#x27;est plus valide" in r.text
