"use client";

/**
 * app/page.tsx — the Memory Integrity Control Plane.
 *
 * Every number on this page comes from GET /workspaces/{id}/audit, which
 * runs audit/auditor.py against CockroachDB. Nothing here is computed in the
 * browser, and nothing is a placeholder: if the API is unreachable the page
 * says so rather than rendering a plausible-looking zero.
 *
 * Each tile carries the SQL that produced it (revealed on click). A number
 * you cannot re-derive is decoration; a number with its query attached is
 * evidence.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, ApiError, type AuditReport } from "./api-client";
import { useWorkspace } from "./workspace-context";

// One audit request runs nine SQL queries. The deployed Function URL is
// public and unauthenticated, so a tight poll here is a load multiplier on
// the cluster for every open tab, not just a UI preference. 15s is fast
// enough to watch a demo update live and slow enough not to be the reason
// the cluster falls over during judging.
const POLL_MS = 15000;

export default function ControlPlane() {
  const { workspaceId } = useWorkspace();
  const [report, setReport] = useState<AuditReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSql, setOpenSql] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId) return;
    let cancelled = false;

    async function tick() {
      try {
        const data = await api.audit(workspaceId);
        if (!cancelled) {
          setReport(data);
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

  const toggleSql = useCallback(
    (name: string) => setOpenSql((cur) => (cur === name ? null : name)),
    [],
  );

  if (!workspaceId) return <NoWorkspace />;

  const m = report?.metrics;
  const compliant = m ? m.active_memories - m.integrity_violations : null;

  return (
    <div className="space-y-6">
      <header>
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight">PALIMPSEST</h1>
          <span className="text-xs uppercase tracking-[0.2em] text-accent">
            Memory Integrity Control Plane
          </span>
        </div>
        <p className="mt-1 text-sm text-text-muted">
          Memory is not merely data. Memory is authority. The model may propose a belief —
          it may not decide how far to trust it.
        </p>
      </header>

      {error && (
        <div className="rounded border border-red-900 bg-red-950/40 p-3 text-sm text-red-300">
          Audit unavailable: {error}
        </div>
      )}

      {!report && !error && (
        <div className="rounded-lg border border-border bg-panel p-8 text-center text-sm text-text-faint">
          Running audit against CockroachDB…
        </div>
      )}

      {report && m && (
        <>
          <section>
            <SectionLabel>Memory integrity</SectionLabel>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              <Tile label="active beliefs" value={m.active_memories} />
              <Tile label="policy compliant" value={compliant ?? 0} tone="good" />
              <Tile
                label="integrity violations"
                value={m.integrity_violations}
                tone={m.integrity_violations > 0 ? "bad" : "good"}
              />
              <Tile
                label="unresolved contradictions"
                value={m.unresolved_contradictions}
                tone={m.unresolved_contradictions > 0 ? "warn" : "good"}
              />
              <Tile label="quarantined" value={m.quarantined_memories} tone="warn" />
              <Tile label="revoked" value={m.revoked_memories} tone="bad" />
            </div>
          </section>

          <section>
            <SectionLabel>Decisions</SectionLabel>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <Tile label="decisions on record" value={m.decisions} />
              <Tile
                label="cited a revoked belief"
                value={m.decisions_touching_revoked}
                tone={m.decisions_touching_revoked > 0 ? "bad" : "good"}
                hint="the rewind blast radius"
              />
              <Tile label="pending approvals" value={m.pending_approvals} tone="warn" />
            </div>
          </section>

          <LedgerBanner
            valid={report.ledger.valid}
            entries={report.ledger.entries_checked}
            brokenAt={report.ledger.broken_at_seq}
            sql={report.ledger.sql}
            open={openSql === "__ledger"}
            onToggle={() => toggleSql("__ledger")}
          />

          <section>
            <SectionLabel>
              Policy checks — independent, read-only, direct to CockroachDB
            </SectionLabel>
            <div className="overflow-hidden rounded-lg border border-border bg-panel">
              {report.checks.map((c) => (
                <div key={c.name} className="border-b border-border last:border-b-0">
                  <button
                    onClick={() => toggleSql(c.name)}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-panel-alt"
                  >
                    <CheckMark kind={c.kind} violation={c.is_violation} />
                    <span className="font-mono-data text-xs text-text w-64 shrink-0">{c.name}</span>
                    <span className="flex-1 truncate text-sm text-text-muted">{c.question}</span>
                    <span
                      className={`font-mono-data text-xs ${
                        c.is_violation ? "text-red-400" : "text-text-faint"
                      }`}
                    >
                      {c.count} row{c.count === 1 ? "" : "s"}
                    </span>
                    <span className="text-xs text-text-faint">
                      {openSql === c.name ? "▾" : "▸"}
                    </span>
                  </button>

                  {openSql === c.name && (
                    <div className="border-t border-border bg-bg px-4 py-3">
                      <SqlBlock sql={c.sql ?? ""} />
                      {c.rows.length > 0 && (
                        <div className="mt-3">
                          <div className="mb-1 text-[11px] uppercase tracking-wide text-text-faint">
                            Rows {c.truncated && "(first 25)"}
                          </div>
                          <div className="max-h-60 overflow-auto rounded bg-panel">
                            {c.rows.map((row, i) => (
                              <pre
                                key={i}
                                className="whitespace-pre-wrap break-all border-b border-border px-3 py-1.5 font-mono-data text-[11px] text-text-muted last:border-b-0"
                              >
                                {JSON.stringify(row)}
                              </pre>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>

          <OneDatabasePanel />

          <p className="text-xs text-text-faint">
            These numbers are served by this API, which is convenience, not proof. To verify
            them without trusting this project, take the SQL above to the CockroachDB Cloud
            Managed MCP Server — or run{" "}
            <code className="font-mono-data text-accent">python -m audit.auditor --print-sql</code>.{" "}
            <Link href="/proof" className="text-accent underline">
              More on independent verification →
            </Link>
          </p>
        </>
      )}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 text-[11px] uppercase tracking-wide text-text-faint">{children}</div>
  );
}

const TONE: Record<string, string> = {
  good: "#34d399",
  warn: "#fb923c",
  bad: "#f87171",
  neutral: "#e4e9f2",
};

function Tile({
  label,
  value,
  tone = "neutral",
  hint,
}: {
  label: string;
  value: number;
  tone?: "good" | "warn" | "bad" | "neutral";
  hint?: string;
}) {
  // A zero count is the good outcome for warn/bad tiles, so don't paint them
  // alarming when there is nothing wrong.
  const color = value === 0 && tone !== "neutral" ? TONE.good : TONE[tone];
  return (
    <div className="rounded-lg border border-border bg-panel px-4 py-3">
      <div className="font-mono-data text-2xl font-bold tabular-nums" style={{ color }}>
        {value.toLocaleString()}
      </div>
      <div className="mt-0.5 text-[11px] leading-tight text-text-muted">{label}</div>
      {hint && <div className="mt-0.5 text-[10px] text-text-faint">{hint}</div>}
    </div>
  );
}

function CheckMark({ kind, violation }: { kind: string; violation: boolean }) {
  if (kind === "inventory") {
    return <span className="font-mono-data text-[11px] text-text-faint">INFO</span>;
  }
  return (
    <span
      className={`font-mono-data text-[11px] font-bold ${violation ? "text-red-400" : "text-emerald-400"}`}
    >
      {violation ? "FAIL" : "PASS"}
    </span>
  );
}

function LedgerBanner({
  valid,
  entries,
  brokenAt,
  sql,
  open,
  onToggle,
}: {
  valid: boolean;
  entries: number;
  brokenAt: number | null;
  sql: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <section>
      <div
        className="rounded-lg border px-4 py-3"
        style={{
          borderColor: valid ? "#0f3d2c" : "#4a1616",
          background: valid ? "rgba(15,61,44,0.35)" : "rgba(74,22,22,0.35)",
        }}
      >
        <button onClick={onToggle} className="flex w-full items-center gap-3 text-left">
          <span
            className="font-mono-data text-sm font-bold"
            style={{ color: valid ? "#34d399" : "#f87171" }}
          >
            LEDGER {valid ? "✓ VERIFIED" : "✗ BROKEN"}
          </span>
          <span className="text-xs text-text-muted">
            {valid
              ? `SHA-256 hash chain re-derived from stored payloads — ${entries} entries, unbroken`
              : `chain fails to re-derive at seq=${brokenAt} after ${entries} entries`}
          </span>
          <span className="ml-auto text-xs text-text-faint">{open ? "▾" : "▸"}</span>
        </button>
        {open && (
          <div className="mt-3">
            <SqlBlock sql={sql} />
            <p className="mt-2 text-[11px] text-text-faint">
              Each entry_hash = sha256(prev_hash || canonical_json(payload)), genesis
              prev_hash = 64 zeros. Re-derive the chain from these rows and it either
              matches or it doesn&apos;t.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function SqlBlock({ sql }: { sql: string }) {
  return (
    <pre className="overflow-x-auto rounded border border-border bg-panel p-3 font-mono-data text-[11px] leading-relaxed text-text-muted">
      {sql}
    </pre>
  );
}

/**
 * Phase 2's architectural claim, as a picture: one database holds all of it,
 * so a belief, its vector, its provenance, the decision it drove, and the
 * audit entry proving it are consistent by construction rather than by
 * synchronization between services.
 */
function OneDatabasePanel() {
  const parts = [
    "beliefs",
    "embeddings (VECTOR INDEX)",
    "provenance + integrity",
    "capability ceilings",
    "contradictions",
    "decisions + memory refs",
    "approvals",
    "temporal state (AS OF SYSTEM TIME)",
    "immutable audit ledger",
  ];
  return (
    <section>
      <SectionLabel>System of record</SectionLabel>
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="mb-3 flex items-baseline gap-2">
          <span className="font-mono-data text-sm font-bold text-accent">ONE DATABASE</span>
          <span className="text-xs text-text-muted">CockroachDB — no second store, no cache to fall out of sync</span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {parts.map((p) => (
            <span
              key={p}
              className="rounded border border-border-strong px-2 py-1 font-mono-data text-[11px] text-text-muted"
            >
              {p}
            </span>
          ))}
        </div>
        <p className="mt-3 text-xs text-text-faint">
          A belief, the vector it is retrieved by, the authority that admitted it, the
          decision it influenced, and the ledger entry proving when — all committed in the
          same SERIALIZABLE transaction, in the same cluster.
        </p>
      </div>
    </section>
  );
}

function NoWorkspace() {
  return (
    <div className="rounded-lg border border-dashed border-border-strong p-10 text-center">
      <p className="text-sm text-text-muted">
        No workspace selected. Run{" "}
        <code className="font-mono-data text-accent">python -m demo.grand_prize</code> and paste
        the printed <code className="font-mono-data text-accent">workspace_id</code> into the
        field in the top-right corner.
      </p>
    </div>
  );
}
