import type { Metadata } from "next";
import { ClientShell } from "@/components/ClientShell";

export const metadata: Metadata = { robots: { index: false, follow: false } };

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  return <ClientShell>{children}</ClientShell>;
}
