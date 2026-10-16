# ADR-0020: Web collection: a polite, structured, quality-checked crawler for company careers pages

- Status: Proposed (decided under delegated authority, ADR-0011; the legal question is B9 for counsel)
- Date: 2026-10-16

## Context
Hiring is the strongest free timing signal for an IT service provider's prospects: a company opening IT roles has a
new team, a new budget and new needs. PipelineOS already read hiring from Greenhouse, Lever and Ashby through their
public APIs. **Most UK SMEs (the ICP's targets) use none of them**: they list vacancies on their own website, so for
them we had no hiring signal at all.

"Scraping" those sites is easy to do badly:
- brittle CSS selectors break on every redesign;
- crawlers hammer small sites and ignore robots.txt;
- fetching URLs taken from web pages opens SSRF holes;
- personal data gets collected "because it was there";
- stale, duplicate or other-employer ads end up reported as news;
- sources fail silently.

## Decision
A reusable web-collection layer (`app/scraping/`) and one source built on it (`app/adapters/signals/career_pages.py`).

### 1. What we read: structured data, not layout
- Job ads published as **schema.org `JobPosting` JSON-LD**. Google for Jobs requires it, so employers who want
  their ads found keep it accurate. It survives redesigns, because no CSS selector is involved.
- **Links** are chosen by where they point and what they say ("Careers", "Vacancies", "Join us") and **sitemaps**
  are parsed with defusedxml.
- A careers page that links to a hosted board we can read for free (Greenhouse, Lever, Ashby) switches that
  company to the board's API, which is more reliable than its pages.

### 2. How we fetch: the polite fetcher (`app/scraping/fetcher.py`)
All rules are enforced in code:

| Rule | Implementation |
|---|---|
| Identified | `PipelineOS-bot` User-Agent with an explanation URL |
| robots.txt (RFC 9309) | Cached per host for 24 h. 2xx: obeyed, including Crawl-delay. 4xx: no rules, except 401/403: stay away. 5xx or unreachable: all disallowed, rechecked after 15 min. A site whose robots.txt says no is never tried through a variant (`www.`, another path) |
| Paced | At most one request per host every 2 s, or the site's Crawl-delay (capped at 60 s). Different hosts run in parallel threads; no host waits for another |
| Bounded | 1 MB per page (streamed), HTML/XML only, 3 redirects; a request budget and a deadline per run; 8 pages per site per crawl |
| Confined (SSRF) | http(s) on standard ports only, no credentials in URLs. The host must be the watched site or a subdomain. Every hop is re-checked, and every address it resolves to must be on the public internet |
| Incremental | Conditional GET with ETag / Last-Modified. Sites are crawled at most every 20 h and rediscovered monthly; not-due sites cost nothing |
| Resilient (ADR-0019) | A per-host circuit breaker (3 failures, then 5 min). 429/503 and network failures defer the site with exponential backoff (up to 7 days); nothing is decided from an outage |

### 3. What we keep: data quality and minimisation (`app/scraping/quality.py`)
- **Allow-list of fields:** title, location, dates, employment type, ad URL, plus the employer's name, which is used only to check the ad.
  Descriptions, salaries, contact points, recruiter names, e-mail addresses and phone numbers are never read.
- **Every rejection has a counted reason**, recorded in the source run's metrics:
  - `missing_title`, `title_too_long`, `contact_details_in_title`;
  - `other_employer` (a recruitment agency advertising for someone else);
  - `date_in_future`, `stale` (over 180 days), `expired` (`validThrough` passed);
  - `duplicate`.
  A source that suddenly finds nothing therefore shows *why*.
- **Deduplication** on site, normalised title and location.
- **"New" is honest.** A dated ad is new when posted within the lookback window. An undated ad is new only when
  first seen **after** the site's first crawl. Ads already there when we started watching are a persisted
  `baseline`, never reported as news.
- **Lineage:** each ad stores its page, ad URL, first and last seen, and the extractor version
  (`jobposting-jsonld/1`), so a parser change can be traced and replayed.

### 4. Storage (migration 0026)
- `scrape_sites`, `scrape_pages` and `scraped_job_ads`. These are **company reference data shared by all clients**, like the
  filing stream: one crawl serves every client watching the same company.
- A 304 keeps that page's ads "seen". Ads unseen for 180 days are deleted.

### 5. Operation
- **On by default** (`CAREER_PAGES=true`) for watchlist companies that have a website and no job board. The source runs inside the hunt and its results are deduplicated like every other source's.
- **Run metrics** (pages read, 304s, ads accepted, rejected by reason, sites unavailable or blocked, budget used up)
  go to `source_runs`. The existing Source health page and the `SignalSourceFailing` alert cover the crawler.
- `pipelineos signals check careers --target <domain> --name "<company>"` crawls one real site live, stores
  nothing, and prints where the ads are, what was accepted and why anything was rejected. It confirms real
  formats after deployment.

## Alternatives rejected
- **Headless browser rendering:** costly, slow and a large attack surface. Sites that build their careers page in
  JavaScript usually embed a hosted board, which we detect and read through its API.
- **Site-specific selectors:** they break on redesigns and do not scale past a handful of sites.
- **Commercial scraping APIs or proxies:** paid (FREE_ONLY), and rotating IPs to evade blocking contradicts the
  politeness rules above.
- **Crawling beyond the watchlist:** out of scope and harder to justify legally. We only read sites of companies
  the client named.

## Consequences
- Hiring signals now exist for watched SMEs without an ATS, at a few requests per site per day.
- **Not measured yet.** This environment has no outbound web access, so the share of target sites that publish
  JobPosting markup is unknown. Every behaviour is tested against simulated sites. The `signals check careers`
  command is the first step on the server.
- **Legal:** B9 in `docs/LEGAL-REVIEW-CHECKLIST.md` (terms of use, database right, purpose). The crawler can be
  turned off with one setting until counsel answers.
