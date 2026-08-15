"use client";

import { useEffect, useState } from "react";
import { api, type Decision, type RewindPreview, type RewindApplyResult, ApiError } from "../api-client";
import { useWorkspace } from "../workspace-context";
import { VerdictBadge } from "../components/badges";
import { EmptyWorkspaceState } from "../timeline/page";
import { SqlPane } from "../sql-pane/sql-pane";

export default function RewindPage() {
  const { workspaceId } = useWorkspace();
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [selectedDecisionId, setSelectedDecisionId] = useState("");
  const [triggerMemory, setTriggerMemory] = useState("");
  const [preview, setPreview] = useState<RewindPreview | null>(null);
  const [applyResult, setApplyResult] = useState<RewindApplyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId) return;
    api.listDecisions(workspaceId, 100).then(setDecisions).catch(() => setDecisions([]));
  }, [workspaceId]);

  const targetHlc = decisions.find((d) => d.decision_id === selectedDecisionId)?.decided_hlc ?? "";

  async function handlePreview() {
    if (!targetHlc || !triggerMemory) return;
    setLoading(true);
    setError(null);
    setApplyResult(null);
    try {
      const result = await api.createRewind(workspaceId, targetHlc, triggerMemory.trim());
      setPreview(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setPreview(null);
    } finally {
      setLoading(false);
    }
  }

  async function handleApply() {
    if (!preview) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.applyRewind(workspaceId, preview.rewind_id);
      setApplyResult(result);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  if (!workspaceId) {
    return <EmptyWorkspaceState />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="mb-4 text-lg font-semibold">Rewind</h1>

        <div className="rounded-lg border border-border bg-panel p-4 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <label className="block">
              <span className="mb-1 block text-[11px] uppercase tracking-wide text-text-faint">
                Target point in time
              </span>
              <select
                value={selectedDecisionId}
                onChange={(e) => setSelectedDecisionId(e.target.value)}
                className="w-full rounded border border-border bg-bg px-2 py-1.5 text-sm font-mono-data text-text focus:border-accent focus:outline-none"
              >
                <option value="">— select a decision —</option>
                {decisions.map((d) => (
                  <option key={d.decision_id} value={d.decision_id}>
                    {d.alert_ref} · {d.verdict} · {new Date(d.created_at).toLocaleString()}
                  </option>
                ))}
              </select>
              {targetHlc && <span className="mt-1 block text-[11px] text-text-faint">decided_hlc: {targetHlc}</span>}
            </label>

            <label className="block">
              <span className="mb-1 block text-[11px] uppercase tracking-wide text-text-faint">
                Trigger memory_id (suspected poisoned belief)
              </span>
              <input
                value={triggerMemory}
                onChange={(e) => setTriggerMemory(e.target.value)}
                placeholder="paste memory_id from the Memories view…"
                className="w-full rounded border border-border bg-bg px-2 py-1.5 text-sm font-mono-data text-text placeholder:text-text-faint focus:border-accent focus:outline-none"
              />
            </label>
          </div>

          <button
            onClick={handlePreview}
            disabled={loading || !targetHlc || !triggerMemory}
            className="rounded bg-accent-dim px-4 py-2 text-sm font-semibold text-accent hover:brightness-125 disabled:opacity-40"
          >
            {loading && !preview ? "Computing…" : "Preview Rewind"}
          </button>
        </div>

        {error && <div className="mt-4 rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">{error}</div>}
      </div>

      {preview && (
        <div className="rounded-lg border border-border bg-panel p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold">Belief Diff (then vs now)</h2>
            <span className="text-xs text-text-muted">
              {preview.decisions_in_blast_radius} decision(s) in blast radius
            </span>
          </div>

          {preview.belief_diff.length === 0 ? (
            <p className="text-sm text-text-faint">No belief changes between the target time and now.</p>
          ) : (
            <div className="space-y-1">
              {preview.belief_diff.map((entry) => (
                <div key={entry.memory_id} className="flex items-center gap-3 rounded bg-bg px-3 py-2 text-xs font-mono-data">
                  <DiffBadge statusThen={entry.status_then} statusNow={entry.status_now} />
                  <span className="flex-1 truncate text-text">{entry.claim}</span>
                  <span className="text-text-faint">
                    {entry.status_then ?? "—"} → {entry.status_now ?? "—"}
                  </span>
                </div>
              ))}
            </div>
          )}

          {preview.state === "awaiting_approval" && !applyResult && (
            <button
              onClick={handleApply}
              disabled={loading}
              className="rounded bg-red-950 border border-red-800 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-900 disabled:opacity-40"
            >
              {loading ? "Replaying…" : "Apply Replay"}
            </button>
          )}
        </div>
      )}

      {applyResult && (
        <div className="rounded-lg border-2 border-accent bg-panel p-6">
          <div className="text-center">
            <div className="text-[11px] uppercase tracking-widest text-text-faint mb-1">Verdict Flips</div>
            <div className="text-6xl font-black text-accent tabular-nums">{applyResult.verdict_flips}</div>
            <div className="mt-1 text-xs text-text-muted">
              out of {applyResult.decisions_replayed} decision(s) replayed against corrected memory
            </div>
          </div>

          <div className="mt-6 space-y-1.5">
            {applyResult.replays.map((r) => (
              <div key={r.original_decision_id} className="flex items-center gap-3 rounded bg-bg px-3 py-2 text-sm">
                <span className="w-28 shrink-0 font-mono-data text-xs text-text-muted">{r.alert_ref}</span>
                <VerdictBadge verdict={r.verdict_before} />
                <span className="text-text-faint">→</span>
                <VerdictBadge verdict={r.verdict_after} />
                {r.flipped && <span className="ml-auto text-[11px] font-bold text-accent">FLIPPED</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-muted">SQL Pane</h2>
        <SqlPane
          queries={[
            {
              label: "AS OF SYSTEM TIME (belief state)",
              sql:
                "SELECT memory_id, status, claim\n" +
                `FROM memories AS OF SYSTEM TIME ${targetHlc || "<target_hlc>"}\n` +
                `WHERE workspace_id = '${workspaceId}';\n\n` +
                "-- diffed in application code against the current (live) SELECT —\n" +
                "-- see database/schema.sql section 10b for why this can't be one query.",
              run: () => {
                if (!targetHlc || !triggerMemory) {
                  return Promise.reject(new Error("Select a target decision and trigger memory first."));
                }
                return api.createRewind(workspaceId, targetHlc, triggerMemory.trim());
              },
            },
          ]}
        />
      </div>
    </div>
  );
}

function DiffBadge({ statusThen, statusNow }: { statusThen: string | null; statusNow: string | null }) {
  if (statusThen === null) {
    return <span className="rounded bg-emerald-950 px-1.5 py-0.5 text-[10px] font-bold text-emerald-400">ADDED</span>;
  }
  if (statusNow === null) {
    return <span className="rounded bg-red-950 px-1.5 py-0.5 text-[10px] font-bold text-red-400">REMOVED</span>;
  }
  return <span className="rounded bg-amber-950 px-1.5 py-0.5 text-[10px] font-bold text-amber-400">CHANGED</span>;
}
