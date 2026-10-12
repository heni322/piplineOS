import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { getMessages } from "@/lib/i18n";

const t = getMessages().operator;
export const metadata: Metadata = { title: t.title };

/** Phase 2 shell. Data-bound panels (pipeline, approval queue, escalations) arrive in Phase 3+. */
export default function OperatorHome() {
  return (
    <main id="main" className="container">
      <h1>{t.title}</h1>
      <p className="banner" role="status">{t.dryRunBanner}</p>
      <div className="grid">
        <Section title={t.pipeline} state={{ kind: "empty", message: t.emptyPipeline }} />
        <Section title={t.approvalQueue} state={{ kind: "empty", message: t.emptyQueue }} />
        <Section title={t.escalations} state={{ kind: "empty", message: t.emptyEscalations }} />
        <Section title={t.golive} state={{ kind: "empty", message: t.golivePending }} />
      </div>
    </main>
  );
}
