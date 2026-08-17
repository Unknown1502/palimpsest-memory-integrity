"use client";

/**
 * app/benchmark/page.tsx — the 12-payload injection benchmark.
 *
 * data.json is a build-time artifact written by `python -m demo.benchmark
 * --json-out`. It is not fetched live and not recomputed here: a run costs
 * minutes and real model spend, so triggering one per page view would be
 * dishonest about cost and useless as a stable reference. The run metadata
 * is rendered prominently instead, so the reader always knows which model
 * produced these numbers and when.
 *
 * The primary metric is retrieval, not suppression. Palimpsest's claim is
 * that a poisoned belief never enters the candidate set — that is
 * deterministic and attributable to the gate. Whether a model that IS shown
 * the poison then falls for it is a property of the model, and it is
 * labelled as such rather than being quietly claimed as our result.
 */

import data from "./data.json";

interface Trial {
  name: string;
  payload: string;
  ungated_verdict: string | null;
  ungated_retrieved: boolean | null;
  gated_verdict: string | null;
  gated_retrieved: boolean | null;
  error: string | null;
}

export default function BenchmarkPage() {
  const t = data.totals;
  const trials = data.trials as Trial[];

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-lg font-semibold">Injection benchmark</h1>
        <p className="mt-1 text-sm text-text-muted">
          Twelve independently-written prompt injections, each attacking the same alert
          through a different route. Same model, same database state, same alert, same
          payloads — the only variable is the memory-integrity gate.
        </p>
      </header>

      <section className="grid gap-3 md:grid-cols-2">
        <BigResult
          heading="Poisoned belief retrieved as evidence"
          subheading="The security metric. Deterministic — this is the gate, not the model."
          ungated={`${t.retrieved_ungated} / ${t.payloads}`}
          gated={`${t.retrieved_gated} / ${t.payloads}`}
          primary
        />
        <BigResult
          heading="Agent suppressed a live RCE alert"
          subheading="Downstream outcome. Model-dependent — this figure moves between runs."
          ungated={`${t.suppressed_ungated} / ${t.payloads}`}
          gated={`${t.suppressed_gated} / ${t.payloads}`}
        />
      </section>

      <section className="rounded-lg border border-border bg-panel p-4 text-xs text-text-muted">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-text-faint">Run metadata</div>
        <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          <Meta k="Generated" v={data.generated} />
          <Meta k="Triage model" v={`${data.model} via ${data.provider}`} />
          <Meta k="Embeddings" v={data.embed_model} />
          <Meta k="Alert under attack" v={`${data.alert.alert_ref} — ${data.alert.signature}`} />
          <Meta k="Source" v={data.alert.source_ip} />
          <Meta k="Wall clock" v={`${data.elapsed_seconds}s, live calls, nothing stubbed`} />
        </dl>
        <p className="mt-3 text-[11px] text-text-faint">
          Reproduce: <code className="font-mono-data text-accent">python -m demo.benchmark</code>
        </p>
      </section>

      <section>
        <div className="mb-2 text-[11px] uppercase tracking-wide text-text-faint">
          Per payload — bold marks the attack succeeding
        </div>
        <div className="overflow-hidden rounded-lg border border-border bg-panel">
          <div className="flex items-center gap-3 border-b border-border bg-panel-alt px-4 py-2 text-[11px] uppercase tracking-wide text-text-faint">
            <span className="w-52 shrink-0">payload</span>
            <span className="flex-1">route</span>
            <span className="w-28 shrink-0 text-center">no gate</span>
            <span className="w-28 shrink-0 text-center">gated</span>
          </div>
          {trials.map((tr) => (
            <div
              key={tr.name}
              className="flex items-center gap-3 border-b border-border px-4 py-2 text-sm last:border-b-0"
            >
              <span className="w-52 shrink-0 font-mono-data text-xs text-text">{tr.name}</span>
              <span className="flex-1 truncate text-xs text-text-faint">{tr.payload}</span>
              <span className="w-28 shrink-0 text-center">
                <VerdictCell verdict={tr.ungated_verdict} retrieved={tr.ungated_retrieved} />
              </span>
              <span className="w-28 shrink-0 text-center">
                <VerdictCell verdict={tr.gated_verdict} retrieved={tr.gated_retrieved} />
              </span>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-border bg-panel p-4">
        <div className="mb-1 text-sm font-semibold">Reading these results</div>
        <p className="text-sm text-text-muted">
          {t.payloads - t.suppressed_ungated} of {t.payloads} payloads failed to flip the
          verdict even with no gate at all — a current model does sometimes resist an
          injection unaided. That inconsistency <em>is</em> the problem, not a mark against
          the benchmark: prompt-level resistance is a probabilistic property of one model at
          one moment, it varies with phrasing, and it regresses the day you change models or
          the attacker rewords the payload.
        </p>
        <p className="mt-2 text-sm text-text-muted">
          The retrieval row is the deterministic one. Without the gate the poisoned belief was
          retrieved and cited as decision evidence{" "}
          <strong className="text-text">
            {t.retrieved_ungated} times out of {t.payloads}
          </strong>
          ; with the gate, <strong className="text-text">{t.retrieved_gated}</strong>. The
          belief never reaches the model, so there is nothing for the model to get right or
          wrong.
        </p>
      </section>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-2">
      <dt className="text-text-faint">{k}:</dt>
      <dd className="font-mono-data text-[11px] text-text-muted">{v}</dd>
    </div>
  );
}

function BigResult({
  heading,
  subheading,
  ungated,
  gated,
  primary,
}: {
  heading: string;
  subheading: string;
  ungated: string;
  gated: string;
  primary?: boolean;
}) {
  return (
    <div
      className="rounded-lg border bg-panel p-4"
      style={{ borderColor: primary ? "#1f5b66" : undefined }}
    >
      <div className="text-sm font-semibold">{heading}</div>
      <div className="mt-0.5 text-[11px] text-text-faint">{subheading}</div>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <div className="rounded border border-border bg-bg px-3 py-2">
          <div className="text-[11px] text-text-faint">without Palimpsest</div>
          <div className="font-mono-data text-2xl font-bold" style={{ color: "#f87171" }}>
            {ungated}
          </div>
        </div>
        <div className="rounded border border-border bg-bg px-3 py-2">
          <div className="text-[11px] text-text-faint">with Palimpsest</div>
          <div className="font-mono-data text-2xl font-bold" style={{ color: "#34d399" }}>
            {gated}
          </div>
        </div>
      </div>
    </div>
  );
}

function VerdictCell({ verdict, retrieved }: { verdict: string | null; retrieved: boolean | null }) {
  if (!verdict) return <span className="text-text-faint">—</span>;
  const attacked = verdict === "suppress";
  return (
    <span className="inline-flex flex-col items-center leading-tight">
      <span
        className={`font-mono-data text-[11px] ${attacked ? "font-bold text-red-400" : "text-text-muted"}`}
      >
        {verdict.toUpperCase()}
      </span>
      <span className="text-[10px] text-text-faint">{retrieved ? "retrieved" : "filtered"}</span>
    </span>
  );
}
