"""Dashboard latency at scale: times the operator and client endpoints against a database seeded with
seed_100k.sql, through a real uvicorn server. Prints p50/p95 per endpoint and fails if any p95 > 2 s.
Usage (from backend/): uv run python ../scripts/loadtest/run_load.py --base http://127.0.0.1:8200 \
    --email ... --password ... --totp-secret ... --client-email ... --client-password ..."""

from __future__ import annotations

import argparse
import statistics
import sys
import time

import httpx
import pyotp

OPERATOR = ["/api/v1/pipeline/summary", "/api/v1/approvals", "/api/v1/compliance/log", "/api/v1/dry-run",
            "/api/v1/meetings", "/api/v1/escalations", "/api/v1/data-protection-requests", "/api/v1/leads",
            "/api/v1/invoices?tenant_id={t}", "/api/v1/reports/metrics?tenant_id={t}", "/api/v1/clients",
            "/api/v1/billing/meetings?tenant_id={t}", "/api/v1/campaigns", "/api/v1/testimonials",
            "/api/v1/clients/{t}/setup"]
CLIENT = ["/api/v1/reports/metrics", "/api/v1/billing/meetings", "/api/v1/meetings", "/api/v1/terms",
          "/api/v1/invoices", "/api/v1/pipeline/summary"]


def login(base: str, email: str, password: str, totp: str | None = None) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=30)
    body = {"email": email, "password": password} | ({"totp_code": pyotp.TOTP(totp).now()} if totp else {})
    r = c.post("/api/v1/auth/login", json=body)
    r.raise_for_status()
    return c


def measure(c: httpx.Client, path: str, n: int) -> tuple[float, float, int]:
    times = []
    status = 0
    for _ in range(n):
        t0 = time.perf_counter()
        r = c.get(path)
        times.append(time.perf_counter() - t0)
        status = r.status_code
    times.sort()
    return statistics.median(times), times[int(0.95 * (len(times) - 1))], status


def main() -> int:
    a = argparse.ArgumentParser()
    for f in ("base", "email", "password", "totp-secret", "client-email", "client-password", "tenant"):
        a.add_argument(f"--{f}", required=True)
    a.add_argument("--n", type=int, default=20)
    args = a.parse_args()
    worst = 0.0
    print(f"{'endpoint':55} {'p50 ms':>8} {'p95 ms':>8}  status")
    for who, c, paths in (("operator", login(args.base, args.email, args.password, args.totp_secret), OPERATOR),
                          ("client", login(args.base, args.client_email, args.client_password), CLIENT)):
        for p in paths:
            path = p.format(t=args.tenant)
            p50, p95, st = measure(c, path, args.n)
            worst = max(worst, p95)
            print(f"{who + ' ' + path:55} {p50 * 1000:8.0f} {p95 * 1000:8.0f}  {st}")
    print(f"worst p95: {worst * 1000:.0f} ms (target < 2000 ms)")
    return 0 if worst < 2.0 else 1


if __name__ == "__main__":
    sys.exit(main())
