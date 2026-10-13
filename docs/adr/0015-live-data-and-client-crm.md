# ADR-0015 — Real data sources (Phase 6.2) and the client's CRM (Phase 6.3)

**Status:** Proposed (engineering and supplier choices taken under the delegation in ADR-0011)

| Decision | Why | Reversible |
|---|---|---|
| **Trigger signals from public contract awards** (Contracts Finder, Find a Tender) | Free, official, no scraping; carry Companies House numbers for an exact registry match | Yes |
| **Job postings are not a source yet** | Adzuna's API terms allow publishing listings and research; other commercial reuse needs a licence ([terms](https://developer.adzuna.com/docs/terms_of_service)). A paid, licensed job-data provider is a commercial decision | — |
| **Hunter.io for finding and verifying the contact**, behind the three-key paid-call gate | One EU-based supplier for both; public web sources, not LinkedIn | Yes |
| **HubSpot first for the client's CRM** | The most common CRM among UK SMB clients, with a free tier and a well-documented API. Other CRMs plug into the same adapter interface | Yes |
| **The client connects their own CRM in the portal** with a private app token; the token is encrypted and write-only | The token never travels by email or chat, and nobody at PipelineOS sees it | — |
| **CRM writes follow the three-key rule; "sandbox" also stays dry-run** | The connected CRM is always the client's real one, so there is no safe test target before go-live | — |
| **What is written:** company (by domain), contact (by email), a deal in the client's pipeline whose stage follows the meeting | Enough for the client's sales team, nothing more | Yes |
| **No personal data for a person who objected or was erased**; only an existing deal's stage may change | Objections and erasure apply everywhere | — |
| **An "attended" stage set by the client in their CRM is attendance evidence** (source `crm`) | Billing already accepts CRM evidence; operators' word alone never makes a meeting billable | — |
| **Idempotent writes:** the deal name carries our reference, and the attempt is recorded before the call, so a retry finds the deal instead of creating a second | No duplicate deals after a crash | — |
| **Failures are retried at most 5 times**, and the stored error is only a status code, never the provider's message (it can echo personal data) | Bounded load on the client's CRM; no personal data in our error records | Yes |
