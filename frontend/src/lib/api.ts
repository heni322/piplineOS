/** Minimal same-origin API client. Sends the CSRF double-submit header on state-changing requests. */
export type Problem = { type: string; title: string; status: number; detail?: string | null; correlation_id?: string };

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|; )pos_csrf=([^;]+)/);
  return m?.[1] ? decodeURIComponent(m[1]) : "";
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) headers.set("x-csrf-token", csrfToken());
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!res.ok) throw (await res.json().catch(() => ({ title: res.statusText, status: res.status }))) as Problem;
  return (res.status === 204 ? undefined : await res.json()) as T;
}
