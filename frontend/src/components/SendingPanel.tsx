"use client";

import Link from "next/link";
import { useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages().clients;

/** Add a client's sending domain and its first inbox. DNS is then checked on the Inboxes page. */
export function SendingPanel({ id, name, onClose, onSaved }: { id: string; name: string; onClose: () => void; onSaved: (m: string) => void }) {
  const [domain, setDomain] = useState("");
  const [dom, setDom] = useState<{ id: string; domain: string } | null>(null);
  const [inbox, setInbox] = useState({ local: "", display: "", provider: "msgraph" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError((e as Problem).detail ?? (e as Problem).title); } finally { setBusy(false); }
  }
  const addDomain = () => run(async () => {
    setDom(await api<{ id: string; domain: string }>(`/api/v1/clients/${id}/sending-domains`, { method: "POST", body: JSON.stringify({ domain }) }));
  });
  const addInbox = () => run(async () => {
    if (!dom) return;
    const r = await api<{ daily_cap: number }>(`/api/v1/clients/${id}/inboxes`, { method: "POST", body: JSON.stringify({
      sending_domain_id: dom.id, address: `${inbox.local.trim()}@${dom.domain}`, display_name: inbox.display, provider: inbox.provider }) });
    onSaved(`${t.domainAdded} ${t.inboxAdded.replace("{n}", String(r.daily_cap))}`);
  });

  return (
    <div className="panel targeting" role="dialog" aria-labelledby="sending-title">
      <h2 id="sending-title">{t.sendingTitle.replace("{c}", name)}</h2>
      <p className="muted" style={{ maxWidth: "70ch" }}>{t.sendingIntro}</p>
      {error && <p role="alert" className="error">{error}</p>}
      {!dom ? (
        <>
          <label htmlFor="s-domain">{t.domain}</label>
          <input id="s-domain" value={domain} onChange={(e) => setDomain(e.target.value)} style={{ maxWidth: 420 }} />
          <div className="row-actions" style={{ marginTop: 10 }}>
            <button type="button" disabled={busy || domain.trim().length < 4} onClick={addDomain}>{t.addDomain}</button>
            <button type="button" className="secondary" onClick={onClose}>{t.cancel}</button>
          </div>
        </>
      ) : (
        <>
          <p className="status" role="status">{t.domainAdded}</p>
          <label htmlFor="s-local">{t.inboxAddress}</label>
          <div className="row-actions"><input id="s-local" value={inbox.local} style={{ maxWidth: 240 }} onChange={(e) => setInbox({ ...inbox, local: e.target.value })} /><span>@{dom.domain}</span></div>
          <label htmlFor="s-display">{t.inboxName}</label>
          <input id="s-display" value={inbox.display} style={{ maxWidth: 420 }} onChange={(e) => setInbox({ ...inbox, display: e.target.value })} />
          <label htmlFor="s-provider">{t.provider}</label>
          <select id="s-provider" value={inbox.provider} onChange={(e) => setInbox({ ...inbox, provider: e.target.value })}>
            <option value="msgraph">Microsoft 365</option><option value="google">Google Workspace</option>
          </select>
          <div className="row-actions" style={{ marginTop: 10 }}>
            <button type="button" disabled={busy || !inbox.local.trim() || inbox.display.trim().length < 2} onClick={addInbox}>{t.addInbox}</button>
            <Link className="button secondary" href="/operator/inboxes">{t.openInboxes}</Link>
          </div>
        </>
      )}
    </div>
  );
}
