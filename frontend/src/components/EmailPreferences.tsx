"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";

const t = getMessages().client;

/** The client admin's own email preferences: the weekly results email can be turned off and on. */
export function EmailPreferences() {
  const [on, setOn] = useState<boolean | null>(null);
  const [admin, setAdmin] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    api<{ role: string }>("/api/v1/auth/me").then((me) => {
      if (me.role !== "tenant_admin") return;
      setAdmin(true);
      api<{ weekly_report: boolean }>("/api/v1/account/preferences").then((p) => setOn(p.weekly_report)).catch(() => undefined);
    }).catch(() => undefined);
  }, []);
  if (!admin || on === null) return null;

  async function toggle(v: boolean) {
    setOn(v); setSaved(false);
    try {
      await api("/api/v1/account/preferences", { method: "PUT", body: JSON.stringify({ weekly_report: v }) });
      setSaved(true);
    } catch { setOn(!v); }
  }
  return (
    <section className="panel" aria-labelledby="prefs-h" style={{ marginTop: 28, maxWidth: 640 }}>
      <h2 id="prefs-h">{t.prefsTitle}</h2>
      <label className="inline"><input type="checkbox" checked={on} onChange={(e) => void toggle(e.target.checked)} /> {t.prefsWeekly}</label>
      {saved && <p className="status" role="status" style={{ marginTop: 10 }}>{t.prefsSaved}</p>}
    </section>
  );
}
