/** A dashboard panel with explicit empty / loading / error states (UX requirement). */
type State = { kind: "loading" } | { kind: "error"; message: string } | { kind: "empty"; message: string } | { kind: "ready" };

export function Section({ title, state, children }: { title: string; state: State; children?: React.ReactNode }) {
  const id = `s-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section className="card" aria-labelledby={id} aria-busy={state.kind === "loading"}>
      <h2 id={id}>{title}</h2>
      {state.kind === "loading" && <p className="muted">Loading…</p>}
      {state.kind === "error" && <p role="alert" className="error">{state.message}</p>}
      {state.kind === "empty" && <p className="muted">{state.message}</p>}
      {state.kind === "ready" && children}
    </section>
  );
}
