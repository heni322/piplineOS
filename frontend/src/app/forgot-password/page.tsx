"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { AuthBrand } from "@/components/AuthBrand";
import { getMessages } from "@/lib/i18n";

const t = getMessages().account;

export default function ForgotPassword() {
  const [sent, setSent] = useState(false);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true); setError(false);
    try {
      await api("/api/v1/auth/forgot-password", { method: "POST", body: JSON.stringify({ email: new FormData(e.currentTarget).get("email") }) });
      setSent(true);
    } catch { setError(true); } finally { setBusy(false); }
  }

  return (
    <main id="main" className="container narrow">
      <AuthBrand />
      <h1>{t.forgotTitle}</h1>
      <div className="card">
        {sent ? <p role="status">{t.sent}</p> : (
          <form onSubmit={submit}>
            <p className="muted" style={{ marginTop: 0 }}>{t.forgotIntro}</p>
            <label htmlFor="email">{getMessages().login.email}</label>
            <input id="email" name="email" type="email" autoComplete="username" required />
            {error && <p role="alert" className="error">{t.error}</p>}
            <button type="submit" disabled={busy} style={{ marginTop: 16 }}>{t.send}</button>
          </form>
        )}
      </div>
      <p><Link href="/login">{t.back}</Link></p>
    </main>
  );
}
