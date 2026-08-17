/**
 * console/app/api-client.ts — typed fetch wrapper against the FastAPI
 * service from api/main.py (Prompt 5). No SDK/codegen — this project is
 * small enough that a hand-written client stays honest about exactly
 * which endpoints exist.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type SourceKind = "human_confirmed" | "verified_tool" | "agent_inferred" | "untrusted_ingest";
export type MemoryStatus = "active" | "superseded" | "quarantined" | "revoked";
export type Verdict = "suppress" | "escalate" | "allow";
export type CapabilityCeiling = "informational" | "suppressive" | "actuating";

export interface Decision {
  decision_id: string;
  agent_id: string | null;
  alert_ref: string;
  alert_payload: Record<string, unknown>;
  verdict: Verdict;
  rationale: string;
  decided_hlc: string;
  created_at: string;
}

export interface MemoryRef {
  memory_id: string;
  claim: string;
  status: MemoryStatus;
  source_kind: SourceKind;
  rank: number;
  semantic_score: number;
  eff_confidence: number;
  integrity_level: number;
  total_score: number;
  influence: number;
}

export interface DecisionDetail extends Decision {
  memory_refs: MemoryRef[];
}

export interface Memory {
  memory_id: string;
  status: MemoryStatus;
  claim: string;
  subject_key: string;
  predicate: string;
  object_value: string;
  source_kind: SourceKind;
  integrity_level: number;
  capability_ceiling: CapabilityCeiling;
  confidence: number;
  eff_confidence: number;
  corroborations: number;
  refutations: number;
  created_at: string;
  updated_at: string;
}

export interface BlastRadiusDecision {
  decision_id: string;
  alert_ref: string;
  verdict: Verdict;
  decided_hlc: string;
  created_at: string;
}

export interface BlastRadiusResponse {
  memory_id: string;
  decisions: BlastRadiusDecision[];
  count: number;
}

export interface RevokeResult {
  memory_id: string;
  status: string;
  reason: string;
  actor: string;
  blast_radius: BlastRadiusDecision[];
  blast_radius_count: number;
}

export interface BeliefDiffEntry {
  memory_id: string;
  claim: string;
  status_then: MemoryStatus | null;
  status_now: MemoryStatus | null;
}

export interface RewindPreview {
  rewind_id: string;
  workspace_id: string;
  target_hlc: string;
  trigger_memory: string;
  belief_diff: BeliefDiffEntry[];
  decisions_in_blast_radius: number;
  blast_radius: BlastRadiusDecision[];
  state: "awaiting_approval" | "applied";
  created_at: string;
}

export interface RewindReplay {
  original_decision_id: string;
  alert_ref: string;
  verdict_before: Verdict;
  verdict_after: Verdict;
  flipped: boolean;
  replay_decision_id: string;
}

export interface RewindApplyResult {
  rewind_id: string;
  state: "applied";
  verdict_flips: number;
  decisions_replayed: number;
  replays: RewindReplay[];
}

export interface LedgerEntry {
  seq: number;
  event_type: string;
  payload: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
  exported_at: string | null;
  created_at: string;
}

export interface LedgerVerifyResult {
  valid: boolean;
  broken_at_seq: number | null;
  entries_checked: number;
}

export interface Approval {
  approval_id: string;
  subject_type: "memory" | "decision";
  subject_id: string;
  reason: string;
  actor: string | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  resolved_at: string | null;
}

/**
 * Audit types mirror api/routes/audit.py, which serves audit/auditor.py.
 *
 * `sql` on each check is the literal query that produced `rows`. It is shown
 * in the UI on purpose: the console displaying a number is convenience, and
 * a viewer re-running that query against the CockroachDB Cloud Managed MCP
 * Server is the part that actually carries trust.
 */
export interface AuditCheck {
  name: string;
  question: string;
  kind: "violation" | "inventory";
  count: number;
  is_violation: boolean;
  rows: Record<string, unknown>[];
  truncated: boolean;
  sql: string | null;
}

export interface AuditMetrics {
  active_memories: number;
  revoked_memories: number;
  quarantined_memories: number;
  superseded_memories: number;
  decisions: number;
  pending_approvals: number;
  decisions_touching_revoked: number;
  integrity_violations: number;
  unresolved_contradictions: number;
}

export interface AuditReport {
  workspace_id: string;
  passed: boolean;
  metrics: AuditMetrics;
  ledger: {
    valid: boolean;
    entries_checked: number;
    broken_at_seq: number | null;
    sql: string;
  };
  checks: AuditCheck[];
  errors: string[];
}

export interface AuditQueries {
  workspace_id: string;
  note: string;
  metrics_sql: string;
  ledger_sql: string;
  checks: { name: string; question: string; kind: string; sql: string }[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      // response wasn't JSON, fall through with statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  listDecisions: (workspaceId: string, limit = 50) =>
    request<Decision[]>(`/workspaces/${workspaceId}/decisions?limit=${limit}`),

  getDecision: (workspaceId: string, decisionId: string) =>
    request<DecisionDetail>(`/workspaces/${workspaceId}/decisions/${decisionId}`),

  listMemories: (workspaceId: string, status?: MemoryStatus) =>
    request<Memory[]>(`/workspaces/${workspaceId}/memories${status ? `?status=${status}` : ""}`),

  blastRadius: (workspaceId: string, memoryId: string) =>
    request<BlastRadiusResponse>(`/workspaces/${workspaceId}/memories/${memoryId}/blast_radius`),

  revokeMemory: (workspaceId: string, memoryId: string, reason: string, actor: string) =>
    request<RevokeResult>(`/workspaces/${workspaceId}/memories/${memoryId}/revoke`, {
      method: "POST",
      body: JSON.stringify({ reason, actor }),
    }),

  listApprovals: (workspaceId: string, status = "pending") =>
    request<Approval[]>(`/workspaces/${workspaceId}/approvals?status=${status}`),

  createRewind: (workspaceId: string, targetHlc: string, triggerMemory: string) =>
    request<RewindPreview>(`/workspaces/${workspaceId}/rewind`, {
      method: "POST",
      body: JSON.stringify({ target_hlc: targetHlc, trigger_memory: triggerMemory }),
    }),

  applyRewind: (workspaceId: string, rewindId: string) =>
    request<RewindApplyResult>(`/workspaces/${workspaceId}/rewind/${rewindId}/apply`, {
      method: "POST",
    }),

  tailLedger: (workspaceId: string, limit = 50) =>
    request<LedgerEntry[]>(`/workspaces/${workspaceId}/ledger?limit=${limit}`),

  verifyLedger: (workspaceId: string) =>
    request<LedgerVerifyResult>(`/workspaces/${workspaceId}/ledger/verify`),

  audit: (workspaceId: string) => request<AuditReport>(`/workspaces/${workspaceId}/audit`),

  auditQueries: (workspaceId: string) =>
    request<AuditQueries>(`/workspaces/${workspaceId}/audit/queries`),
};

/**
 * Which workspace to show when the visitor hasn't picked one. Used by
 * workspace-context so the deployed demo renders real data on first paint
 * instead of an empty state telling the visitor to run a seed script.
 * Returns "" rather than throwing when nothing is seeded or the API is
 * unreachable — the views already render their own empty state, and a
 * rejected promise here would surface as an unhandled rejection.
 */
export async function fetchDemoWorkspace(): Promise<string> {
  try {
    const res = await request<{ workspace_id: string | null }>("/demo-workspace");
    return res.workspace_id ?? "";
  } catch {
    return "";
  }
}
