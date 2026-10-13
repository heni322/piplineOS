"""Microsoft identity platform, client-credentials flow (app-only) for Microsoft Graph.

The app registration needs admin-consented application permissions scoped to the sending mailboxes only
(Mail.Send, Mail.Read, Calendars.ReadWrite) via an Exchange application access policy. Tokens live in memory."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from app.adapters.http import check, client

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"


@dataclass
class GraphCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    transport: httpx.BaseTransport | None = None
    clock: object = field(default=time.monotonic)
    _token: str = field(default="", init=False, repr=False)
    _expires: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def token(self) -> str:
        with self._lock:
            now = self.clock()  # type: ignore[operator]
            if self._token and now < self._expires - 60:
                return self._token
            with client(LOGIN, self.transport) as c:
                data = check(
                    c.post(
                        f"/{self.tenant_id}/oauth2/v2.0/token",
                        data={
                            "grant_type": "client_credentials",
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "scope": "https://graph.microsoft.com/.default",
                        },
                    )
                ).json()
            self._token, self._expires = data["access_token"], now + float(data.get("expires_in", 3600))
            return self._token

    def graph(self) -> httpx.Client:
        return client(GRAPH, self.transport, headers={"Authorization": f"Bearer {self.token()}"})
