import type { CapabilityCeiling, MemoryStatus, SourceKind, Verdict } from "../api-client";

const INTEGRITY_STYLE: Record<SourceKind, { label: string; fg: string; bg: string }> = {
  human_confirmed: { label: "human_confirmed", fg: "#34d399", bg: "#0f3d2c" },
  verified_tool: { label: "verified_tool", fg: "#38bdf8", bg: "#0c3550" },
  agent_inferred: { label: "agent_inferred", fg: "#fbbf24", bg: "#4a3a0c" },
  untrusted_ingest: { label: "untrusted_ingest", fg: "#f87171", bg: "#4a1616" },
};

export function IntegrityBadge({ sourceKind }: { sourceKind: SourceKind }) {
  const s = INTEGRITY_STYLE[sourceKind];
  return (
    <span
      className="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-mono-data font-medium tracking-tight"
      style={{ color: s.fg, background: s.bg }}
      title={`source_kind: ${sourceKind}`}
    >
      {s.label}
    </span>
  );
}

const STATUS_STYLE: Record<MemoryStatus, { label: string; fg: string; bg: string; blocked?: boolean }> = {
  active: { label: "ACTIVE", fg: "#34d399", bg: "#0f3d2c" },
  superseded: { label: "SUPERSEDED", fg: "#8b95ac", bg: "#232a3a" },
  quarantined: { label: "BLOCKED", fg: "#fb923c", bg: "#4a2c0c", blocked: true },
  revoked: { label: "REVOKED", fg: "#f87171", bg: "#4a1616" },
};

export function StatusBadge({ status }: { status: MemoryStatus }) {
  const s = STATUS_STYLE[status];
  return (
    <span
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-mono-data font-bold tracking-wide"
      style={{ color: s.fg, background: s.bg, boxShadow: s.blocked ? `0 0 0 1px ${s.fg}` : undefined }}
    >
      {s.blocked && "⛔"} {s.label}
    </span>
  );
}

const VERDICT_STYLE: Record<Verdict, { fg: string; bg: string }> = {
  suppress: { fg: "#38bdf8", bg: "#0c3550" },
  escalate: { fg: "#fb923c", bg: "#4a2c0c" },
  allow: { fg: "#34d399", bg: "#0f3d2c" },
};

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const s = VERDICT_STYLE[verdict];
  return (
    <span
      className="inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wide"
      style={{ color: s.fg, background: s.bg }}
    >
      {verdict}
    </span>
  );
}

const CAPABILITY_LABEL: Record<CapabilityCeiling, string> = {
  informational: "informational",
  suppressive: "suppressive",
  actuating: "actuating",
};

export function CapabilityBadge({ capability }: { capability: CapabilityCeiling }) {
  return (
    <span className="inline-flex items-center rounded border border-border-strong px-1.5 py-0.5 text-[11px] font-mono-data text-text-muted">
      {CAPABILITY_LABEL[capability]}
    </span>
  );
}

export function IntegrityLevelDot({ level }: { level: number }) {
  const colors = ["#5b6480", "#f87171", "#fbbf24", "#38bdf8", "#34d399"];
  return (
    <span className="inline-flex items-center gap-0.5" title={`integrity_level: ${level}`}>
      {[1, 2, 3, 4].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: i <= level ? colors[level] : "#232a3a" }}
        />
      ))}
    </span>
  );
}
