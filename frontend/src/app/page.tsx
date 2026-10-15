import type { Metadata } from "next";
import Link from "next/link";
import { getMessages } from "@/lib/i18n";

const t = getMessages().landing;
const CONTACT = process.env.NEXT_PUBLIC_CONTACT_EMAIL;

// Public and indexable: the offer. No invented results, logos or testimonials (there is no measured pilot yet).
export const metadata: Metadata = {
  title: { absolute: `${getMessages().app.name}: ${t.metaTitle}` },
  description: t.metaDescription,
  robots: { index: true, follow: true },
};

function List({ items }: { items: string[] }) {
  return <ul className="ticks">{items.map((i) => <li key={i}>{i}</li>)}</ul>;
}

export default function Landing() {
  return (
    <main id="main" className="landing">
      <header className="landing-top">
        <span className="brand-mark" aria-hidden="true">P</span><strong>{getMessages().app.name}</strong>
        <Link href="/login" className="landing-signin">{t.ctaSignIn}</Link>
      </header>
      <section className="hero" aria-labelledby="hero-h">
        <p className="eyebrow">{t.eyebrow}</p>
        <h1 id="hero-h">{t.headline}</h1>
        <p className="lede">{t.lede}</p>
        <div className="row-actions">
          {CONTACT ? <a className="button" href={`mailto:${CONTACT}?subject=${encodeURIComponent("PipelineOS pilot")}`}>{t.ctaPilot}</a>
            : <p className="muted">{t.ctaNoEmail}</p>}
          <Link className="button secondary" href="/login">{t.ctaSignIn}</Link>
        </div>
      </section>
      <section aria-labelledby="how-h">
        <h2 id="how-h">{t.howTitle}</h2>
        <ol className="steps">
          {t.steps.map((s) => <li key={s.t} className="card"><h3>{s.t}</h3><p className="muted">{s.d}</p></li>)}
        </ol>
      </section>
      <div className="grid landing-cols">
        <section className="card" aria-labelledby="pay-h"><h2 id="pay-h">{t.payTitle}</h2><List items={t.pay} /></section>
        <section className="card" aria-labelledby="pilot-h"><h2 id="pilot-h">{t.pilotTitle}</h2><List items={t.pilot} /></section>
        <section className="card" aria-labelledby="trust-h"><h2 id="trust-h">{t.trustTitle}</h2><List items={t.trust} /></section>
      </div>
      <section aria-labelledby="honest-h" className="honest">
        <h2 id="honest-h">{t.honestTitle}</h2>
        <p>{t.honest}</p>
      </section>
      <footer className="landing-foot muted">{t.footer}</footer>
    </main>
  );
}
