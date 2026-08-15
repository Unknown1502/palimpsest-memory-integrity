"use client";

import { useEffect, useState } from "react";
import { api, type Decision, type DecisionDetail, ApiError } from "../api-client";
import { useWorkspace } from "../workspace-context";
import { VerdictBadge, IntegrityBadge, StatusBadge } from "../components/badges";

const POLL_MS = 2000;

export default function TimelinePage() {
  const { workspaceId } = useWorkspace();
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<DecisionDetail | null>(null);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;

    async function tick() {
      try {
        const data = await api.listDecisions(workspaceId, 50);
        if (!cancelled) {
          setDecisions(data);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    }

    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [workspaceId]);

  useEffect(() => {
    if (!expanded || !workspaceId) {
      setDetail(null);
      return;
    }
    api.getDecision(workspaceId, expanded).then(setDetail).catch(() => setDetail(null));
  }, [expanded, workspaceId]);

  if (!workspaceId) {
    return <EmptyWorkspaceState />;
  }

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-lg font-semibold">Decision Timeline</h1>
        <span className="text-xs text-text-faint">polling every {POLL_MS / 1000}s</span>
      </div>

      {error && <div className="mb-4 rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">{error}</div>}

      <div className="rounded-lg border border-border bg-panel overflow-hidden">
        {decisions.length === 0 && !error && (
          <div className="p-8 text-center text-sm text-text-faint">No decisions yet in this workspace.</div>
        )}
        {decisions.map((d) => (
          <div key={d.decision_id} className="border-b border-border last:border-b-0">
            <button
              onClick={() => setExpanded(expanded === d.decision_id ? null : d.decision_id)}
              className="flex w-full items-center gap-4 px-4 py-3 text-left hover:bg-panel-alt transition-colors"
            >
              <span className="font-mono-data text-xs text-text-muted w-28 shrink-0">{d.alert_ref}</span>
              <VerdictBadge verdict={d.verdict} />
              <span className="flex-1 truncate text-sm text-text-muted">{d.rationale}</span>
              <span className="text-[11px] text-text-faint shrink-0">
                {new Date(d.created_at).toLocaleTimeString()}
              </span>
              <span className="text-text-faint text-xs">{expanded === d.decision_id ? "▾" : "▸"}</span>
            </button>

            {expanded === d.decision_id && detail && (
              <div className="border-t border-border bg-bg px-4 py-3">
                <div className="mb-2 text-[11px] uppercase tracking-wide text-text-faint">
                  Influencing memories ({detail.memory_refs.length})
                </div>
                {detail.memory_refs.length === 0 && (
                  <div className="text-sm text-text-faint">No memories were retrieved for this decision.</div>
                )}
                <div className="space-y-1.5">
                  {detail.memory_refs.map((m) => (
                    <div key={m.memory_id} className="flex items-center gap-3 rounded bg-panel px-3 py-2 text-sm">
                      <span className="text-text-faint text-xs w-6">#{m.rank}</span>
                      <StatusBadge status={m.status} />
                      <IntegrityBadge sourceKind={m.source_kind} />
                      <span className="flex-1 truncate font-mono-data text-xs">{m.claim}</span>
                      <span className="text-xs text-text-muted">influence {m.influence.toFixed(2)}</span>
                    </div>
                  ))}
                </div>
                <div className="mt-3 text-[11px] text-text-faint font-mono-data">
                  decided_hlc: {detail.decided_hlc}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function EmptyWorkspaceState() {
  return (
    <div className="rounded-lg border border-dashed border-border-strong p-10 text-center">
      <p className="text-sm text-text-muted">
        No workspace selected. Run <code className="font-mono-data text-accent">python -m demo.seed</code>{" "}
        and paste the printed <code className="font-mono-data text-accent">workspace_id</code> into the field
        in the top-right corner.
      </p>
    </div>
  );
}
