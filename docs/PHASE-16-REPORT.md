# Phase 16 — CI green, time-safe tests, dependency review: Report (2026-10-15)

## 1. CI now passes on GitHub, for the first time
Before this phase, no CI run had ever fully passed:

| Job | Cause | Fix |
|---|---|---|
| frontend | "No pnpm version is specified" | pnpm pinned to 10.28.0 in CI and in `frontend/package.json` (`packageManager`). CI, Docker and developers now use the same pnpm, and a new pnpm release can't change the build. |
| security | `aquasecurity/trivy-action@0.28.0` no longer resolves; its tags now start with `v` | `v0.36.0` |
| security | The web image had HIGH findings, all inside npm's own bundled packages | The production image no longer ships npm, corepack or yarn. It only runs `node server.js`. |
| e2e | Had never run, because the frontend job failed first. Its first run found a real accessibility bug: a table that scrolls sideways was not reachable by keyboard. | Every table's scroll box is a focusable, labelled region (WCAG 2.1.1). |

Also:
- Actions moved off deprecated Node 20: checkout v7, pnpm/action-setup v6, setup-uv v7.
- A failing e2e run now reports the end of its output as an annotation.

## 2. Tests that depended on the calendar
Two tests mixed fixed dates with the database's real clock:
- **Warm-up:** passed only on 2026-10-14.
- **Dispute window:** would have started failing in early 2027.

Both now use dates relative to the recorded event. The whole backend suite was then run with the clock moved forward
60 and 400 days (libfaketime, for both the app and PostgreSQL). Every test passes.

## 3. Dependency review (Dependabot)
- **Safe, tested together on branch `chore/deps-check`:** SQLAlchemy 2.1.4, redis 8.1.0, React 19.3, Playwright 1.64
  and the frontend minor/patch group. 749 backend and 14 browser tests pass. Covers #8, #9 and #12.
- **Python 3.14 (#13): not as it is.** `pyproject.toml` allows `<3.14`, so the image's `uv sync` fails. With the
  limit raised, all 749 tests pass on 3.14.6, so it is a small, deliberate change when wanted.
- **Node 26 (#7): not as it is.** Node 26 no longer ships corepack, so `RUN corepack enable` fails. The app itself
  builds, lints and passes its unit tests on Node 26.12.
- **Next.js 16 (#11, #14): a planned upgrade.** #14 alone breaks `pnpm lint` (the lint config format changed).
- **PostgreSQL 18 (#6): needs a data migration plan** (`pg_upgrade` or dump and restore), not just a new image.
- **GitHub Actions (#1–#5):** done here for checkout, pnpm, setup-uv and trivy. gitleaks v3 and setup-node v7 are
  left for later.
- **`braces` (dev-only, through Next's lint plugin):** no fixed version exists. It is never part of the app.
  Accepted, as before.
