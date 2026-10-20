"use client";

import { useState } from "react";
import { api, type Problem } from "@/lib/api";
import { webUrl } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { CompanySearches, WebResearch } from "@/lib/types";

const t = getMessages().discover;
const fill = (s: string, v: Record<string, string | number>) =>
  Object.entries(v).reduce((acc, [k, x]) => acc.replaceAll(`{${k}}`, String(x)), s);

/** Ask Gemini (grounded on Google Search) about one company. Google's terms: the answer is shown to the person who
 * asked, with Google's Search Suggestions unchanged, and not stored. The suggestions are Google's own HTML, so they
 * are shown in a sandboxed frame with no scripts; their links open in a new tab. */
export function WebResearchPanel({ tenant, setup, onAsked }: {
  tenant: string; setup: CompanySearches["setup"] | null; onAsked: () => void;
}) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<WebResearch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError(null); setResult(null);
    try {
      setResult(await api<WebResearch>(`/api/v1/clients/${tenant}/web-research`, {
        method: "POST", body: JSON.stringify({ question: question.trim() }),
      }));
      onAsked();
    } catch (err) {
      setError((err as Problem).detail ?? (err as Problem).title);
    } finally {
      setBusy(false);
    }
  }

  const note = !setup ? null : setup.free_only ? t.setupFreeOnly.replace("Google Places", "Gemini")
    : !setup.research_key_set ? t.researchNoKey : setup.research_mode === "live" && setup.global_go_live ? null : t.researchOff;
  const reason = result && !result.called && result.reason
    ? (t.researchReasons as Record<string, string>)[result.reason] ?? result.reason : null;

  return (
    <section className="panel" aria-labelledby="research-h">
      <h2 id="research-h">{t.researchTitle}</h2>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.researchIntro}</p>
      {note && <p className="status" role="note">{note}</p>}
      {setup && <p className="muted">{fill(t.researchUsage, { n: setup.research_calls_this_month, cap: setup.research_monthly_cap })}</p>}
      <form onSubmit={ask}>
        <label>{t.researchQuestion}
          <textarea value={question} required minLength={5} maxLength={500} placeholder={t.researchPlaceholder}
            onChange={(e) => setQuestion(e.target.value)} style={{ minHeight: "3rem", fontFamily: "var(--sans)" }} />
        </label>
        <button type="submit" disabled={busy || !tenant || question.trim().length < 5}>{busy ? t.researchAsking : t.researchAsk}</button>
      </form>
      {error && <p role="alert" className="error">{error}</p>}
      {reason && <p className="status" role="status">{fill(t.researchNotCalled, { r: reason })}</p>}
      {result?.called && (
        <div aria-live="polite">
          <h3>{t.researchAnswer}</h3>
          <p style={{ whiteSpace: "pre-wrap", maxWidth: "70ch" }}>{result.answer}</p>
          {result.sources.length > 0 && (
            <>
              <h3>{t.researchSources}</h3>
              <ul>
                {result.sources.map((s) => webUrl(s.url) && (
                  <li key={s.url}><a href={webUrl(s.url) ?? undefined} target="_blank" rel="noopener noreferrer">{s.title}</a></li>
                ))}
              </ul>
            </>
          )}
          {result.suggestions_html && (
            <iframe title={t.researchSuggestions} sandbox="allow-popups allow-popups-to-escape-sandbox"
              srcDoc={`<base target="_blank">${result.suggestions_html}`} style={{ width: "100%", height: "5rem", border: 0 }} />
          )}
        </div>
      )}
    </section>
  );
}
