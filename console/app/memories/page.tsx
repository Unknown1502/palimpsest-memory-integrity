"use client";

import { useEffect, useState } from "react";
import { api, type Memory, type BlastRadiusDecision, type MemoryStatus, ApiError } from "../api-client";
import { useWorkspace } from "../workspace-context";
import { IntegrityBadge, StatusBadge, CapabilityBadge } from "../components/badges";
import { EmptyWorkspaceState } from "../timeline/page";
import { SqlPane } from "../sql-pane/sql-pane";

const STATUS_FILTERS: Array<MemoryStatus | "all"> = ["all", "active", "quarantined", "superseded", "revoked"];

export default function MemoriesPage() {
  const { workspaceId } = useWorkspace();
  const [statusFilter, setStatusFilter] = useState<MemoryStatus | "all">("all");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [blastFor, setBlastFor] = useState<string | null>(null);
  const [blastResult, setBlastResult] = useState<BlastRadiusDecision[] | null>(null);
  const [blastLoading, setBlastLoading] = useState(false);

  useEffect(() => {
    if (!workspaceId) return;
    api
      .listMemories(workspaceId, statusFilter === "all" ? undefined : statusFilter)
      .then((data) => {
        setMemories(data);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [workspaceId, statusFilter]);

  async function showBlastRadius(memoryId: string) {
    setBlastFor(memoryId);
    setBlastLoading(true);
    setBlastResult(null);
    try {
      const res = await api.blastRadius(workspaceId, memoryId);
      setBlastResult(res.decisions);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBlastLoading(false);
    }
  }

  if (!workspaceId) {
    return <EmptyWorkspaceState />;
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-4 flex items-center justify-between">
          <h1 className="text-lg font-semibold">Belief Store</h1>
          <div className="flex gap-1">
            {STATUS_FILTERS.map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                  statusFilter === s ? "bg-panel-alt text-text border border-border-strong" : "text-text-muted hover:text-text"
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {error && <div className="mb-4 rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">{error}</div>}

        <div className="rounded-lg border border-border bg-panel overflow-hidden">
          {memories.length === 0 && !error && (
            <div className="p-8 text-center text-sm text-text-faint">No memories match this filter.</div>
          )}
          {memories.map((m) => (
            <div key={m.memory_id} className="border-b border-border last:border-b-0 px-4 py-3">
              <div className="flex items-center gap-3">
                <StatusBadge status={m.status} />
                <IntegrityBadge sourceKind={m.source_kind} />
                <CapabilityBadge capability={m.capability_ceiling} />
                <span className="flex-1 truncate font-mono-data text-sm">{m.claim}</span>
                <button
                  onClick={() => showBlastRadius(m.memory_id)}
                  className="shrink-0 rounded border border-border-strong px-2 py-1 text-[11px] font-medium text-text-muted hover:text-text hover:border-accent transition-colors"
                >
                  Blast Radius
                </button>
              </div>
              <div className="mt-1.5 flex items-center gap-4 pl-1 text-[11px] text-text-faint">
                <span>confidence {m.confidence.toFixed(2)}</span>
                <span>eff_confidence {m.eff_confidence.toFixed(2)}</span>
                <span>+{m.corroborations} corroborations</span>
                <span>−{m.refutations} refutations</span>
                <span>{new Date(m.created_at).toLocaleString()}</span>
              </div>

              {blastFor === m.memory_id && (
                <div className="mt-2 rounded border border-border bg-bg p-3">
                  {blastLoading && <span className="text-xs text-text-faint">Loading…</span>}
                  {!blastLoading && blastResult && blastResult.length === 0 && (
                    <span className="text-xs text-text-faint">No decisions have cited this memory.</span>
                  )}
                  {!blastLoading && blastResult && blastResult.length > 0 && (
                    <div className="space-y-1">
                      <div className="text-[11px] uppercase tracking-wide text-text-faint mb-1">
                        {blastResult.length} decision(s) influenced by this memory
                      </div>
                      {blastResult.map((d) => (
                        <div key={d.decision_id} className="flex items-center gap-3 text-xs font-mono-data">
                          <span className="text-text-muted w-28">{d.alert_ref}</span>
                          <span className="text-text">{d.verdict}</span>
                          <span className="text-text-faint">{new Date(d.created_at).toLocaleString()}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-muted">SQL Pane</h2>
        <SqlPane
          queries={[
            {
              label: "Quarantine check",
              sql:
                "SELECT memory_id, claim, source_kind, integrity_level, capability_ceiling\n" +
                "FROM memories\n" +
                `WHERE workspace_id = '${workspaceId}' AND status = 'quarantined';`,
              run: () => api.listMemories(workspaceId, "quarantined"),
            },
          ]}
        />
      </div>
    </div>
  );
}
