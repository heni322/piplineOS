"""Sending-domain DNS check: SPF, DKIM and DMARC, read from public DNS (read-only, free). Mailbox providers
(Google, Microsoft, Yahoo) reject or junk bulk mail from domains without them, and the client onboarding checklist
requires a verified domain. Every finding is phrased so the owner can pass it to whoever manages the domain.

Provider-specific expectations follow the providers' own setup guides:
- Microsoft 365: SPF includes spf.protection.outlook.com; DKIM = CNAMEs selector1/selector2._domainkey
- Google Workspace: SPF includes _spf.google.com; DKIM = TXT google._domainkey (v=DKIM1)
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

Lookup = Callable[[str, str], list[str]]  # (name, "TXT" | "CNAME" | "MX" | "A") -> records ([] when none)

SPF_INCLUDE = {"msgraph": "spf.protection.outlook.com", "google": "_spf.google.com"}
DKIM_SELECTORS = {"msgraph": [("selector1", "CNAME"), ("selector2", "CNAME")], "google": [("google", "TXT")]}


@dataclass
class DnsResult:
    domain: str
    spf_ok: bool = False
    dkim_ok: bool = False
    dmarc_ok: bool = False
    dmarc_policy: str | None = None
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.spf_ok and self.dkim_ok and self.dmarc_ok


def dns_lookup(timeout: float = 5.0) -> Lookup:
    """Real resolver (public DNS). Missing names and records come back as []."""
    import dns.exception
    import dns.resolver

    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout

    def lookup(name: str, rdtype: str) -> list[str]:
        try:
            answer = resolver.resolve(name, rdtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout):
            return []
        if rdtype == "TXT":
            return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]
        if rdtype == "MX":
            return [str(r.exchange).rstrip(".") for r in answer]  # "" = null MX (RFC 7505: accepts no mail)
        if rdtype == "A":
            return [str(r.address) for r in answer]
        return [str(r.target).rstrip(".") for r in answer]

    return lookup


def check_domain(domain: str, providers: set[str], lookup: Lookup) -> DnsResult:
    """Pure apart from `lookup`. `providers`: the inbox providers sending from this domain (msgraph, google, mock)."""
    d = domain.strip().lower().rstrip(".")
    res = DnsResult(d)
    real = {p for p in providers if p in SPF_INCLUDE}

    spf = [t for t in lookup(d, "TXT") if t.lower().startswith("v=spf1")]
    if not spf:
        res.issues.append(f"No SPF record on {d}: publish a TXT record starting with v=spf1.")
    elif len(spf) > 1:
        res.issues.append(f"{d} has {len(spf)} SPF records; there must be exactly one (merge them).")
    else:
        rec = spf[0].lower()
        missing = [SPF_INCLUDE[p] for p in sorted(real) if f"include:{SPF_INCLUDE[p]}" not in rec]
        if missing:
            res.issues.append(f"SPF on {d} doesn't include {', '.join(missing)} (needed for your mail provider).")
        if re.search(r"(^|\s)\+?all(\s|$)", rec):
            res.issues.append(f"SPF on {d} ends with +all, which lets anyone send as you: use -all or ~all.")
        elif not re.search(r"(^|\s)[-~]all(\s|$)", rec):
            res.issues.append(f"SPF on {d} should end with -all or ~all.")
        else:
            res.spf_ok = not missing

    candidates = [s for p in sorted(real) for s in DKIM_SELECTORS[p]] or [
        s for p in sorted(DKIM_SELECTORS) for s in DKIM_SELECTORS[p]
    ]
    found = []
    for selector, rdtype in candidates:
        records = lookup(f"{selector}._domainkey.{d}", rdtype)
        if rdtype == "TXT":
            records = [r for r in records if "p=" in r.replace(" ", "")]
        if records:
            found.append(selector)
    if real:
        need = {s for p in real for s, _ in DKIM_SELECTORS[p]}
        res.dkim_ok = need <= set(found) if "msgraph" in real else bool(found)
        if not res.dkim_ok:
            names = ", ".join(f"{s}._domainkey.{d}" for s in sorted(need - set(found)))
            res.issues.append(
                f"DKIM isn't set up: no record at {names}. Turn on DKIM signing in your mail admin centre."
            )
    else:
        res.dkim_ok = bool(found)
        if not found:
            res.issues.append(f"No DKIM record found for {d} (checked the Microsoft 365 and Google selectors).")

    dmarc = [t for t in lookup(f"_dmarc.{d}", "TXT") if t.lower().replace(" ", "").startswith("v=dmarc1")]
    if not dmarc:
        res.issues.append(f"No DMARC record: publish a TXT record at _dmarc.{d}, e.g. v=DMARC1; p=none; rua=mailto:...")
    elif len(dmarc) > 1:
        res.issues.append(f"_dmarc.{d} has {len(dmarc)} DMARC records; there must be exactly one.")
    else:
        m = re.search(r"(?:^|;)\s*p\s*=\s*(none|quarantine|reject)", dmarc[0], re.IGNORECASE)
        if not m:
            res.issues.append(f"The DMARC record at _dmarc.{d} has no valid p= policy (none, quarantine or reject).")
        else:
            res.dmarc_ok = True
            res.dmarc_policy = m.group(1).lower()
            if res.dmarc_policy == "none":
                res.issues.append(
                    "DMARC is in monitoring mode (p=none): accepted, but move to quarantine once reports look clean."
                )
    return res


def check_and_store(conn: Connection, domain_id: uuid.UUID, lookup: Lookup, now: datetime) -> DnsResult:
    row = (
        conn.execute(
            text("""SELECT d.domain, d.status, coalesce(array_agg(DISTINCT i.provider)
                      FILTER (WHERE i.provider IS NOT NULL), '{}') AS providers
                    FROM sending_domains d LEFT JOIN inboxes i ON i.sending_domain_id = d.id
                    WHERE d.id = :d GROUP BY d.id"""),
            {"d": domain_id},
        )
        .mappings()
        .one()
    )
    res = check_domain(row["domain"], set(row["providers"]), lookup)
    import json

    conn.execute(
        text("""UPDATE sending_domains SET spf_ok = :spf, dkim_ok = :dkim, dmarc_ok = :dmarc, dmarc_policy = :pol,
                  dns_issues = CAST(:iss AS jsonb), dns_checked_at = :now,
                  verified_at = CASE WHEN :all_ok THEN coalesce(verified_at, :now) ELSE NULL END,
                  status = CASE WHEN :all_ok AND status = 'setup' THEN 'warming' ELSE status END
                WHERE id = :d"""),
        {
            "spf": res.spf_ok,
            "dkim": res.dkim_ok,
            "dmarc": res.dmarc_ok,
            "pol": res.dmarc_policy,
            "iss": json.dumps(res.issues),
            "now": now,
            "all_ok": res.ok,
            "d": domain_id,
        },
    )
    return res
