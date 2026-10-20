import json, httpx
from datetime import UTC, datetime, timedelta
UA = "PipelineOS/1.0 (B2B outreach compliance checks; https://github.com/heni322/piplineOS)"
out = {}
since = (datetime.now(UTC) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S")
c = httpx.Client(timeout=30, follow_redirects=False)
r = c.get("https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search", params={"publishedFrom": since, "stages": "award", "limit": 100}, headers={"Accept": "application/json"})
try:
    j = r.json(); rels = j.get("releases") or []
    tags = {}
    for x in rels:
        for t in x.get("tag") or []: tags[t] = tags.get(t, 0) + 1
    out["cf"] = {"status": r.status_code, "releases": len(rels), "tags": tags, "keys": list(j)[:8], "next": bool((j.get("links") or {}).get("next")),
                 "titles": [((x.get("tender") or {}).get("title") or "")[:60] for x in rels[:5]],
                 "has_awards": sum(1 for x in rels if x.get("awards")), "suppliers": sum(len(a.get("suppliers") or []) for x in rels for a in x.get("awards") or [])}
except Exception as e:
    out["cf"] = {"status": r.status_code, "err": str(e)[:200], "body": r.text[:300]}
r = c.get("https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search", params={"publishedFrom": since, "stage": "award", "limit": 100}, headers={"Accept": "application/json"})
try:
    j = r.json(); rels = j.get("releases") or []
    tags = {}
    for x in rels:
        for t in x.get("tag") or []: tags[t] = tags.get(t, 0) + 1
    out["cf_stage_param"] = {"status": r.status_code, "releases": len(rels), "tags": tags}
except Exception as e:
    out["cf_stage_param"] = {"status": r.status_code, "err": str(e)[:200]}
q = 'SELECT ?w WHERE { ?c wdt:P2622 "00445790"; wdt:P856 ?w . } LIMIT 3'
for label, ua in (("wikidata_old_ua", "PipelineOS-contact-check/1.0 (published business contacts; respects robots.txt)"), ("wikidata_contact_ua", UA)):
    r = c.get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"}, headers={"User-Agent": ua, "Accept": "application/sparql-results+json"})
    out[label] = {"status": r.status_code, "rows": (r.json()["results"]["bindings"] if r.status_code == 200 else r.text[:120])}
for label, ua in (("gazette_default_ua", None), ("gazette_contact_ua", UA)):
    h = {"Accept": "application/atom+xml"} | ({"User-Agent": ua} if ua else {})
    r = c.get("https://www.thegazette.co.uk/all-notices/notice/data.feed", params={"text": '"00445790"', "categorycode": "G205000000", "results-page-size": 20}, headers=h)
    out[label] = {"status": r.status_code, "ctype": r.headers.get("content-type"), "start": r.text[:120]}
    r = c.get("https://www.thegazette.co.uk/all-notices/notice/data.feed", params={"categorycode": "G205000000", "results-page-size": 5}, headers=h)
    out[label + "_nofilter"] = {"status": r.status_code}
print(json.dumps(out, indent=1, ensure_ascii=False, default=str))