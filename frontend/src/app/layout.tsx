import type { Metadata } from "next";
import "./globals.css";
import { getMessages, LOCALE } from "@/lib/i18n";

const t = getMessages();

export const metadata: Metadata = {
  title: { default: t.app.name, template: `%s · ${t.app.name}` },
  // Default: not indexable. Only the public privacy notice opts in (see app/privacy).
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang={LOCALE}>
      <body>
        <a className="skip" href="#main">{t.app.skipToContent}</a>
        {children}
      </body>
    </html>
  );
}
