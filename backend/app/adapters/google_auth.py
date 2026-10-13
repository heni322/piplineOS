"""Google Workspace service account with domain-wide delegation: the app acts as each sending mailbox (the JWT
`sub`), with only the scopes each call needs. Workspace admins authorise the client ID for exactly those scopes.

Also verifies the OIDC token Google attaches to Pub/Sub push messages (Gmail new-mail notifications).
Tokens and Google's signing keys live in memory. Uses `cryptography` (already a dependency) rather than google-auth."""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.adapters.http import check, client

TOKEN_URL = "https://oauth2.googleapis.com/token"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GMAIL = "https://gmail.googleapis.com/gmail/v1"
CALENDAR = "https://www.googleapis.com/calendar/v3"

SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"
SCOPE_READ = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class GoogleAuthError(Exception):
    pass


@dataclass
class GoogleCredentials:
    service_account_json: str  # the key file's contents (a secret)
    transport: httpx.BaseTransport | None = None
    clock: Any = field(default=time.time)
    _tokens: dict[tuple[str, str], tuple[str, float]] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        info = json.loads(self.service_account_json)
        self.client_email: str = info["client_email"]
        key = serialization.load_pem_private_key(info["private_key"].encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise GoogleAuthError("service account key must be RSA")
        self._key = key

    def _assertion(self, subject: str, scopes: str, now: int) -> str:
        header = {"alg": "RS256", "typ": "JWT"}
        claims = {
            "iss": self.client_email,
            "sub": subject,
            "scope": scopes,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        signing_input = f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"
        sig = self._key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{b64url(sig)}"

    def token(self, subject: str, *scopes: str) -> str:
        scope = " ".join(sorted(scopes))
        with self._lock:
            now = float(self.clock())
            cached = self._tokens.get((subject.lower(), scope))
            if cached and now < cached[1] - 60:
                return cached[0]
            with client("https://oauth2.googleapis.com", self.transport) as c:
                data = check(
                    c.post(
                        "/token",
                        data={
                            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                            "assertion": self._assertion(subject, scope, int(now)),
                        },
                    )
                ).json()
            self._tokens[(subject.lower(), scope)] = (data["access_token"], now + float(data.get("expires_in", 3600)))
            return str(data["access_token"])

    def api(self, base: str, subject: str, *scopes: str) -> httpx.Client:
        return client(base, self.transport, headers={"Authorization": f"Bearer {self.token(subject, *scopes)}"})


@dataclass
class PushVerifier:
    """Verifies the Google-signed OIDC token on a Pub/Sub push request: signature (Google's published keys),
    issuer, audience (our webhook URL), the push service account's email, and expiry."""

    audience: str
    service_account_email: str
    transport: httpx.BaseTransport | None = None
    clock: Any = field(default=time.time)
    _keys: dict[str, rsa.RSAPublicKey] = field(default_factory=dict, init=False, repr=False)
    _keys_at: float = field(default=0.0, init=False)

    def _key(self, kid: str) -> rsa.RSAPublicKey | None:
        now = float(self.clock())
        if kid not in self._keys or now - self._keys_at > 3600:
            with client("https://www.googleapis.com", self.transport) as c:
                jwks = check(c.get("/oauth2/v3/certs")).json()
            keys = {}
            for k in jwks.get("keys", []):
                if k.get("kty") == "RSA":
                    n = int.from_bytes(b64url_decode(k["n"]), "big")
                    e = int.from_bytes(b64url_decode(k["e"]), "big")
                    keys[k["kid"]] = rsa.RSAPublicNumbers(e, n).public_key()
            self._keys, self._keys_at = keys, now
        return self._keys.get(kid)

    def verify(self, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise GoogleAuthError("missing bearer token")
        try:
            head_b64, claims_b64, sig_b64 = authorization[7:].split(".")
            header = json.loads(b64url_decode(head_b64))
            claims: dict[str, Any] = json.loads(b64url_decode(claims_b64))
        except (ValueError, KeyError) as exc:
            raise GoogleAuthError("malformed token") from exc
        if header.get("alg") != "RS256":
            raise GoogleAuthError("unexpected algorithm")
        key = self._key(str(header.get("kid", "")))
        if key is None:
            raise GoogleAuthError("unknown signing key")
        try:
            key.verify(b64url_decode(sig_b64), f"{head_b64}.{claims_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        except Exception as exc:
            raise GoogleAuthError("bad signature") from exc
        now = float(self.clock())
        if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
            raise GoogleAuthError("wrong issuer")
        if claims.get("aud") != self.audience:
            raise GoogleAuthError("wrong audience")
        if claims.get("email") != self.service_account_email or claims.get("email_verified") is not True:
            raise GoogleAuthError("wrong service account")
        if not (float(claims.get("iat", 0)) - 300 <= now < float(claims.get("exp", 0))):
            raise GoogleAuthError("expired")
        return claims
