"""Sending problems: emails that failed, bounced, were blocked at send time, or are held back for a reason the operator
must act on. Read-only; it explains each one in plain words and says what to do. Shown in the operator console (an
alert on every page, and the list on the Checks page).

Nothing personal is shown: the company name (hidden for sole traders), the client and the campaign, never the
recipient's address. The reason codes come from the outbound gateway (`app.domain.outbound.gateway`), the compliance
engine and the email providers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

Severity = Literal["error", "warning"]

# Statuses that always mean "this email did not go out as it should".
FAILED = ("failed_retryable", "failed_permanent", "needs_reconciliation", "bounced", "complained", "blocked_at_send")
# A queued/approved email held back with one of these reasons needs the operator (the others resolve by themselves).
HELD_ERRORS = {"INBOX_OAUTH_INVALID", "INBOX_UNAVAILABLE"}

STATUS_TEXT: dict[str, tuple[str, str, Severity]] = {
    # status: (what happened, what to do, severity)
    "failed_retryable": (
        "The email provider refused or did not answer (temporary error). It will be tried again.",
        "If it keeps failing, run all checks: the mailbox connection or the provider may be down.",
        "error",
    ),
    "failed_permanent": (
        "The email provider refused this email for good.",
        "Look at the reason below; check the mailbox and the sending domain, then write a new email if needed.",
        "error",
    ),
    "needs_reconciliation": (
        "Sending started but no confirmation came back (crash or timeout). It is NOT resent automatically, so the "
        "person is never emailed twice.",
        "Check the mailbox's Sent folder: if it was sent, nothing to do; if not, approve a new email.",
        "error",
    ),
    "bounced": (
        "The recipient's server rejected the email (address does not exist or mailbox full).",
        "Nothing to resend: the address is suppressed. Many bounces mean the address source needs checking.",
        "error",
    ),
    "complained": (
        "The recipient marked the email as spam.",
        "The address is suppressed. Review the campaign's wording and targeting before sending more.",
        "error",
    ),
    "blocked_at_send": (
        "The final check just before sending stopped this email.",
        "See the reason below. This is a safety check working; fix the cause if the email should go out.",
        "warning",
    ),
    "held": (
        "This email is waiting and cannot go out yet.",
        "See the reason below.",
        "warning",
    ),
}

REASON_TEXT: dict[str, tuple[str, str]] = {
    # reason code: (explanation, what to do)
    "INBOX_OAUTH_INVALID": (
        "The mailbox's connection to Microsoft 365 / Google has expired or was revoked.",
        "Reconnect the mailbox on the Inboxes page.",
    ),
    "INBOX_UNAVAILABLE": (
        "No active mailbox is available for this campaign (none set, or it is paused).",
        "Add or resume a mailbox on the Inboxes page.",
    ),
    "CLIENT_PAUSED": ("The client is paused (emergency stop).", "Resume the client when ready."),
    "CAMPAIGN_PAUSED": ("The campaign is paused.", "Resume the campaign when ready."),
    "PILOT_LIMIT_REACHED": (
        "The campaign reached its pilot limit of new people.",
        "Raise the limit on the Campaigns page if you want to continue.",
    ),
    "PROSPECT_HAS_NO_EMAIL": ("No email address is known for this person.", "Nothing to send; no action needed."),
    "RECIPIENT_MISMATCH": (
        "The address changed after the email was approved.",
        "Review and approve a new email for this person.",
    ),
    "TIMEZONE_UNKNOWN": (
        "The recipient's country or time zone is unknown, so business hours cannot be respected.",
        "Complete the person's location, or leave it: nothing is sent without it.",
    ),
    "FOOTER_INVALID": (
        "The legal footer (company details, unsubscribe link) is incomplete.",
        "Complete the client's company details and privacy notice link.",
    ),
    "OUTSIDE_BUSINESS_HOURS": ("Waiting for the recipient's business hours.", "No action needed."),
    "CAMPAIGN_DAILY_CAP": ("The campaign's daily limit is reached; it continues tomorrow.", "No action needed."),
    "SUPPRESSED_GLOBAL": ("The address is on the do-not-contact list.", "No action: it must not be emailed."),
    "SUPPRESSED_TENANT": ("The address is on this client's do-not-contact list.", "No action: it must not be emailed."),
    "COUNTRY_OUTSIDE_EUROPE": (
        "The person or company is outside Europe.",
        "No action: PipelineOS works only in Europe.",
    ),
    "COUNTRY_UNKNOWN": ("The person's or company's country is unknown.", "Complete the location if it is known."),
    "COUNTRY_NOT_ENABLED": (
        "The country is not enabled yet.",
        "Enable it on the Go-live page once its rules are verified.",
    ),
    "EMAIL_NOT_VERIFIED": ("The address is not verified as deliverable.", "Verify the address before sending."),
    "TENANT_NO_LIA": ("The client's legitimate interests assessment is not approved.", "Record the LIA."),
    "TENANT_NO_DPA": ("The client's data processing agreement is not signed.", "Record the DPA."),
    "RetryableProviderError": ("The email provider answered with a temporary error.", "It will be tried again."),
    "RateLimited": (
        "The email provider is limiting how fast this mailbox can send.",
        "It will be tried again; if it lasts, lower the mailbox's daily limit.",
    ),
}


@dataclass(frozen=True)
class Problem:
    id: str
    client: str
    campaign: str
    company: str
    status: str
    severity: Severity
    what: str
    fix: str
    reasons: list[dict[str, str]]
    when: datetime


def explain(status: str, last_error: str | None) -> tuple[Severity, str, str, list[dict[str, str]]]:
    """(severity, what happened, what to do, one explanation per reason code). Unknown codes are shown as given."""
    key = status if status in STATUS_TEXT else "held"
    what, fix, severity = STATUS_TEXT[key]
    codes = [c.strip() for c in (last_error or "").split(",") if c.strip()]
    reasons = []
    for code in codes:
        expl, todo = REASON_TEXT.get(code, (code.replace("_", " ").capitalize(), ""))
        reasons.append({"code": code, "text": expl, "fix": todo})
    if key == "held" and any(c in HELD_ERRORS for c in codes):
        severity = "error"
    return severity, what, fix, reasons


def _where(days: int) -> str:
    statuses = ", ".join(f"'{s}'" for s in FAILED)
    held = ", ".join(f"'{c}'" for c in HELD_ERRORS)
    return f"""m.updated_at > now() - make_interval(days => {int(days)}) AND m.kind = 'outreach' AND (
        m.status IN ({statuses})
        OR (m.status IN ('queued', 'approved') AND m.last_error IS NOT NULL
            AND string_to_array(m.last_error, ',') && ARRAY[{held}]::text[]))"""


def sending_problems(conn: Connection, *, days: int = 30, limit: int = 100) -> list[dict[str, object]]:
    """Newest first, operator context (every client)."""
    rows = conn.execute(
        text(f"""SELECT m.id, m.status, m.last_error, m.updated_at, t.legal_name AS client, cp.name AS campaign,
                   CASE WHEN co.name_is_personal THEN NULL ELSE co.name END AS company
                 FROM messages m
                 JOIN tenants t ON t.id = m.tenant_id
                 JOIN campaigns cp ON cp.id = m.campaign_id
                 JOIN leads l ON l.id = m.lead_id
                 JOIN companies co ON co.id = l.company_id
                 WHERE {_where(days)}
                 ORDER BY m.updated_at DESC LIMIT :n"""),  # noqa: S608 - only constants and an int are formatted in
        {"n": limit},
    ).mappings()
    out = []
    for r in rows:
        severity, what, fix, reasons = explain(r["status"], r["last_error"])
        out.append(
            asdict(
                Problem(
                    id=str(r["id"]),
                    client=r["client"],
                    campaign=r["campaign"],
                    company=r["company"] or "(name hidden: sole trader)",
                    status=r["status"],
                    severity=severity,
                    what=what,
                    fix=fix,
                    reasons=reasons,
                    when=r["updated_at"],
                )
            )
        )
    return out


def error_count(conn: Connection, *, days: int = 7) -> int:
    """Emails in an error state recently: what the console's alert counts (warnings are not counted)."""
    return sum(1 for p in sending_problems(conn, days=days, limit=500) if p["severity"] == "error")
