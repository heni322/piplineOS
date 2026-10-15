"use client";

import { useEffect, useState } from "react";
import { EmailPreferences } from "@/components/EmailPreferences";
import { EmergencyStop } from "@/components/EmergencyStop";
import { MetricsLedger } from "@/components/MetricsLedger";
import { RoiForm } from "@/components/RoiForm";
import { TestimonialForm } from "@/components/TestimonialForm";
import { api } from "@/lib/api";
import { getMessages } from "@/lib/i18n";
import type { Metrics } from "@/lib/types";

const t = getMessages().client;

export default function ClientHome() {
  const [m, setM] = useState<Metrics | null>(null);
  const [error, setError] = useState(false);
  const load = () => { api<Metrics>("/api/v1/reports/metrics").then(setM).catch(() => setError(true)); };
  useEffect(load, []);
  if (error) return <p role="alert" className="error">{getMessages().operator.loadError}</p>;
  if (!m) return <p className="muted" aria-busy="true">{getMessages().operator.loading}</p>;
  return (
    <>
      <h1>{t.title}</h1>
      <p className="muted">{t.last90}</p>
      <MetricsLedger m={m} />
      {m.meetings_booked === 0 && <p className="muted" style={{ marginTop: 18 }}>{t.emptyMeetings}</p>}
      {m.roi?.status === "not_configured" && <RoiForm onSaved={load} />}
      <TestimonialForm />
      <EmailPreferences />
      <EmergencyStop />
    </>
  );
}
