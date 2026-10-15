"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Testimonial } from "@/lib/types";

const t = getMessages().proof;
const scopes = t.scopes as Record<string, string>;

/** Testimonials clients consented to share, with the scope each allows. Withdrawn ones never appear here. */
export default function ProofPage() {
  const [rows, setRows] = useState<Testimonial[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api<Testimonial[]>("/api/v1/testimonials").then(setRows).catch(() => setError(true)); }, []);
  if (error) return <p role="alert" className="error">{getMessages().operator.loadError}</p>;
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.intro}</p>
      {!rows ? <p className="muted" aria-busy="true">{getMessages().operator.loading}</p> : rows.length === 0 ? <p className="muted">{t.empty}</p> : (
        <div className="grid">
          {rows.map((r) => (
            <figure key={r.id} className="card" style={{ margin: 0 }}>
              <blockquote style={{ margin: "0 0 10px" }}>“{r.quote}”</blockquote>
              <figcaption className="muted">{r.attribution ?? r.client} · {scopes[r.scope]} · {t.given.replace("{d}", day(r.consented_at))}</figcaption>
            </figure>
          ))}
        </div>
      )}
    </>
  );
}
