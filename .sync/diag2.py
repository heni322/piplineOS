import json, httpx
from datetime import UTC, datetime, timedelta
c = httpx.Client(timeout=30, follow_redirects=False, headers={"Accept": "application/json"})
B = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
now = datetime.now(UTC)
f = lambda d: (now - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%S")
out = {}
def probe(label, params):
    r = c.get(B, params=params)
    try:
        j = r.json(); rels = j.get("releases") or []
        tags = {}
        for x in rels:
            for t in x.get("tag") or []: tags[t] = tags.get(t, 0) + 1
        out[label] = {"status": r.status_code, "n": len(rels), "tags": tags, "next": (j.get("links") or {}).get("next", "")[:160], "url": str(r.url)[:200]}
    except Exception as e:
        out[label] = {"status": r.status_code, "err": str(e)[:100], "body": r.text[:200]}
probe("no_stage", {"publishedFrom": f(14), "limit": 100})
probe("from_to", {"publishedFrom": f(14), "publishedTo": f(0), "stages": "award", "limit": 100})
probe("date_only", {"publishedFrom": f(14)[:10], "stages": "award", "limit": 100})
probe("size", {"publishedFrom": f(14), "stages": "award", "size": 100})
probe("no_params", {})
print(json.dumps(out, indent=1))