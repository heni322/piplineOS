"""Contract tests for the real provider adapters (Companies House, Microsoft Graph). Every request goes to an
in-process httpx.MockTransport: these tests never touch the network."""

from __future__ import annotations

import base64
import email
import json
from datetime import UTC, datetime, timedelta
from email import policy
from typing import Any

import httpx
import pytest

from app.adapters.base import RateLimited, RetryableProviderError
from app.adapters.calendar.base import Slot
from app.adapters.calendar.msgraph import GraphCalendar, transaction_id
from app.adapters.companies_house.live import CompaniesHouseRegistry, normalise_name
from app.adapters.email.base import EmailPayload
from app.adapters.email.msgraph import GraphEmailSender, build_mime, message_id
from app.adapters.email.sink import SinkSmtpSender
from app.adapters.http import ProviderError
from app.adapters.inbound.msgraph import GraphInbox, client_state, mailbox_for, renew_all, subscribe
from app.adapters.msgraph_auth import GraphCredentials


class Recorder:
    def __init__(self, routes: dict[tuple[str, str], Any]) -> None:
        self.routes, self.calls = routes, []  # type: ignore[var-annotated]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        key = (req.method, req.url.path)
        handler = self.routes.get(key)
        if handler is None:
            return httpx.Response(404, json={"error": "no route", "path": req.url.path})
        return handler(req) if callable(handler) else httpx.Response(200, json=handler)


# --------------------------------------------------------------------------------------------- Companies House
def ch(routes: dict[tuple[str, str], Any]) -> tuple[CompaniesHouseRegistry, Recorder]:
    rec = Recorder(routes)
    return CompaniesHouseRegistry("test-key", transport=httpx.MockTransport(rec)), rec


SEARCH = {
    "items": [
        {"title": "CONTOSO MANAGED IT LTD", "company_number": "01234567", "company_status": "active"},
        {"title": "CONTOSO MANAGED IT HOLDINGS LIMITED", "company_number": "07654321", "company_status": "active"},
    ]
}


def test_companies_house_exact_match_maps_legal_form_and_uses_basic_auth() -> None:
    reg, rec = ch(
        {
            ("GET", "/search/companies"): SEARCH,
            ("GET", "/company/01234567"): {
                "company_name": "CONTOSO MANAGED IT LTD",
                "type": "ltd",
                "company_status": "active",
                "jurisdiction": "england-wales",
            },
        }
    )
    r = reg.lookup("Contoso Managed IT Limited")
    assert r is not None and (r.company_number, r.legal_form, r.status, r.registered_country) == (
        "01234567",
        "ltd",
        "active",
        "GB",
    )
    assert rec.calls[0].headers["authorization"] == "Basic " + base64.b64encode(b"test-key:").decode()
    assert rec.calls[0].url.params["q"] == "Contoso Managed IT Limited"


@pytest.mark.parametrize(
    ("ch_type", "form"),
    [
        ("llp", "llp"),
        ("plc", "plc"),
        ("limited-partnership", "lp"),
        ("scottish-partnership", "other_corporate"),
        ("charitable-incorporated-organisation", "charity"),
        ("something-new", "unknown"),
    ],
)
def test_companies_house_type_mapping(ch_type: str, form: str) -> None:
    reg, _ = ch(
        {
            ("GET", "/search/companies"): {
                "items": [{"title": "Woodgrove IT", "company_number": "1", "company_status": "active"}]
            },
            ("GET", "/company/1"): {"type": ch_type, "company_status": "active", "jurisdiction": "scotland"},
        }
    )
    r = reg.lookup("Woodgrove IT")
    assert r is not None and r.legal_form == form


def test_companies_house_no_or_ambiguous_match_returns_none() -> None:
    reg, _ = ch({("GET", "/search/companies"): {"items": []}})
    assert reg.lookup("Nobody Ltd") is None
    twins = {
        "items": [
            {"title": "Twin Ltd", "company_number": "1", "company_status": "active"},
            {"title": "TWIN LIMITED", "company_number": "2", "company_status": "active"},
        ]
    }
    reg, _ = ch({("GET", "/search/companies"): twins})
    assert reg.lookup("Twin Ltd") is None  # two equally good matches: unknown, never a guess
    assert normalise_name("The Twin Co. Limited") == "twin co"


def test_companies_house_errors_are_classified() -> None:
    reg, _ = ch({("GET", "/search/companies"): lambda r: httpx.Response(429, headers={"Retry-After": "5"})})
    with pytest.raises(RateLimited):
        reg.lookup("X Ltd")
    reg, _ = ch({("GET", "/search/companies"): lambda r: httpx.Response(503)})
    with pytest.raises(RetryableProviderError):
        reg.lookup("X Ltd")
    reg, _ = ch({("GET", "/search/companies"): lambda r: httpx.Response(401, json={"error": "bad key"})})
    with pytest.raises(ProviderError):
        reg.lookup("X Ltd")
    with pytest.raises(ValueError):
        CompaniesHouseRegistry("")


# --------------------------------------------------------------------------------------------- Graph auth
def graph(routes: dict[tuple[str, str], Any], clock: list[float] | None = None) -> tuple[GraphCredentials, Recorder]:
    t = clock or [1000.0]
    routes = {
        ("POST", "/tenant-1/oauth2/v2.0/token"): {"access_token": "tok-" + str(len(t)), "expires_in": 3600},
        **routes,
    }
    rec = Recorder(routes)
    return GraphCredentials(
        "tenant-1", "client-1", "secret-1", transport=httpx.MockTransport(rec), clock=lambda: t[0]
    ), rec


def test_graph_token_is_cached_and_refreshed_before_expiry() -> None:
    clock = [1000.0]
    creds, rec = graph({}, clock)
    assert creds.token() == creds.token()
    assert sum(1 for c in rec.calls if c.url.path.endswith("/token")) == 1
    body = dict(x.split("=") for x in rec.calls[0].content.decode().split("&"))
    assert body["grant_type"] == "client_credentials" and body["scope"].startswith("https%3A%2F%2Fgraph")
    clock[0] += 3600 - 30  # inside the 60 s safety margin -> refresh
    creds.token()
    assert sum(1 for c in rec.calls if c.url.path.endswith("/token")) == 2


# --------------------------------------------------------------------------------------------- Graph mail
PAYLOAD = EmailPayload(
    to_email="alex.morgan@contoso.example",
    from_address="jordan@outreach.northwind-demo.example",
    from_name="Jordan Lee",
    subject="Service desk cover",
    body_text="Hi Alex,\n\nQuick question?\n\nJordan",
    idempotency_key="msg:abc:0",
    headers={
        "List-Unsubscribe": "<https://app.example/u/tok>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "From": "attacker@evil.example",
    },
)


def test_graph_mail_is_sent_as_mime_with_compliance_headers() -> None:
    creds, rec = graph(
        {("POST", "/v1.0/users/jordan@outreach.northwind-demo.example/sendMail"): lambda r: httpx.Response(202)}
    )
    res = GraphEmailSender(creds).send(PAYLOAD)
    call = rec.calls[-1]
    assert call.headers["content-type"] == "text/plain" and call.headers["authorization"] == "Bearer tok-1"
    msg = email.message_from_bytes(base64.b64decode(call.content), policy=policy.default)
    assert msg["List-Unsubscribe"] == "<https://app.example/u/tok>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert msg["From"] == "Jordan Lee <jordan@outreach.northwind-demo.example>"  # caller can't override From
    assert res.provider_message_id == msg["Message-ID"] == message_id("msg:abc:0", "outreach.northwind-demo.example")
    assert build_mime(PAYLOAD)[1] == res.provider_message_id  # deterministic across retries


def test_graph_mail_failure_before_acceptance_is_retryable() -> None:
    creds, _ = graph(
        {("POST", "/v1.0/users/jordan@outreach.northwind-demo.example/sendMail"): lambda r: httpx.Response(503)}
    )
    with pytest.raises(RetryableProviderError):
        GraphEmailSender(creds).send(PAYLOAD)


def test_sandbox_smtp_only_talks_to_a_local_sink() -> None:
    with pytest.raises(ValueError):
        SinkSmtpSender("smtp.office365.com", 587)
    assert SinkSmtpSender("mailpit").host == "mailpit"


# --------------------------------------------------------------------------------------------- Graph calendar
def test_graph_calendar_busy_create_and_cancel() -> None:
    created: list[dict[str, Any]] = []

    def create(req: httpx.Request) -> httpx.Response:
        created.append(json.loads(req.content))
        return httpx.Response(201, json={"id": "evt-1"})

    sched = {
        "value": [
            {
                "scheduleItems": [
                    {
                        "status": "busy",
                        "start": {"dateTime": "2026-10-14T09:00:00.0000000"},
                        "end": {"dateTime": "2026-10-14T10:00:00.0000000"},
                    },
                    {
                        "status": "free",
                        "start": {"dateTime": "2026-10-14T11:00:00.0000000"},
                        "end": {"dateTime": "2026-10-14T12:00:00.0000000"},
                    },
                ]
            }
        ]
    }
    mb = "jordan@outreach.northwind-demo.example"
    creds, _ = graph(
        {
            ("POST", f"/v1.0/users/{mb}/calendar/getSchedule"): sched,
            ("POST", f"/v1.0/users/{mb}/events"): create,
            ("POST", f"/v1.0/users/{mb}/events/evt-1/cancel"): lambda r: httpx.Response(202),
            ("POST", f"/v1.0/users/{mb}/events/gone/cancel"): lambda r: httpx.Response(404),
        }
    )
    cal = GraphCalendar(creds, mb)
    busy = cal.busy("primary", datetime(2026, 10, 14, tzinfo=UTC), datetime(2026, 10, 15, tzinfo=UTC))
    assert busy == [Slot(datetime(2026, 10, 14, 9, tzinfo=UTC), datetime(2026, 10, 14, 10, tzinfo=UTC))]
    slot = Slot(datetime(2026, 10, 15, 13, tzinfo=UTC), datetime(2026, 10, 15, 13, 30, tzinfo=UTC))
    assert cal.create_event("primary", slot, "alex@contoso.example", "Intro call", "evt:m1:0") == "evt-1"
    assert created[0]["transactionId"] == transaction_id("evt:m1:0")  # Graph de-duplicates on this
    assert created[0]["attendees"][0]["emailAddress"]["address"] == "alex@contoso.example"
    cal.cancel_event("primary", "evt-1", "cancel:m1:0")
    cal.cancel_event("primary", "gone", "cancel:m1:1")  # already deleted: no error


# --------------------------------------------------------------------------------------------- Graph inbound
def test_graph_inbound_message_is_parsed_with_threading_header() -> None:
    mb = "jordan@outreach.northwind-demo.example"
    creds, rec = graph(
        {
            ("GET", f"/v1.0/users/{mb}/messages/AAMk1"): {
                "internetMessageId": "<reply-1@contoso.example>",
                "subject": "Re: Service desk cover",
                "from": {"emailAddress": {"address": "alex.morgan@contoso.example"}},
                "body": {"contentType": "text", "content": "Happy to talk."},
                "receivedDateTime": "2026-10-13T11:00:00Z",
                "internetMessageHeaders": [{"name": "In-Reply-To", "value": "<abc@outreach.northwind-demo.example>"}],
            }
        }
    )
    m = GraphInbox(creds, mb).fetch_message("AAMk1")
    assert (m.from_email, m.in_reply_to, m.body_text) == (
        "alex.morgan@contoso.example",
        "<abc@outreach.northwind-demo.example>",
        "Happy to talk.",
    )
    assert m.received_at == datetime(2026, 10, 13, 11, tzinfo=UTC)
    assert rec.calls[-1].headers["prefer"] == 'outlook.body-content-type="text"'


def test_client_state_identifies_and_authenticates_the_mailbox() -> None:
    secret, boxes = b"s" * 32, ["a@x.example", "b@x.example"]
    assert mailbox_for(client_state(secret, "B@x.example"), secret, boxes) == "b@x.example"
    assert mailbox_for("forged", secret, boxes) is None and mailbox_for(None, secret, boxes) is None
    assert mailbox_for(client_state(b"t" * 32, "a@x.example"), secret, boxes) is None


def test_subscriptions_are_created_and_renewed_before_expiry() -> None:
    now = datetime(2026, 10, 13, 12, tzinfo=UTC)
    patched: list[str] = []

    def patch(req: httpx.Request) -> httpx.Response:
        patched.append(req.url.path)
        return httpx.Response(200, json={})

    subs = {
        "value": [
            {"id": "soon", "expirationDateTime": (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {"id": "later", "expirationDateTime": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        ]
    }
    creds, rec = graph(
        {
            ("POST", "/v1.0/subscriptions"): {"id": "sub-1"},
            ("GET", "/v1.0/subscriptions"): subs,
            ("PATCH", "/v1.0/subscriptions/soon"): patch,
        }
    )
    assert subscribe(creds, "a@x.example", "https://api.example/webhooks/msgraph", b"s" * 32, now) == "sub-1"
    body = json.loads(rec.calls[-1].content)
    assert body["clientState"] == client_state(b"s" * 32, "a@x.example") and body["changeType"] == "created"
    assert renew_all(creds, now) == 1 and patched == ["/v1.0/subscriptions/soon"]


def test_graph_sweep_lists_ids_only_across_pages() -> None:
    mb = "jordan@outreach.northwind-demo.example"
    path = f"/v1.0/users/{mb}/mailFolders('Inbox')/messages"
    pages = {
        None: {
            "value": [{"id": "A1", "internetMessageId": "<a1@x>"}],
            "@odata.nextLink": f"https://graph.microsoft.com{path}?$skiptoken=2",
        },
        "2": {"value": [{"id": "A2", "internetMessageId": "<a2@x>"}]},
    }

    def listing(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[req.url.params.get("$skiptoken")])

    creds, rec = graph({("GET", path): listing})
    ids = GraphInbox(creds, mb).recent_ids(datetime(2026, 10, 11, tzinfo=UTC))
    assert ids == [("A1", "<a1@x>"), ("A2", "<a2@x>")]
    first = next(c for c in rec.calls if c.url.path == path)
    assert first.url.params["$select"] == "id,internetMessageId"  # no bodies are read when listing
    assert first.url.params["$filter"] == "receivedDateTime ge 2026-10-11T00:00:00Z"
