"""Web collection state (ADR-0020). Reference data shared by every client, like register_filing_events: COMPANY
data only, read from the companies' own public pages. No person, e-mail address or phone number is stored.

- scrape_sites:     per company website: where its job ads are (career pages, or a hosted board we can read), when
                    it was last crawled, when it is due again, and how many crawls in a row failed (incremental).
- scrape_pages:     per page: its ETag / Last-Modified for conditional requests, and a hash of its content.
- scraped_job_ads:  each distinct job ad (site + normalised title + location): first and last seen. The first-seen
                    date makes "new ad" reliable even for sites that omit or refresh datePosted. Ads not seen for
                    180 days are deleted by the crawler (minimisation). Ads already on the site at its first
                    crawl are marked `baseline`: without a date, they can never be reported as new.

Revision ID: 0026
Revises: 0025
"""

from alembic import op

from app.db.ddl import APP_ROLE

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(f"""
CREATE TABLE scrape_sites (
  site text PRIMARY KEY CHECK (site ~ '^[a-z0-9.-]{{4,253}}$'),
  career_urls text[] NOT NULL DEFAULT '{{}}',
  ats_board text CHECK (ats_board IS NULL OR ats_board ~ '^(greenhouse|lever|lever-eu|ashby):[a-z0-9_.-]{{1,100}}$'),
  discovered_at timestamptz,
  last_crawled_at timestamptz,
  next_crawl_at timestamptz,
  consecutive_failures integer NOT NULL DEFAULT 0,
  last_outcome text,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scrape_sites_due ON scrape_sites (next_crawl_at);
CREATE TABLE scrape_pages (
  url text PRIMARY KEY CHECK (length(url) <= 2048),
  site text NOT NULL,
  status smallint NOT NULL,
  etag text CHECK (length(etag) <= 300),
  last_modified text CHECK (length(last_modified) <= 100),
  content_sha256 text,
  fetched_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scrape_pages_site ON scrape_pages (site);
CREATE TABLE scraped_job_ads (
  site text NOT NULL,
  dedupe_key text NOT NULL CHECK (length(dedupe_key) = 32),
  title text NOT NULL CHECK (length(title) BETWEEN 3 AND 150),
  location text NOT NULL DEFAULT '',
  employment_type text NOT NULL DEFAULT '',
  date_posted date,
  valid_through date,
  ad_url text NOT NULL CHECK (length(ad_url) <= 2048),
  page_url text NOT NULL CHECK (length(page_url) <= 2048),  -- where we read it: a 304 there keeps it "seen"
  baseline boolean NOT NULL DEFAULT false,  -- already there on the site's first crawl: never reported as new
  extractor_version text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (site, dedupe_key)
);
CREATE INDEX scraped_job_ads_seen ON scraped_job_ads (last_seen_at);
GRANT SELECT, INSERT, UPDATE ON scrape_sites, scrape_pages TO {APP_ROLE};
GRANT SELECT, INSERT, UPDATE, DELETE ON scraped_job_ads TO {APP_ROLE};
""")


def downgrade() -> None:
    op.execute("DROP TABLE scraped_job_ads; DROP TABLE scrape_pages; DROP TABLE scrape_sites;")
