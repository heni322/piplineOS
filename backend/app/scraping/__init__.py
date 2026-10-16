"""Production web collection (ADR-0020): a polite, bounded, observable way to read public company web pages.

Layers, each usable on its own:
- fetcher:  one HTTP fetch, done politely and safely (robots.txt per RFC 9309, per-host pacing and Crawl-delay,
            per-host circuit breaker, conditional GET, size and type limits, public-internet-only, same-site only).
- extract:  structure from HTML: schema.org JSON-LD, links, sitemaps. No layout-specific CSS selectors, so a site
            redesign does not break extraction as long as the site keeps publishing structured data.
- quality:  validation, normalisation, deduplication and data minimisation of what was extracted, with a counted
            reason for every record rejected.
- store:    what was seen and when (incremental crawling, first-seen dates, page validators), shared by all clients.
"""
