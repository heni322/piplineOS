"""Contactable-rate measurement (read-only): today's Find a Tender award winners with a company number, run through
the free website finder. Prints aggregate counts and outcome categories only (no addresses or names stored)."""
import json, time
from collections import Counter
from datetime import UTC, datetime, timedelta
from app.adapters.signals.ocds import find_a_tender
from app.adapters.website_contacts import WebsiteFinder
since = datetime.now(UTC) - timedelta(days=14)
sigs = find_a_tender(max_pages=3).fetch("IT; software; service; managed; support; digital; consultancy; maintenance; cleaning; construction", since)
seen, companies = set(), []
for s in sigs:
    n = getattr(s, "company_number", None)
    if n and n not in seen:
        seen.add(n); companies.append((n, s.company_name))
companies = companies[:40]
f = WebsiteFinder(contact="https://github.com/heni322/piplineOS")
outcome = Counter(); wikidata_hit = 0; t0 = time.time()
for n, name in companies:
    try:
        if f.wikidata_domains(n): wikidata_hit += 1
        site = f.find(n, name, None)
        if site is None: outcome["no verified site"] += 1
        elif site.address: outcome["verified site + generic address"] += 1
        else: outcome["verified site, no generic address"] += 1
    except Exception as e:
        outcome["error:" + type(e).__name__] += 1
print(json.dumps({"award_signals": len(sigs), "companies_checked": len(companies), "wikidata_has_website": wikidata_hit,
                  "outcomes": dict(outcome), "seconds": round(time.time() - t0)}, indent=1))