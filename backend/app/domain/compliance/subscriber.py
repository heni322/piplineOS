"""Deterministic subscriber-type classification (PECR corporate vs individual subscriber)."""

from __future__ import annotations

from typing import Literal

SubscriberType = Literal["corporate", "individual", "unknown"]

CORPORATE_FORMS = frozenset({"ltd", "plc", "llp", "public_body", "charity", "other_corporate"})
INDIVIDUAL_FORMS = frozenset({"sole_trader", "partnership", "lp"})

FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "hotmail.co.uk",
        "live.com",
        "live.co.uk",
        "yahoo.com",
        "yahoo.co.uk",
        "icloud.com",
        "me.com",
        "aol.com",
        "btinternet.com",
        "sky.com",
        "virginmedia.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "gmx.de",
        "web.de",
        "orange.fr",
        "free.fr",
        "laposte.net",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "talktalk.net",
        "eircom.net",
        # Nordic consumer mail
        "online.no",
        "hotmail.no",
        "live.no",
        "getmail.no",
        "frisurf.no",
        "start.no",
        "luukku.com",
        "suomi24.fi",
        "kolumbus.fi",
        "saunalahti.fi",
        "elisanet.fi",
        "welho.com",
        "hotmail.fi",
        "live.fi",
        "hotmail.se",
        "live.se",
        "spray.se",
        "bredband.net",
        "comhem.se",
        "hotmail.dk",
        "live.dk",
        "jubii.dk",
        "mail.dk",
        "ofir.dk",
        "stofanet.dk",
        "yahoo.dk",
        # Spain, Italy, Portugal, Belgium consumer mail
        "hotmail.es",
        "yahoo.es",
        "telefonica.net",
        "libero.it",
        "virgilio.it",
        "alice.it",
        "tiscali.it",
        "hotmail.it",
        "yahoo.it",
        "sapo.pt",
        "hotmail.com.pt",
        "clix.pt",
        "skynet.be",
        "telenet.be",
        "hotmail.be",
        "proximus.be",
    }
)


def classify_subscriber(
    legal_form: str | None, email_domain: str | None, registry_matched: bool
) -> tuple[SubscriberType, str]:
    """Return (type, reason). Anything uncertain is 'unknown', which the engine blocks."""
    if email_domain and email_domain.lower() in FREE_MAIL_DOMAINS:
        return "individual", "free_mail_domain"
    form = (legal_form or "unknown").lower()
    if form in INDIVIDUAL_FORMS:
        return "individual", f"legal_form:{form}"
    if form in CORPORATE_FORMS:
        if not registry_matched:
            return "unknown", f"legal_form:{form}_not_registry_confirmed"
        return "corporate", f"registry:{form}"
    return "unknown", "legal_form_unknown"
