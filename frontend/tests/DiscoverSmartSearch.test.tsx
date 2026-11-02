import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import DiscoverPage from "@/app/operator/discover/page";

const TENANT = "t-1";
const SEARCHES = {
  setup: {
    free_only: false, key_set: true, adapter_mode: "sandbox", global_go_live: false, calls_this_month: 3, monthly_cap: 1000,
    research_key_set: true, research_mode: "mock", research_calls_this_month: 0, research_monthly_cap: 1000,
    ai: "gemini", ai_key_set: true,
  },
  searches: [{
    id: "s-1", query: "Smart search (AI)", country: "FR", pages: 1, status: "done", called_google: true, error: null,
    created_at: "2026-11-01T10:00:00Z", finished_at: "2026-11-01T10:05:00Z",
    counts: { searched: 40, with_website: 30 }, stopped_early: false, smart: true, ranking: "done",
    queries: ["administrateur de biens Lyon", "gestion locative Bordeaux"],
    found: [
      { name: "GESTION LYON SAS", company_number: "123456789", domain: "gestion-lyon.example", address: "contact@gestion-lyon.example",
        country: "FR", fit_score: 88, fit_reason: "Agence de gestion locative." },
      { name: "AUTRE SARL", company_number: "987654321", domain: "autre.example", address: null, country: "FR",
        fit_score: null, fit_reason: null },
    ],
  }],
};

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status });
    if (url.endsWith("/api/v1/clients")) {
      return json([{ id: TENANT, legal_name: "LYLIUM", checklist: [{ key: "config", done: true, detail: "" }] }]);
    }
    if (url.endsWith("/company-searches") && init?.method === "POST") return json(SEARCHES.searches[0], 202);
    if (url.endsWith("/company-searches")) return json(SEARCHES);
    if (url.endsWith("/targeting")) return json({ watchlist: [], countries: ["FR"] });
    return json({});
  });
}

describe("Find companies: smart search", () => {
  afterEach(() => vi.restoreAllMocks());

  it("says which AI is used, lists the AI's searches and shows each company's fit, best first", async () => {
    mockApi();
    render(<DiscoverPage />);
    expect(await screen.findByRole("heading", { name: "Smart search" })).toBeInTheDocument();
    expect(await screen.findByText("AI for planning and ranking: Gemini (real calls).")).toBeInTheDocument();
    expect(screen.getByText(/Testing with real searches/)).toBeInTheDocument();
    expect(screen.getByText("Searches written by the AI: administrateur de biens Lyon · gestion locative Bordeaux"))
      .toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Fit" })).toBeInTheDocument();
    expect(screen.getByText(/Agence de gestion locative/)).toBeInTheDocument();
    expect(screen.getByText("Not ranked")).toBeInTheDocument();
  });

  it("starts a smart search without a query", async () => {
    const fetchMock = mockApi();
    render(<DiscoverPage />);
    await waitFor(() => expect(screen.getByLabelText("Country")).toHaveValue("FR"));
    fireEvent.click(await screen.findByRole("button", { name: "Run a smart search" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => i?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([, i]) => i?.method === "POST")!;
    // a French client's page searches France by itself (the UK would need a Companies House key)
    expect(JSON.parse(init!.body as string)).toEqual({ smart: true, country: "FR", pages: 1 });
  });
});
