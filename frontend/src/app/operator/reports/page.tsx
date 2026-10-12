"use client";

import { useCallback, useEffect, useState } from "react";
import { MetricsLedger } from "@/components/MetricsLedger";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Metrics, Tenant } from "@/lib/types";

const t = getMessages().reports;
const iso = (d: Date) => d.toISOString().slice(0, 10);

export default function ReportsPage() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [tenant, setTenant] = useState("");
  const [from, setFrom] = useState(iso(new Date(Date.now() - 89 * 864e5)));
  const [to, setTo] = useState(iso(new Date()));
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Tenant[]>("/api/v1/tenants").then((ts) => { setTenants(ts); setTenant((c) => c || ts[0]?.id || ""); })
      .catch(() => setError(getMessages().operator.loadError));
  }, []);
  const load = useCallback(() => {
    if (!tenant) return;
    api<Metrics>(`/api/v1/reports/metrics?tenant_id=${tenant}&start=${from}&end=${to}`).then(setM)
      .catch(() => setError(getMessages().operator.loadError));
  }, [tenant, from, to]);
  useEffect(() => { load(); }, [load]);

  return (
    <>
      <h1>{t.title}</h1>
      <div className="toolbar">
        <div>
          <label htmlFor="r-tenant">{getMessages().billing.client}</label>
          <select id="r-tenant" value={tenant} onChange={(e) => setTenant(e.target.value)}>
            {tenants.map((x) => <option key={x.id} value={x.id}>{x.legal_name}</option>)}
          </select>
        </div>
        <div><label htmlFor="r-from">{t.from}</label><input id="r-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
        <div><label htmlFor="r-to">{t.to}</label><input id="r-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></div>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      {m ? <MetricsLedger m={m} detailed /> : <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>}
    </>
  );
}
