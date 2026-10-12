import type { Metadata } from "next";
import { OperatorShell } from "@/components/OperatorShell";

export const metadata: Metadata = { robots: { index: false, follow: false } };

export default function OperatorLayout({ children }: { children: React.ReactNode }) {
  return <OperatorShell>{children}</OperatorShell>;
}
