"""Builds the real provider objects for the outbound package from settings. Lives here because only
app.domain.outbound may import side-effecting adapters (import contract). Building an object sends nothing:
whether anything is sent is still decided per call by the three-key switch.

Each inbox says which provider it belongs to (Microsoft 365 or Google Workspace); the live sender routes on that."""

from __future__ import annotations

from app.adapters.calendar.base import CalendarAdapter
from app.adapters.calendar.google import GoogleCalendar
from app.adapters.calendar.msgraph import GraphCalendar
from app.adapters.email.base import EmailPayload, EmailSender, SendResult
from app.adapters.email.gmail import GmailSender
from app.adapters.email.msgraph import GraphEmailSender
from app.adapters.email.sink import SinkSmtpSender
from app.adapters.google_auth import GoogleCredentials
from app.adapters.msgraph_auth import GraphCredentials
from app.core.crypto import PiiCipher
from app.domain.outbound.calendar import CalendarGateway
from app.settings import Settings

PROVIDERS = ("msgraph", "google")


class ProviderNotConfigured(RuntimeError):
    pass


def graph_credentials(settings: Settings) -> GraphCredentials:
    if not (settings.msgraph_tenant_id and settings.msgraph_client_id and settings.msgraph_client_secret):
        raise ProviderNotConfigured("Microsoft Graph credentials are not set (MSGRAPH_TENANT_ID/CLIENT_ID/SECRET)")
    return GraphCredentials(
        settings.msgraph_tenant_id, settings.msgraph_client_id, settings.msgraph_client_secret.get_secret_value()
    )


def google_credentials(settings: Settings) -> GoogleCredentials:
    if not settings.google_service_account_json:  # unset or empty
        raise ProviderNotConfigured("Google service account is not set (GOOGLE_SERVICE_ACCOUNT_JSON)")
    return GoogleCredentials(settings.google_service_account_json.get_secret_value())


def configured_providers(settings: Settings) -> list[str]:
    out = []
    if settings.msgraph_tenant_id and settings.msgraph_client_id and settings.msgraph_client_secret:
        out.append("msgraph")
    if settings.google_service_account_json:
        out.append("google")
    return out


class RoutingEmailSender:
    """Sends through the provider of the sending inbox. An inbox whose provider isn't configured raises before any
    provider is called (the message then goes to reconciliation, never an automatic resend)."""

    name = "email.routing"

    def __init__(self, senders: dict[str, EmailSender]) -> None:
        self.senders = senders

    def send(self, payload: EmailPayload) -> SendResult:
        sender = self.senders.get(payload.provider)
        if sender is None:
            raise ProviderNotConfigured(f"no live sender for inbox provider {payload.provider!r}")
        return sender.send(payload)


def email_senders(settings: Settings) -> tuple[EmailSender | None, EmailSender | None]:
    """(live, sandbox). Live is only built when the email adapter itself is set to live."""
    live: EmailSender | None = None
    if settings.email_adapter_mode == "live":
        available = configured_providers(settings)
        if not available:
            raise ProviderNotConfigured("live email needs Microsoft Graph or Google Workspace credentials")
        senders: dict[str, EmailSender] = {}
        if "msgraph" in available:
            senders["msgraph"] = GraphEmailSender(graph_credentials(settings))
        if "google" in available:
            senders["google"] = GmailSender(google_credentials(settings))
        live = RoutingEmailSender(senders)
    sandbox = (
        SinkSmtpSender(settings.sandbox_smtp_host, settings.sandbox_smtp_port)
        if settings.email_adapter_mode == "sandbox"
        else None
    )
    return live, sandbox


def calendar_gateway(
    settings: Settings, cipher: PiiCipher, mailbox: str | None = None, provider: str = "msgraph"
) -> CalendarGateway:
    """Reads availability from the real calendar in sandbox/live mode; creates events for real only in live mode."""
    if settings.calendar_adapter_mode == "mock" or mailbox is None or provider not in PROVIDERS:
        return CalendarGateway(settings, cipher)
    real: CalendarAdapter = (
        GoogleCalendar(google_credentials(settings), mailbox)
        if provider == "google"
        else GraphCalendar(graph_credentials(settings), mailbox)
    )
    return CalendarGateway(
        settings, cipher, adapter=real, live_adapter=real if settings.calendar_adapter_mode == "live" else None
    )
