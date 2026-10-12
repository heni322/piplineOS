# ADR-0002 — Thin Anthropic SDK wrapper + deterministic state machine (not LangGraph)

**Status:** Proposed

## Context
Each agent is essentially: build context → one or a few LLM calls → validate structured output → (for Agent 3) self-critique loop. Control flow between agents is the lead state machine, which must be deterministic, auditable and compliance-gated. The spec allows LangGraph or the Claude Agent SDK.

## Decision
- A small in-house `LLMClient` port (`app/llm/`) with one implementation for the official `anthropic` Python SDK plus `MockLLM` and `RecordedLLM` (replays golden responses).
- Each call: prompt file + version hash, Pydantic output schema (via tool-use / JSON schema), timeout, retries with exponential backoff + jitter, token/cost accounting written to `llm_calls` with tenant and lead IDs, per-step budget check.
- Invalid output → retry with the validation error fed back (max 2) → escalate. Refusals detected and escalated, never parsed.
- Agents are plain Python classes with a `run(input: Model) -> Model` signature. No autonomous tool-use loops in MVP — agents cannot call tools that cause side effects.

## Alternatives
- **LangGraph**: good for graph-shaped agent flows with checkpointing. Rejected: our graph *is* the lead state machine, which we want in plain, testable code and in Postgres; LangGraph would add a second state store and a large dependency tree.
- **Claude Agent SDK**: optimised for autonomous tool-using agents. We deliberately avoid giving agents side-effecting tools (ADR-0004, threat model). May be adopted later for research sub-steps (Agent 3 reading public pages) behind the same port.

## Consequences
+ Minimal dependencies, full control of validation, cost, logging, and injection defences.
− We build retry/validation helpers ourselves (~300 LOC, fully unit-tested).

## Reversibility
High — the `LLMClient` port isolates the provider and framework.
