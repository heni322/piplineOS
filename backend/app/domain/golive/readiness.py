"""Go-live readiness: everything that must be true before real sending, checked automatically and shown on the
Go-live page. It only READS state; it never signs anything off or switches anything on. The sign-offs stay the
owner's own action (the go-live checklist, MFA).

Each item: key, title, state ("done" | "todo" | "info"), who acts ("you" | "system"), detail in plain words."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.settings import Settings


@dataclass(frozen=True)
class Item:
    key: str
    title: str
    state: str
    who: str
    detail: str


def _secret(settings: Settings, name: str) -> bool:
    v = getattr(settings, name, None)
    return bool(v is not None and (v.get_secret_value() if hasattr(v, "get_secret_value") else v))


def readiness(conn: Connection, settings: Settings) -> list[dict[str, str]]:
    """Operator context (all clients)."""
    out: list[Item] = []

    sup = conn.execute(text("SELECT placeholder FROM supplier_profiles ORDER BY version DESC LIMIT 1")).scalar()
    out.append(
        Item(
            "company_details",
            "Your company details",
            "done" if sup is False else "todo",
            "you",
            "Complete" if sup is False else "Fill in config/supplier.yaml (legal name, number, address, bank, email)",
        )
    )

    gb = conn.execute(
        text("""SELECT r.enabled, coalesce(c.verified_by_counsel, false) AS counsel
                FROM current_country_rollout r LEFT JOIN current_compliance_rules c ON c.country_code = r.country_code
                WHERE r.country_code = 'GB'""")
    ).first()
    counsel = bool(gb and gb.counsel)
    out.append(
        Item(
            "uk_rules_reviewed",
            "UK rules reviewed by your solicitor",
            "done" if counsel else "todo",
            "you",
            "Recorded" if counsel else "Record the solicitor's review on this page (Countries), then enable GB",
        )
    )
    out.append(
        Item(
            "uk_enabled",
            "UK enabled for sending",
            "done" if (gb and gb.enabled and counsel) else "todo",
            "you",
            "Enabled after review"
            if (gb and gb.enabled and counsel)
            else ("Enabled without a recorded review (demo only)" if gb and gb.enabled else "Not enabled"),
        )
    )

    tax = conn.execute(
        text("""SELECT count(*) FILTER (WHERE verified_by_accountant) AS ok, count(*) AS n
                FROM (SELECT DISTINCT ON (code) verified_by_accountant FROM tax_rules ORDER BY code, version DESC) t""")
    ).one()
    out.append(
        Item(
            "tax_rules",
            "Tax rules checked by your accountant",
            "done" if tax.n and tax.ok == tax.n else "todo",
            "you",
            f"{tax.ok} of {tax.n} rules recorded as checked",
        )
    )

    dom = conn.execute(
        text("""SELECT count(*) AS n, count(*) FILTER (WHERE spf_ok AND dkim_ok AND dmarc_ok) AS ok,
                       count(*) FILTER (WHERE dns_checked_at IS NULL) AS unchecked
                FROM sending_domains d JOIN tenants t ON t.id = d.tenant_id WHERE t.status = 'active'""")
    ).one()
    out.append(
        Item(
            "sending_domains",
            "Sending domains pass SPF, DKIM and DMARC",
            "done" if dom.n and dom.ok == dom.n else "todo",
            "you",
            f"{dom.ok} of {dom.n} verified"
            + (f"; {dom.unchecked} never checked" if dom.unchecked else "")
            + " (Inboxes page: Check DNS)",
        )
    )

    inb = conn.execute(
        text("""SELECT count(*) FILTER (WHERE i.status = 'active') AS active,
                       count(*) FILTER (WHERE i.status = 'warming') AS warming,
                       count(*) FILTER (WHERE i.status = 'paused') AS paused
                FROM inboxes i JOIN tenants t ON t.id = i.tenant_id WHERE t.status = 'active'""")
    ).one()
    out.append(
        Item(
            "inboxes_warmed",
            "Sending inboxes warmed up",
            "done" if inb.active and not inb.warming else ("info" if inb.warming else "todo"),
            "system",
            f"{inb.active} active, {inb.warming} warming, {inb.paused} paused. Warm-up only advances on days with "
            "real sends, so it starts after go-live at low volume",
        )
    )

    needed = []
    if settings.email_adapter_mode == "live" and not (
        _secret(settings, "msgraph_client_secret") or _secret(settings, "google_service_account_json")
    ):
        needed.append("Microsoft 365 or Google credentials")
    if settings.llm_adapter_mode == "live" and not _secret(settings, "anthropic_api_key"):
        needed.append("Anthropic API key")
    if "live" in (settings.enrichment_adapter_mode, settings.verifier_adapter_mode) and not _secret(
        settings, "hunter_api_key"
    ):
        needed.append("Hunter API key")
    if settings.companies_house_adapter_mode != "mock" and not _secret(settings, "companies_house_api_key"):
        needed.append("Companies House API key")
    live_modes = [
        k
        for k in ("email", "calendar", "crm", "enrichment", "verifier", "llm")
        if getattr(settings, f"{k}_adapter_mode") == "live"
    ]
    out.append(
        Item(
            "providers",
            "Real providers configured",
            "todo" if needed or not live_modes else "done",
            "you",
            ("Missing: " + ", ".join(needed))
            if needed
            else ("Live: " + ", ".join(live_modes))
            if live_modes
            else "All providers still on demo/test data (expected until you switch on)",
        )
    )

    onboarded = conn.execute(
        text(
            "SELECT count(*) FROM tenants WHERE status = 'active' AND lia_approved_at IS NOT NULL "
            "AND dpa_status = 'signed'"
        )
    ).scalar()
    out.append(
        Item(
            "pilot_client",
            "A pilot client is onboarded",
            "done" if onboarded else "todo",
            "you",
            f"{onboarded} active client(s) with a legitimate-interest assessment and a signed DPA",
        )
    )

    signed, total = conn.execute(
        text("SELECT count(signed_off_at), count(*) FROM golive_checklist_items WHERE scope = 'uk'")
    ).one()
    out.append(
        Item(
            "checklist",
            "Go-live checklist signed off",
            "done" if total and signed == total else "todo",
            "you",
            f"{signed} of {total} items signed",
        )
    )
    out.append(
        Item(
            "switch",
            "Server switch GLOBAL_GO_LIVE",
            "done" if settings.global_go_live else "todo",
            "you",
            "On" if settings.global_go_live else "Off. Turn it on last, after everything above",
        )
    )
    return [asdict(i) for i in out]
