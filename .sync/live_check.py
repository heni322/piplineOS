"""One live, read-only call to each free source that needs no key. Prints counts and company-level samples only."""
import json, traceback
from datetime import UTC, datetime, timedelta
since = datetime.now(UTC) - timedelta(days=14)
out = {}
def run(name, fn):
    try:
        out[name] = fn()
    except Exception as e:
        out[name] = {"ERROR": type(e).__name__, "detail": str(e)[:300]}
def ocds(src):
    sigs = src.fetch("IT; software; managed service", since)
    return {"signals": len(sigs), "sample": [(s.company_name, getattr(s, "company_number", None), s.title[:60]) for s in sigs[:3]]}
from app.adapters.signals.ocds import contracts_finder, find_a_tender
run("contracts_finder", lambda: ocds(contracts_finder()))
run("find_a_tender", lambda: ocds(find_a_tender()))
def reg(cls, name, number=None):
    r = cls().lookup(name, company_number=number)
    return r.__dict__ if r else None
from app.adapters.registries.france import FranceRegistry
from app.adapters.registries.norway import NorwayRegistry
from app.adapters.registries.finland import FinlandRegistry
run("fr_register_number", lambda: reg(FranceRegistry, "Danone", "552032534"))
run("fr_register_name", lambda: reg(FranceRegistry, "Danone"))
run("no_register_number", lambda: reg(NorwayRegistry, "Equinor", "923609016"))
run("no_register_name", lambda: reg(NorwayRegistry, "Equinor ASA"))
run("fi_register_number", lambda: reg(FinlandRegistry, "Nokia", "0112038-9"))
run("fi_register_name", lambda: reg(FinlandRegistry, "Supercell Oy"))
print(json.dumps(out, indent=1, default=str, ensure_ascii=False))