import type { Metadata } from "next";
import { Section } from "@/components/Section";
import { getMessages } from "@/lib/i18n";

const t = getMessages().client;
export const metadata: Metadata = { title: t.title };

export default function ClientHome() {
  return (
    <main id="main" className="container">
      <h1>{t.title}</h1>
      <Section title="Meetings" state={{ kind: "empty", message: t.emptyMeetings }} />
    </main>
  );
}
