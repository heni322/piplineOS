"""Builds the real provider objects for the outbound package from settings. Lives here because only
app.domain.outbound may import side-effecting adapters (import contract). Building an object sends nothing:
whether anything is sent is still decided per call by the three-key switch."""

from __future__ import annotations

from app.adapters.calendar.base import CalendarAdapter
from app.adapters.calendar.msgraph import GraphCalendar
from app.adapters.email.base import EmailSender
from app.adapters.email.msgraph import GraphEmailSender
from app.adapters.email.sink import SinkSmtpSender
from app.adapters.msgraph_auth import GraphCredentials
from app.core.crypto import PiiCipher
from app.domain.outbound.calendar import CalendarGateway
from app.settings import Settings


class ProviderNotConfigured(Exception):
    pass


def graph_credentials(settings: Settings) -> GraphCredentials:
    if not (settings.msgraph_tenant_id and settings.msgraph_client_id and settings.msgraph_client_secret):
        raise ProviderNotConfigured("Microsoft Graph credentials are not set (MSGRAPH_TENANT_ID/CLIENT_ID/SECRET)")
    return GraphCredentials(
        settings.msgraph_tenant_id, settings.msgraph_client_id, settings.msgraph_client_secret.get_secret_value()
    )


def email_senders(settings: Settings) -> tuple[EmailSender | None, EmailSender | None]:
    """(live, sandbox). Live is only built when the email adapter itself is set to live."""
    live = GraphEmailSender(graph_credentials(settings)) if settings.email_adapter_mode == "live" else None
    sandbox = (
        SinkSmtpSender(settings.sandbox_smtp_host, settings.sandbox_smtp_port)
        if settings.email_adapter_mode == "sandbox"
        else None
    )
    return live, sandbox


def calendar_gateway(settings: Settings, cipher: PiiCipher, mailbox: str | None = None) -> CalendarGateway:
    """Reads availability from the real calendar in sandbox/live mode; creates events for real only in live mode."""
    if settings.calendar_adapter_mode == "mock" or mailbox is None:
        return CalendarGateway(settings, cipher)
    real: CalendarAdapter = GraphCalendar(graph_credentials(settings), mailbox)
    return CalendarGateway(
        settings, cipher, adapter=real, live_adapter=real if settings.calendar_adapter_mode == "live" else None
    )
