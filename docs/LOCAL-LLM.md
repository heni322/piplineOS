# The free local AI model on the server

PipelineOS can do all of its AI work (reading replies, writing emails, checking drafts) with a free, open-weights
model running on your own server through [Ollama](https://ollama.com). No per-call cost, no AI provider account,
and prospect data in the prompts never leaves the server: the model container has no internet access and no
published port.

The application already enforces this: the model URL must be this machine or the `ollama` service, and that name
must resolve to a private address only, or the app refuses to use it (fail closed).

## 1. Choose the model from the server's memory

The app itself needs about 6 GB. The model needs its file size plus about 1–2 GB. On a server without a graphics
card, choose a **mixture-of-experts** model: only about 3–4 billion of its parameters work on each word, so it
answers several times faster on a CPU than a dense model of the same size.

| Server | Model (Ollama tag) | Download | Why | Licence |
|---|---|---|---|---|
| 16 GB RAM | `gemma4:12b` | 8 GB | Fits beside the app; Google's Gemma 4, good in European languages | Apache 2.0 |
| 32 GB RAM (recommended) | `glm-4.7-flash` | 19 GB | 30B mixture of experts, 3B active: fast on CPU, strong at structured answers | MIT |
| 32 GB RAM (alternative) | `gemma4:26b` | 18 GB | 26B mixture of experts, 4B active | Apache 2.0 |
| 48 GB RAM or more | `qwen3.6:35b` | 24 GB | 35B mixture of experts, 3B active: the strongest that runs well on CPU | Apache 2.0 |
| Graphics card, 24 GB | `qwen3.6:27b` | 18 GB | Dense 27B: the best quality, but too slow on a CPU alone | Apache 2.0 |

**GLM-5 and the other very large models** (several hundred billion parameters) need several high-end graphics
cards. They are not realistic on a rented server for this budget.

These are starting points, not measurements on our tasks. **The benchmark in §4 decides**: run it on the server
with two or three candidates and use the model it recommends.

### Server prices (DigitalOcean, list prices, USD per month, excluding VAT; re-check before buying)

| Droplet | RAM / vCPU | Price | Fits |
|---|---|---|---|
| Basic | 16 GiB / 8 vCPU | $96 | `gemma4:12b` |
| Memory-Optimized | 32 GiB / 4 vCPU | $168 | `glm-4.7-flash`, `gemma4:26b` |
| Memory-Optimized | 64 GiB / 8 vCPU | $336 | `qwen3.6:35b` (comfortably) |

Source: [DigitalOcean Droplet pricing](https://www.digitalocean.com/pricing/droplets), checked 2026-10-19. Any UK
host with that memory works the same way; keep the server in the UK (the brief requires UK data residency).

Speed: AI work runs in the background workers (drafts, replies), so a few seconds per answer is fine. If the
reply queue backs up, the alerts in `ops/prometheus/alerts.yml` show it; move to a bigger server or a GPU.

## 2. Set it in `.env.prod`

```sh
LOCAL_LLM_URL=http://ollama:11434
LOCAL_LLM_MODEL=glm-4.7-flash   # from the table above
LOCAL_LLM_THINK=false           # no "thinking" pass: much faster, same answers for these tasks
LOCAL_LLM_KEEP_ALIVE=24h        # keep the model in memory between calls
LOCAL_LLM_NUM_CTX=8192
OLLAMA_MEM_LIMIT=24g            # the model's size plus about 4 GB
OLLAMA_IMAGE_TAG=0.34.4         # pinned; raise it if a new model reports an unknown architecture
FREE_ONLY=true                  # live AI work uses this model and never a paid API
```

When the model is used:
- **Before go-live (everything in dry-run):** only if `LLM_ADAPTER_MODE=sandbox`. Otherwise the built-in
  simulator answers. Use sandbox on staging to see real drafts with nothing sent.
- **After go-live, with `FREE_ONLY=true`:** always this model, for every client that is live.

## 3. Download the model and start it

Pass `--env-file .env.prod` so compose reads the model settings. The download is the only step with internet
access, in a separate one-off container that exits when done:

```sh
cd /srv/pipelineos
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm \
  run --rm ollama-pull glm-4.7-flash gemma4:26b    # one or more tags; about 20 GB each
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm up -d
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm exec ollama ollama list
```

Always add `--profile local-llm` to later `up` commands, or the model service is left out.

## 4. Benchmark the candidates on the server

```sh
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm \
  run --rm api pipelineos llm benchmark --models glm-4.7-flash,gemma4:26b
```

It uses synthetic data only and sends nothing. For each model it reports:
- **reply understanding** on the English, French, Nordic and Spanish/Italian/Portuguese/Dutch test sets;
- **safety**: every unsubscribe, objection and data request caught, and every hidden instruction in a reply
  escalated to a person and never acted on;
- **email drafting** on six synthetic leads in four languages: how many drafts pass the automatic checks
  (sourced facts only, no invented figures, formal address, no spam words...) within one rewrite;
- the time taken.

Only fully safe models can be recommended. Among those, the score is 60% reply accuracy and 40% drafting. Then
set `LOCAL_LLM_MODEL` to the recommended model and restart the API and workers:

```sh
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm up -d api worker-default worker-send worker-replies
```

Options: `--sets en,fr` to test fewer languages, `--no-drafts` to skip drafting. Each model takes several minutes
on a CPU.

The deterministic rules stay in force whatever the model says: unsubscribes, objections and data requests are
caught by fixed rules first, every draft passes the automatic checks, and every send goes through the compliance
gate and the suppression check.

## 5. Updating or removing a model

```sh
# a newer version of the same tag
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm run --rm ollama-pull glm-4.7-flash
# free the disk space of a model you no longer use (your decision; nothing else depends on it)
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile local-llm exec ollama ollama rm gemma4:26b
```

Re-run the benchmark after changing models.
