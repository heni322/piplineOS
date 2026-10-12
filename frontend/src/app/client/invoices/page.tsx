"use client";

import { useEffect, useState } from "react";
import { InvoiceDocument } from "@/components/InvoiceDocument";
import { api } from "@/lib/api";
import { money } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Invoice } from "@/lib/types";

const t = getMessages().client;

export default function ClientInvoices() {
  const [list, setList] = useState<Invoice[] | null>(null);
  const [open, setOpen] = useState<Invoice | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api<Invoice[]>("/api/v1/invoices").then(setList).catch(() => setError(true)); }, []);
  async function show(id: string) { setOpen(await api<Invoice>(`/api/v1/invoices/${id}`)); }

  if (error) return <p role="alert" className="error">{getMessages().operator.loadError}</p>;
  if (!list) return <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.invoicesTitle}</h1>
      {list.length === 0 ? <p className="muted">{t.noInvoices}</p> : (
        <div className="split">
          <ul className="queue">
            {list.map((i) => (
              <li key={i.id}><button type="button" aria-current={open?.id === i.id} onClick={() => show(i.id)}>
                <span className="co">{i.number}</span><span className="meta">{i.status}, {money(i.total_minor, i.currency)}</span>
              </button></li>
            ))}
          </ul>
          {open && <InvoiceDocument inv={open} />}
        </div>
      )}
    </>
  );
}
