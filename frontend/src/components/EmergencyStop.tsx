"use client";

import { useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { day } from "@/lib/format";
import { getMessages } from "@/lib/i18n";
import type { Account } from "@/lib/types";

const t = getMessages().client;

/** The client's own stop button: holds all sending for their account at once. Only the operator resumes. */
export function EmergencyStop() {
  const [account, setAccount] = useState<Account | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Account>("/api/v1/account").then(setAccount).catch(() => undefined);
    api<{ role: string }>("/api/v1/auth/me").then((me) => setRole(me.role)).catch(() => undefined);
  }, []);
  if (!account || (account.status !== "active" && account.status !== "paused")) return null;

  async function stop() {
    setBusy(true); setError(null);
    try {
      setAccount(await api<Account>("/api/v1/account/pause", { method: "POST", body: JSON.stringify({ reason }) }));
      setConfirming(false);
    } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }

  return (
    <section className="panel" aria-labelledby="stop-h" style={{ marginTop: 28, maxWidth: 640 }}>
      <h2 id="stop-h">{t.stopTitle}</h2>
      {account.status === "paused" ? (
        <p className="error" role="status"><strong>{t.stopped}</strong>{" "}
          {t.stoppedSince.replace("{d}", account.paused_at ? day(account.paused_at) : "").replace("{r}", account.paused_reason ?? "")}</p>
      ) : (
        <>
          <p className="muted">{t.stopIntro}</p>
          {error && <p role="alert" className="error">{error}</p>}
          {role !== "tenant_admin" ? <p className="hint">{t.stopAdminOnly}</p> : !confirming ? (
            <button type="button" className="danger" onClick={() => setConfirming(true)}>{t.stopButton}</button>
          ) : (
            <>
              <label htmlFor="stop-reason">{t.stopReason}</label>
              <input id="stop-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
              <div className="row-actions" style={{ marginTop: 10 }}>
                <button type="button" className="danger" disabled={busy || reason.trim().length < 3} onClick={stop}>{t.stopConfirm}</button>
                <button type="button" className="secondary" onClick={() => setConfirming(false)}>{getMessages().clients.cancel}</button>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
