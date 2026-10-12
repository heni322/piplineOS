"use client";

import { useState } from "react";
import { api, type Problem } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages().login;

type Me = { role: "operator" | "tenant_admin" | "tenant_viewer" };

export function LoginForm({ onSuccess }: { onSuccess?: (me: Me) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const form = new FormData(e.currentTarget);
    try {
      const me = await api<Me>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: form.get("email"),
          password: form.get("password"),
          totp_code: form.get("totp") || null,
        }),
      });
      if (onSuccess) onSuccess(me);
      else window.location.assign(me.role === "operator" ? "/operator" : "/client");
    } catch (err) {
      setError((err as Problem).status === 429 ? t.locked : t.error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate aria-describedby={error ? "login-error" : undefined}>
      <label htmlFor="email">{t.email}</label>
      <input id="email" name="email" type="email" autoComplete="username" required />
      <label htmlFor="password">{t.password}</label>
      <input id="password" name="password" type="password" autoComplete="current-password" required />
      <label htmlFor="totp">{t.totp}</label>
      <input id="totp" name="totp" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}"
             aria-describedby="totp-hint" />
      <p id="totp-hint" className="hint">{t.totpHint}</p>
      {error && <p id="login-error" role="alert" className="error">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? t.submitting : t.submit}</button>
    </form>
  );
}
