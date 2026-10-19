# Google Places: company websites and new companies

The live check showed the main gap: with free sources, 0 of 40 real companies could be emailed, because their
website could not be found (`docs/LIVE-SOURCE-CHECK.md`). Google Places knows most businesses' websites. PipelineOS
uses it in two ways:

1. **Finding a company's website.** When a lead's company has no known site and Wikidata (free) knows none either,
   Places is asked for the company name. Each website it suggests must still prove it belongs to the company: one
   of its pages must print the company's register number. Then the generic business address the site publishes
   (`sales@`, `contact@`, `info@`…) is used. Personal addresses are never taken.
2. **Finding new target companies** by activity and area:
   ```bash
   pipelineos places discover --client acme --query "IT support Leeds" --country GB --pages 2
   pipelineos places discover --client acme --query "agence web Lyon" --country FR --add --operator-email you@voyz.fr
   ```
   A business is kept only when its own site prints a register number (UK company number, or French SIREN/SIRET),
   the register confirms it (active, same name), and the site passes the website check. `--add` puts the confirmed
   companies on the client's watchlist as a new configuration version.

Google Places does not give email addresses. The addresses come from the companies' own websites, as before.

## Rules that are enforced

- **Paid, so behind the three keys.** Real searches only with `PLACES_ADAPTER_MODE=live`, the client live, and
  `GLOBAL_GO_LIVE=true` with the go-live checklist signed. Before that, each would-be search is written to the
  dry-run log and nothing is called. Never with `FREE_ONLY=true`, and never in sandbox (Google has no test key).
- **Monthly cap.** Every billed request is counted first, across all workers. At `PLACES_MONTHLY_CAP` (default
  1,000) searching stops until the 1st of next month.
- **Google's storage rule.** Only place IDs may be stored, and PipelineOS stores nothing from Places at all. What is
  kept comes from the register (name, number) and from the company's own site (domain, published address).
- **Data minimisation.** Only the ID, name, address, website and business status are requested: no phone numbers,
  reviews, photos or opening hours.

## Cost

Asking for the website puts each request in Google's "Text Search Enterprise" tier: **1,000 requests a month free,
then $35 per 1,000** (list price, read 2026-10-19; Google's pricing page:
https://developers.google.com/maps/billing-and-pricing/pricing). One request returns up to 20 businesses (a
discovery page) or the candidates for one company (website finding). With the default cap the cost stays at zero.
To go beyond, raise `PLACES_MONTHLY_CAP` and set a budget alert in Google Cloud Billing.

## Questions for the solicitor

- Google's terms on using Places results outside a Google map (attribution) and on building a prospect list from
  them. We store nothing from Places, but the solicitor should confirm the use.
- The French "mentions légales" and SIREN used as proof of ownership, and the use of generic business addresses for
  B2B prospecting in France (CNIL guidance on B2B prospecting by email).
