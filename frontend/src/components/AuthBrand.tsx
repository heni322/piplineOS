import { getMessages } from "@/lib/i18n";

const t = getMessages().app;

/** The brand above the sign-in and account cards. */
export function AuthBrand() {
  return (
    <p className="auth-brand">
      <span className="brand-mark" aria-hidden="true">P</span>
      <span className="brand-text"><strong>{t.name}</strong><span>{t.tagline}</span></span>
    </p>
  );
}
