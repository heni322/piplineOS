import json
from sqlalchemy import text
from app.db.session import DbContext, get_engine, transaction
e = get_engine()
with transaction(DbContext(role="operator"), e) as c:
    t = c.execute(text("SELECT id FROM tenants WHERE legal_name LIKE 'Northwind%'")).scalar_one()
report = {"searched": 20, "with_website": 14, "no_number_on_site": 9, "not_confirmed_by_register": 2,
  "site_check_failed": 1, "stopped_early": False, "found": [
  {"name": "EXEMPLE STUDIO WEB SAS", "company_number": "912345678", "domain": "exemple-studio.example", "address": "contact@exemple-studio.example", "country": "FR"},
  {"name": "AGENCE FICTIVE LYON SARL", "company_number": "923456789", "domain": "agence-fictive.example", "address": None, "country": "FR"}]}
with transaction(DbContext(role="operator", tenant_id=t), e) as c:
    sid = c.execute(text("INSERT INTO company_searches (tenant_id, query, country) VALUES (:t, 'agence web Lyon (synthetic preview)', 'FR') RETURNING id"), {"t": t}).scalar_one()
with transaction(DbContext.system(t), e) as c:
    c.execute(text("UPDATE company_searches SET status='done', called_google=true, report=CAST(:r AS jsonb), finished_at=now() WHERE id=:i"), {"r": json.dumps(report), "i": sid})
print("ok", sid)

