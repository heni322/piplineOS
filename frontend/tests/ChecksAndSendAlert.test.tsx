import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChecksPage from "@/app/operator/checks/page";
import { OperatorShell } from "@/components/OperatorShell";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }), usePathname: () => "/operator" }));

const CHECKS = {
  checked_at: "2026-11-01T15:00:00Z", ok: false,
  checks: [
    { key: "schema", title: "Database and migrations", status: "fail", detail: "The database is at migration 0027, the code expects 0028.",
      fix: "In backend/, run: uv run alembic upgrade head" },
    { key: "ai", title: "AI (Gemini)", status: "ok", detail: "Key accepted, model gemini-flash-latest available.", fix: "" },
  ],
};
const PROBLEMS = {
  errors_last_7_days: 2,
  problems: [{
    id: "m1", client: "LYLIUM", campaign: "Bailleurs Lyon", company: "GESTION LYON SAS", status: "queued", severity: "error",
    what: "This email is waiting and cannot go out yet.", fix: "See the reason below.", when: "2026-11-01T14:00:00Z",
    reasons: [{ code: "INBOX_OAUTH_INVALID", text: "The mailbox's connection has expired.", fix: "Reconnect the mailbox on the Inboxes page." }],
  }],
};

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
    if (url.endsWith("/api/v1/system-checks")) return json(CHECKS);
    if (url.includes("/api/v1/sending-problems")) return json(PROBLEMS);
    if (url.endsWith("/api/v1/auth/me")) return json({ role: "operator" });
    if (url.endsWith("/api/v1/governance/status")) return json({ global_go_live: false, adapter_modes: { email: "mock" }, live_clients: 0 });
    return json({});
  });
}

describe("Checks page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("runs all checks on open, says what failed and what to do, and lists sending problems", async () => {
    mockApi();
    render(<ChecksPage />);
    expect(await screen.findByText(/1 check\(s\) failed/)).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Database and migrations" })).toBeInTheDocument();
    expect(screen.getByText(/uv run alembic upgrade head/)).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /GESTION LYON SAS/ })).toBeInTheDocument();
    expect(screen.getByText(/Reconnect the mailbox on the Inboxes page/)).toBeInTheDocument();
  });

  it("can be run again on demand", async () => {
    const fetchMock = mockApi();
    render(<ChecksPage />);
    await screen.findByText(/1 check\(s\) failed/);
    fireEvent.click(screen.getByRole("button", { name: "Run all checks" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/system-checks")).length).toBe(2));
  });
});

describe("Operator console alert", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows failed emails as a red alert on every page, with a link to the reasons", async () => {
    mockApi();
    render(<OperatorShell><p>page</p></OperatorShell>);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("2 email(s) could not be sent in the last 7 days.");
    expect(screen.getByRole("link", { name: "See why and what to do" })).toHaveAttribute("href", "/operator/checks");
  });
});
