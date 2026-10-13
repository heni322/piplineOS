"""The paid-call gate. Looking up a person or verifying an address with a commercial provider costs money, so it
follows the brief's rule for paid API calls: it happens only when the adapter is in live mode, the client is in live
mode AND global go-live is on with the go-live checklist signed off (`resolve_mode`). Sandbox mode uses the provider's
free test key. Otherwise the would-be call is written to the dry-run log and the work is DEFERRED: the lead stays
"discovered" and is picked up again later. It is never disqualified for want of a paid lookup.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.adapters.enrichment.base import EnrichedPerson, EnrichmentProvider
from app.adapters.verifier.base import EmailVerifier, Verification
from app.core.crypto import PiiCipher, blind_index
from app.db.session import DbContext, transaction
from app.domain.outbound.modes import SendMode


class PaidCallDeferred(Exception):
    """The paid call was recorded in the dry-run log instead of being made."""


@dataclass
class PaidGate:
    engine: Engine
    cipher: PiiCipher
    blind_key: bytes
    tenant_id: uuid.UUID
    mode: SendMode

    @property
    def allowed(self) -> bool:
        return self.mode in ("live", "sandbox")

    def record(
        self, *, adapter: str, operation: str, subject: str, lead_id: uuid.UUID | None, to: str | None = None
    ) -> None:
        key = hashlib.sha256(f"{adapter}|{operation}|{lead_id}|{to or ''}".encode()).hexdigest()
        with transaction(DbContext.system(self.tenant_id), self.engine) as conn:
            blob = self.cipher.encrypt(
                conn,
                self.tenant_id,
                "dry_run_log.payload",
                json.dumps({"to": to, "subject": subject, "operation": operation}),
            )
            conn.execute(
                text("""INSERT INTO dry_run_log (tenant_id, effect_type, adapter, idempotency_key, entity_id,
                          payload_enc, recipient_bidx) VALUES (:t, 'paid_api_call', :a, :k, :e, :p, :b)
                        ON CONFLICT (tenant_id, effect_type, idempotency_key) DO NOTHING"""),
                {
                    "t": self.tenant_id,
                    "a": adapter,
                    "k": key,
                    "e": lead_id,
                    "p": blob,
                    "b": blind_index(self.blind_key, to, self.tenant_id) if to else None,
                },
            )


class GatedEnrichment:
    """Wraps a paid enrichment provider. `preflight` lets the caller defer BEFORE any credit is spent."""

    def __init__(self, provider: EnrichmentProvider, gate: PaidGate) -> None:
        self.provider, self.gate = provider, gate
        self.name = provider.name
        self.lead_id: uuid.UUID | None = None

    def preflight(self, lead_id: uuid.UUID, label: str) -> None:
        self.lead_id = lead_id
        if not self.gate.allowed:
            self.gate.record(
                adapter=self.name, operation="find_people", subject=f"Find decision-maker at {label}", lead_id=lead_id
            )
            raise PaidCallDeferred(self.name)

    def find_people(
        self, company_domain: str, persona_titles: list[str], company_name: str | None = None
    ) -> list[EnrichedPerson]:
        if not self.gate.allowed:  # defence in depth: never call without the switch, even if preflight was skipped
            self.gate.record(
                adapter=self.name,
                operation="find_people",
                subject=f"Find decision-maker at {company_name or company_domain}",
                lead_id=self.lead_id,
            )
            raise PaidCallDeferred(self.name)
        return self.provider.find_people(company_domain, persona_titles, company_name)


class GatedVerifier:
    def __init__(self, provider: EmailVerifier, gate: PaidGate) -> None:
        self.provider, self.gate = provider, gate
        self.name = provider.name
        self.lead_id: uuid.UUID | None = None

    def preflight(self, lead_id: uuid.UUID, label: str) -> None:
        self.lead_id = lead_id
        if not self.gate.allowed:
            self.gate.record(
                adapter=self.name, operation="verify", subject=f"Verify the contact's email at {label}", lead_id=lead_id
            )
            raise PaidCallDeferred(self.name)

    def verify(self, email: str) -> Verification:
        if not self.gate.allowed:
            self.gate.record(
                adapter=self.name, operation="verify", subject="Verify email", lead_id=self.lead_id, to=email
            )
            raise PaidCallDeferred(self.name)
        return self.provider.verify(email)
