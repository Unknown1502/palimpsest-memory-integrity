"use client";

/**
 * sql-pane/sql-pane.tsx — a fixed set of labeled, pre-written queries with
 * a "run" button per query, not a general SQL editor (deliberately, per
 * CONTEXT.md's demo notes — a controlled set of queries is better for a
 * filmed demo than an open text box). Each query's `run` calls a REAL
 * api-client function; nothing here is mocked or hardcoded output.
 */

import { useState } from "react";

export interface SqlQuery {
  label: string;
  sql: string;
  run: () => Promise<unknown>;
}

export function SqlPane({ queries }: { queries: SqlQuery[] }) {
  const [active, setActive] = useState(0);
  const [result, setResult] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ranAt, setRanAt] = useState<string | null>(null);

  const current = queries[active];

  async function handleRun() {
    setLoading(true);
    setError(null);
    try {
      const data = await current.run();
      setResult(data);
      setRanAt(new Date().toLocaleTimeString());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-panel overflow-hidden">
      <div className="flex items-center gap-1 border-b border-border bg-panel-alt px-2 pt-2">
        {queries.map((q, i) => (
          <button
            key={q.label}
            onClick={() => {
              setActive(i);
              setResult(null);
              setError(null);
              setRanAt(null);
            }}
            className={`rounded-t px-3 py-1.5 text-xs font-medium transition-colors ${
              i === active
                ? "bg-panel text-text border border-border border-b-0"
                : "text-text-muted hover:text-text"
            }`}
          >
            {q.label}
          </button>
        ))}
      </div>

      <div className="p-3">
        <pre className="overflow-x-auto rounded bg-bg border border-border p-3 text-[12px] leading-relaxed font-mono-data text-accent">
          {current.sql}
        </pre>

        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={handleRun}
            disabled={loading}
            className="rounded bg-accent-dim px-3 py-1.5 text-xs font-semibold text-accent hover:brightness-125 disabled:opacity-50"
          >
            {loading ? "Running…" : "▶ Run"}
          </button>
          {ranAt && <span className="text-[11px] text-text-faint">ran at {ranAt}</span>}
        </div>

        {error && (
          <div className="mt-3 rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-300">
            {error}
          </div>
        )}

        {result !== null && !error && (
          <pre className="mt-3 max-h-96 overflow-auto rounded bg-bg border border-border p-3 text-[12px] leading-relaxed font-mono-data text-text">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
