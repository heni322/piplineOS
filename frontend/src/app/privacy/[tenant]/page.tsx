import type { Metadata } from "next";
import { getMessages } from "@/lib/i18n";

const t = getMessages().privacy;

// Public, indexable notice linked from every email (Article 14). Content is per tenant (controller) and is
// a PLACEHOLDER until the solicitor-reviewed text is supplied (go-live checklist).
export const metadata: Metadata = { title: t.title, robots: { index: true, follow: true } };

export default async function PrivacyNotice({ params }: { params: Promise<{ tenant: string }> }) {
  const { tenant } = await params;
  return (
    <main id="main" className="container narrow" style={{ maxWidth: 720 }}>
      <h1>{t.title}</h1>
      <p className="banner" role="note">{t.draftBanner}</p>
      <p className="muted">Reference: {tenant}</p>
      <h2>{t.whoWeAre}</h2>
      <p>[Controller legal name, company number, registered office, contact for data protection queries.]</p>
      <p>[Where the controller is outside the EU/EEA and writes to people there: its EU/EEA representative (Article 27), name and contact.]</p>
      <h2>{t.whatData}</h2>
      <p>[Professional contact details only: name, job title, work email, company. Source: licensed B2B data providers and public professional sources.]</p>
      <h2>{t.why}</h2>
      <p>[Purpose and lawful basis (legitimate interests, with a documented assessment).]</p>
      <h2>{t.rights}</h2>
      <p>{t.objectNow}</p>
      <p>[Access, erasure, rectification, restriction, portability; how to complain to the ICO.]</p>
    </main>
  );
}
