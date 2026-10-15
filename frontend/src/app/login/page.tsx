import type { Metadata } from "next";
import Link from "next/link";
import { LoginForm } from "@/components/LoginForm";
import { AuthBrand } from "@/components/AuthBrand";
import { getMessages } from "@/lib/i18n";

const t = getMessages();
export const metadata: Metadata = { title: t.login.title };

export default function LoginPage() {
  return (
    <main id="main" className="container narrow">
      <AuthBrand />
      <h1>{t.login.title}</h1>
      <div className="card"><LoginForm /></div>
      <p><Link href="/forgot-password">{t.login.forgot}</Link></p>
    </main>
  );
}
