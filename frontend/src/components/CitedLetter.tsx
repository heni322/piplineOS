import type { Claim } from "@/lib/types";

/**
 * Renders email text as plain text (never HTML: content is untrusted) with every sourced claim highlighted and a
 * numbered citation linking to its source, so a reviewer can verify each fact in one click.
 */
export function CitedLetter({ text, claims, numbering }: { text: string; claims: Claim[]; numbering: Map<string, number> }) {
  const lower = text.toLowerCase();
  const hits: { start: number; end: number; claim: Claim }[] = [];
  for (const c of claims) {
    const i = lower.indexOf(c.text.toLowerCase());
    if (i >= 0 && !hits.some((h) => i < h.end && i + c.text.length > h.start)) hits.push({ start: i, end: i + c.text.length, claim: c });
  }
  hits.sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let pos = 0;
  hits.forEach((h, k) => {
    if (h.start > pos) parts.push(text.slice(pos, h.start));
    const n = numbering.get(h.claim.source_url) ?? 0;
    parts.push(
      <span key={k}>
        <mark>{text.slice(h.start, h.end)}</mark>
        <sup>
          <a href={h.claim.source_url} target="_blank" rel="noopener noreferrer" aria-label={`Source ${n}: opens in a new tab`}>[{n}]</a>
        </sup>
      </span>,
    );
    pos = h.end;
  });
  if (pos < text.length) parts.push(text.slice(pos));
  return <p className="letter">{parts}</p>;
}
