# PipelineOS — Documentation

Done-for-you, compliance-first multi-agent outbound system for UK (then EU) B2B meeting generation.

| Doc | Contents |
|---|---|
| [01-architecture.md](01-architecture.md) | Components, rules R1–R5, data flows, deployment, Tunisia access |
| [adr/](adr/README.md) | Architecture Decision Records 0001–0008 |
| [03-data-model.md](03-data-model.md) | All 33 required tables + supporting tables, constraints, retention |
| [04-state-machine.md](04-state-machine.md) | Lead, message and meeting state machines, invariants |
| [05-compliance-engine.md](05-compliance-engine.md) | Evaluation order, jurisdiction rules, seed config, footer |
| [06-threat-model.md](06-threat-model.md) | STRIDE, OWASP Top 10, prompt injection |
| [07-dependencies.md](07-dependencies.md) | Libraries, licences, costs, rejected options |
| [08-repo-layout.md](08-repo-layout.md) | Monorepo structure and conventions |
| [09-backlog-and-test-strategy.md](09-backlog-and-test-strategy.md) | Phase 2–3 backlog, test layers, eval datasets |
| [PHASE-0-REPORT.md](PHASE-0-REPORT.md) | Investigation report |
| [PHASE-1-REPORT.md](PHASE-1-REPORT.md) | Phase 1 report |
| [PHASE-2-REPORT.md](PHASE-2-REPORT.md) | Phase 2 report (foundations) |
| [traces-phase-2.json](traces-phase-2.json) | Raw output of the Phase 2 dry-run compliance traces |

> Not legal or tax advice. All legal/tax rules are configuration marked `verified_by_counsel: false` / `verified_by_accountant: false` until signed off by a solicitor/accountant.
