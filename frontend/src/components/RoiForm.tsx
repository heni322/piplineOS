"use client";

import { useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages().reports;

/** The client's own deal value and close rate (client admin only), for the estimated-return figure. */
export function RoiForm({ onSaved }: { onSaved: () => void }) {
  const [admin, setAdmin] = useState(false);
  const [value, setValue] = useState("");
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api<{ role: string }>("/api/v1/auth/me").then((me) => setAdmin(me.role === "tenant_admin")).catch(() => undefined); }, []);
  if (!admin) return null;

  const valid = /^\d+(\.\d{1,2})?$/.test(value) && /^\d{1,3}$/.test(rate) && +rate <= 100;
  async function save() {
    setBusy(true); setMsg(null);
    try {
      await api("/api/v1/account/roi", { method: "PUT", body: JSON.stringify({
        average_deal_value_minor: Math.round(parseFloat(value) * 100), close_rate_pct: parseInt(rate, 10) }) });
      setMsg({ ok: true, text: t.roiSaved }); onSaved();
    } catch (e) { setMsg({ ok: false, text: (e as Problem).detail ?? (e as Problem).title }); } finally { setBusy(false); }
  }
  return (
    <section className="panel" aria-labelledby="roi-h" style={{ marginTop: 28, maxWidth: 640 }}>
      <h2 id="roi-h">{t.roiTitle}</h2>
      <p className="muted">{t.roiNotSet}</p>
      {msg && <p className={msg.ok ? "status" : "error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      <label htmlFor="roi-value">{t.dealValue}</label>
      <input id="roi-value" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
      <label htmlFor="roi-rate">{t.closeRate}</label>
      <input id="roi-rate" inputMode="numeric" value={rate} onChange={(e) => setRate(e.target.value)} />
      <div className="row-actions" style={{ marginTop: 10 }}>
        <button type="button" disabled={busy || !valid} onClick={save}>{t.saveRoi}</button>
      </div>
    </section>
  );
}
