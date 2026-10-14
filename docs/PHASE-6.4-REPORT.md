# Phase 6.4 — Testing with a free local AI model: Report (2026-10-14)

## 1. Summary
The agents can now be tested with a real AI model at no cost: an open-weights model running on the owner's PC through Ollama. Nothing leaves the machine, and only test data is used.

- Measured with **Qwen 2.5 7B** (the recommended free model) and **phi3:mini**.
- Testing exposed a weakness in the writer's instructions, now fixed in version 2. With Qwen 7B, passing drafts went from 1 in 5 to **3 in 5**.
- The owner's local app now uses Qwen 7B (`LLM_ADAPTER_MODE=sandbox`).
- Everything stays in dry-run: the local model only replaces the simulator.

## 2. Changes

| Area | What |
|---|---|
| AI backend | `OllamaBackend` (`app/llm/backends.py`). Accepts only a local address (127.0.0.1, localhost, host.docker.internal), so prompts never leave the machine. Answers are constrained to the schema with Ollama structured outputs. Temperature 0; costs recorded as zero (`local:<model>`) |
| Switch | `LLM_ADAPTER_MODE=sandbox` selects the local model whatever the live keys say. `live` still needs all three keys and an Anthropic key |
| Settings | `LOCAL_LLM_URL`, `LOCAL_LLM_MODEL` (default `qwen2.5:7b`) in both env examples |
| Evaluation | `tests/evals/local_model_run.py`. Part 1: the 75 labelled replies through the real reply prompts. Part 2: the demo pipeline on a fresh database, compared with the expected outcomes, with the drafts and AI-call statistics |
| Writer | `prompts/writer/draft.v2.md`. Version 1 stays locked for the audit trail; the writer uses version 2. The deterministic checks are unchanged (same strictness) |
| Dashboard | Go-live page: "Free open model on this computer (Ollama, test only)" when the local model is in use |
| Tests | 6 new (local backend: request shape, local-only address, timeout, truncation, zero cost, mode selection). Backend: **570 passed** |

## 3. Results (owner's PC: Intel i5-10500H, 16 GB RAM, GTX 1650 4 GB)

### 3.1 Replies (Agent 4), 75 labelled synthetic replies

| | phi3:mini (3.8B) | Qwen 2.5 7B |
|---|---|---|
| Correctly classified | 95.1% | **98.4%** |
| Safety classes caught (unsubscribe, objection, data request) | 100% | 100% |
| Hidden instructions escalated / acted on | 14 of 14 / 0 | 14 of 14 / 0 |
| Weakest class | "negative" 80%, "unclear" 50% | "unclear" 50% |
| Time per reply | ≈ 15 s | ≈ 14 s |

Every mistake leaned toward caution, for example an unsubscribe classed as an objection, which still suppresses the address.

### 3.2 Demo pipeline (Agents 1–3 and compliance), 12 companies with known expected outcomes

| | phi3:mini, writer v1 | Qwen 7B, writer v1 | **Qwen 7B, writer v2** |
|---|---|---|---|
| Outcomes as expected | 5 / 12 | 7 / 12 | **9 / 12** |
| Drafts passing every check | 0 / 5 | 1 / 5 | **3 / 5** (quality 8.6, 8.8, 9.1) |
| Compliance decisions where reached | all correct | all correct | all correct |
| Run time | 36 min | 26 min | 22 min |

The compliance decisions are code, not AI, so they are right with any model.

**What failed, and why it is safe:** every rejected draft was stopped by the automatic checks and sent to a person. None reached the approval queue. The checks caught:
- a claim reworded instead of quoted word for word
- invented figures (phi3: "6,000", "24")
- an opener over 90 words
- missing follow-ups
- banned words ("game-changer", "free")

**Remaining misses with Qwen 7B (v2):**
- 2 drafts still paraphrase their claim after two rewrites, so they go to a person.
- Signal scoring rated one demo company (Graphic Design Institute) below the threshold, so it never became a lead. The intended outcome was "disqualified" one step later; either way it is not contacted.

### 3.3 A draft that passed (Qwen 7B, writer v2, synthetic data)
> Hi Taylor,
>
> I saw that Blue Yonder Managed Services launched a managed SOC offering for its 200 mid-market clients. As a Service Delivery Director, you'll know the importance of having robust support. Would a one-page summary of how we can help with overflow cover be useful?
>
> Jordan

## 4. Conclusions
1. **Use the local model for testing replies, scoring and the end-to-end flow.** Qwen 7B is good enough there, at no cost.
2. **For emails sent to real prospects, a stronger model is still recommended** (Claude, which the system is built for). With Qwen 7B, 2 of 5 drafts still need a person to rewrite them.
3. **The safety net works with weak models.** Bad drafts are stopped, never sent.

## 5. Risks

| # | Risk | Mitigation |
|---|---|---|
| R43 | A local model on a laptop is slow (≈ 15–80 s per call) | Test use only. Dashboard actions that call the AI take minutes in this mode |
| R44 | Writer v2 has only been measured with local models | Measure with Claude before go-live (needs the owner's key and approval) |
| R45 | Docker Desktop on the PC closes when the PC sleeps, interrupting long tests | Re-run the `.sync\llm-eval-*.cmd` scripts; results are written at the end of each part |

## 6. What the owner must do
Nothing. To go back to the free simulated AI, set `LLM_ADAPTER_MODE=mock` in `.env`; a backup of the previous `.env` is in `.sync\env-backup-before-local-llm.txt`.
