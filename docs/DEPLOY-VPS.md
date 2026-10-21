# Deploying PipelineOS on the shared VPS (voyz.fr)

The Contabo VPS (79.143.180.160, France, 23 GB RAM, 8 cores, no GPU) already runs another company's apps
(the `cdnkt_*` containers), including their nginx on ports 80/443 and their own Ollama. PipelineOS runs beside them
without touching them:

- its own containers, networks and volumes (Compose project `pipelineos-prod`), with a memory cap on each container
  (about 15 GB in total, the AI model included);
- no published port: their nginx terminates HTTPS for **voyz.fr** and forwards to PipelineOS over one private Docker
  network (`voyz_edge`);
- its own Ollama with its own models, capped at 10 GB of memory and 6 cores.

Everything stays in dry-run: nothing is sent, booked, written to a CRM or paid for until the three live keys are on
and you have signed the go-live checklist.

**Data location:** this server is in France (EU). The brief says prospect data stays in the UK. Testing and dry-run
with synthetic data are fine here; before real client data, either move to a UK server or have the solicitor
confirm an EU server is acceptable (UK GDPR allows transfers to the EU under the UK adequacy regulations, but your
privacy notice and DPAs must say so).

## 1. DNS (at your domain registrar)

| Type | Name | Value |
|---|---|---|
| A | `voyz.fr` | `79.143.180.160` |
| A | `www` | `79.143.180.160` |
| AAAA | `voyz.fr` | `2a02:c207:2326:1698::1` (optional) |

Check from your PC: `nslookup voyz.fr` must answer 79.143.180.160 before step 5.

## 2. Swap (recommended, once)

The server has no swap. With two companies' apps on it, 8 GB of swap stops a memory spike from killing a
container:

```bash
fallocate -l 8G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
```

## 3. Code and secrets

```bash
mkdir -p /srv && cd /srv
git clone https://github.com/heni322/piplineOS.git pipelineos
cd pipelineos
bash deploy/vps/bootstrap.sh
```

`bootstrap.sh` generates every password and key **on the server**, asks for your Gemini, Google Places and
Companies House keys without showing them, writes `.env.prod` and `secrets/` (root only), and prints once the keys
to store offline. Store them before you continue (password manager + a second place, never next to the backups).
It refuses to run a second time, because regenerating would make the database unreadable.

Your API keys:
- **Google Places:** Google Cloud console → APIs & Services → enable "Places API (New)" → Credentials → create an API
  key → restrict it to "Places API (New)" and to the server's IP 79.143.180.160. Set a budget alert in Billing.
- **Gemini:** Google AI Studio → API key, on a **paid** project. Only used if you later set `LLM_PROVIDER=gemini`.
- **Companies House:** developer.company-information.service.gov.uk → create an application → REST API key (free).

You can change any of them later in `.env.prod`, then restart: step 7.

## 4. The model

If you started the stand-alone `pipelineos_ollama` container for the benchmark, stop it first. Its models stay in
the `pipelineos-prod_ollamamodels` volume and the Compose service reuses them:

```bash
docker rm -f pipelineos_ollama 2>/dev/null || true
cd /srv/pipelineos
C="docker compose --env-file .env.prod -f docker-compose.prod.yml -f deploy/vps/docker-compose.vps.yml --profile local-llm"
$C run --rm ollama-pull gemma4:12b
```

## 5. HTTPS certificate for voyz.fr (on the existing nginx and certbot)

First find where their nginx keeps its configuration and how certbot shares its webroot:

```bash
docker inspect cdnkt_nginx --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
docker inspect cdnkt_certbot --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

On this server (checked 2026-10-20) their nginx mounts a **single file**,
`/opt/cdnktapi-testing/docker/nginx/default.conf` (→ `/etc/nginx/conf.d/default.conf`); certbot's Let's Encrypt
volume is mounted in nginx at `/etc/nginx/ssl`, and both share the webroot `/var/www/certbot`.
`deploy/vps/nginx-voyz.fr.conf` uses those paths. The voyz.fr blocks are therefore **appended** to that file:

1. Back up the file (`cp …/default.conf …/default.conf.bak-$(date +%F)`), append **only the first server block**
   (port 80) of `deploy/vps/nginx-voyz.fr.conf`, then `docker exec cdnkt_nginx nginx -t && docker exec cdnkt_nginx
   nginx -s reload`.
2. Get the certificate (their certbot container renews it automatically afterwards):
   ```bash
   docker exec cdnkt_certbot certbot certonly --webroot -w /var/www/certbot \
     -d voyz.fr -d www.voyz.fr --email YOUR_EMAIL --agree-tos --no-eff-email
   ```
3. Append the second block (port 443), then test and reload as in 1. If their nginx is recreated from their own
   repository, check that the voyz.fr blocks are still in `default.conf`.

## 6. Start PipelineOS

```bash
docker network create voyz_edge
docker network connect voyz_edge cdnkt_nginx
cd /srv/pipelineos
C="docker compose --env-file .env.prod -f docker-compose.prod.yml -f deploy/vps/docker-compose.vps.yml --profile local-llm"
$C build
$C up -d
$C ps
curl -s https://voyz.fr/api/v1/ready
```

`docker network connect` is lost if the other company re-creates its nginx container. To make it permanent, add
`voyz_edge` (external) to the nginx service in their compose file.

Create your operator account (MFA is mandatory; scan the code with your authenticator app):

```bash
$C run --rm api pipelineos users create-operator YOUR_EMAIL
```

Then open https://voyz.fr, sign in, and follow `docs/LAUNCH-PLAN-30-DAYS.md`.

## 7. Day to day

| Task | Command (with `C=...` as above) |
|---|---|
| Status | `$C ps` and `docker stats --no-stream` |
| Logs | `$C logs -f --tail 100 api worker-default` |
| After changing `.env.prod` | `$C up -d` |
| Update the code | `git pull && $C build && $C up -d` (migrations run first, automatically) |
| Benchmark the models | `$C run --rm api pipelineos llm benchmark --models gemma4:12b,qwen3:8b` |
| Places searches this month | shown at the end of every `pipelineos places discover` run |
| Backups | nightly at 02:00 UTC into `/srv/pipelineos/backups` (encrypted); copy them off the server |

## 8. What stays off until you decide

- Sending, calendar and CRM: `EMAIL/CALENDAR/CRM_ADAPTER_MODE=mock`. Mailboxes and warm-up come first
  (`docs/GO-LIVE-RUNBOOK.md`).
- Google Places and Gemini: configured, but no request reaches Google until a client is live and `GLOBAL_GO_LIVE`
  is on with the checklist signed. Until then each would-be search is written to the dry-run log.
- Trigger sources and registers: `SIGNALS_ADAPTER_MODE` and the register modes are `mock` (demo data). Switch them
  to `sandbox` to read the free public sources (read-only) once the Companies House key is set.

## 9. A preset own account (Vayz)

Your own outreach (you are the controller: your own LIA, privacy notice and sending domain; no DPA, client login
or billing) can be created on the server already filled in from a template in `config/niches/`. For Vayz:

```bash
$C run --rm api pipelineos clients create-own vayz-bailleurs \
  --operator-email you@example.com --legal-name LYLIUM --company-number "103 579 322" \
  --address "9 Allée Simone Gébelin, 13001 Marseille" --country FR --currency EUR
```

It creates the account once (a second run is refused) with the French offer, targeting (B2B landlords only:
SCI, agencies, residences; private landlords are never emailed), criteria and objection answers. Then in the
console: record the LIA, add the sending domain and inbox, check DNS, create a campaign. Everything stays dry-run.
