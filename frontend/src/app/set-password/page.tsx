"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, type Problem } from "@/lib/api";
import { AuthBrand } from "@/components/AuthBrand";
import { getMessages } from "@/lib/i18n";

const t = getMessages().account;

export default function SetPassword() {
  const [token, setToken] = useState<string | null | undefined>(undefined);
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    // The token is read from the link and then removed from the address bar (and so from history).
    const tok = new URLSearchParams(window.location.search).get("token");
    setToken(tok);
    if (tok) window.history.replaceState(null, "", "/set-password");
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw.length < 12) return setError(t.tooShort);
    if (pw !== again) return setError(t.mismatch);
    setBusy(true); setError(null);
    try {
      await api("/api/v1/auth/set-password", { method: "POST", body: JSON.stringify({ token, password: pw }) });
      setDone(true);
    } catch (err) {
      setError((err as Problem).status === 400 ? t.invalid : t.error);
    } finally { setBusy(false); }
  }

  return (
    <main id="main" className="container narrow">
      <AuthBrand />
      <h1>{t.setTitle}</h1>
      <div className="card">
        {done ? (
          <><p role="status">{t.saved}</p><p><Link href="/login">{t.signIn}</Link></p></>
        ) : token === undefined ? null : token === null ? <p>{t.missing}</p> : (
          <form onSubmit={submit} noValidate>
            <p className="muted" style={{ marginTop: 0 }} id="pw-hint">{t.setIntro}</p>
            <label htmlFor="pw">{t.password}</label>
            <input id="pw" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} aria-describedby="pw-hint" required />
            <label htmlFor="pw2">{t.confirm}</label>
            <input id="pw2" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
            {error && <p role="alert" className="error">{error}</p>}
            <button type="submit" disabled={busy} style={{ marginTop: 16 }}>{t.save}</button>
          </form>
        )}
      </div>
    </main>
  );
}
