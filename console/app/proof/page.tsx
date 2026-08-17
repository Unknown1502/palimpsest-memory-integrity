"use client";

/**
 * app/proof/page.tsx — the evidence page.
 *
 * Two panels. The CockroachDB one shows the schema-level mechanisms the
 * security argument rests on, quoted verbatim from database/schema.sql, plus
 * the live read-only queries anyone can re-run. The AWS one lists only
 * services with a real role in the running system — nothing is listed to pad
 * a count.
 *
 * The point of this page is to be leavable: everything on it is something a
 * skeptic can check somewhere other than here.
 */

import { useEffect, useState } from "react";
import { api, ApiError, type AuditQueries } from "../api-client";
import { useWorkspace } from "../workspace-context";

export default function ProofPage() {
  const { workspaceId } = useWorkspace();
  const [queries, setQueries] = useState<AuditQueries | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId) return;
    api
      .auditQueries(workspaceId)
      .then(setQueries)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [workspaceId]);

  return (
    <div className="space-y-8">
      <header>
        <h1 className="text-lg font-semibold">Proof</h1>
        <p className="mt-1 text-sm text-text-muted">
          Every claim below is checkable without trusting this console, this API, or the
          project&apos;s own test suite.
        </p>
      </header>

      <section>
        <PanelTitle accent="#7b6bd6">CockroachDB — the system of record</PanelTitle>
        <div className="space-y-3">
          <Evidence
            title="Distributed vector indexing (C-SPANN), with prefix columns"
            claim="Quarantining a belief moves its vector into a different k-means tree — it is not a WHERE clause hiding a row."
            code={`VECTOR INDEX memories_vec_idx (workspace_id, status, embedding)`}
            note="CockroachDB maintains a separate tree per distinct prefix value, so (workspace_id, status) gives tenant isolation and quarantine isolation from the same mechanism."
          />
          <Evidence
            title="The integrity lattice, as a database constraint"
            claim="Even if every line of Python were bypassed, the database still refuses to store a belief holding authority its source cannot justify."
            code={`CONSTRAINT capability_requires_integrity CHECK (
  (capability_ceiling = 'informational')
  OR (capability_ceiling = 'suppressive' AND integrity_level >= 3)
  OR (capability_ceiling = 'actuating'   AND integrity_level >= 4)
)`}
            note="Enforced twice on purpose: memory/lattice.py rejects before a connection opens, this CHECK is the backstop."
          />
          <Evidence
            title="Atomic contradiction adjudication"
            claim="Detection, arbitration, the state change, and the audit entry commit or abort together — there is no window where a belief changed status but the ledger doesn't say so."
            code={`BEGIN;
  SELECT ... FOR UPDATE (workspace_id, subject_key, predicate)
  -- supersede / quarantine / adjudicate
  INSERT INTO memory_ledger (prev_hash -> entry_hash)
COMMIT;   -- SERIALIZABLE, with a real 40001 retry loop`}
            note="tests/test_concurrent_admission.py forces genuine 40001 SerializationFailures and asserts the retry loop absorbs them."
          />
          <Evidence
            title="Temporal reconstruction"
            claim="What the agent believed at decision time is recovered from the database's own MVCC history — not from a log this project wrote about itself."
            code={`SELECT ... FROM memories
AS OF SYSTEM TIME '<decisions.decided_hlc>'
WHERE workspace_id = ...`}
            note="decided_hlc is cluster_logical_timestamp() captured at decision time, never now()."
          />
          <Evidence
            title="Tamper-evident ledger"
            claim="entry_hash = sha256(prev_hash || canonical_json(payload)). Rewrite any committed payload and the chain stops re-deriving at that exact sequence number."
            code={queries?.ledger_sql ?? "SELECT seq, payload, prev_hash, entry_hash FROM memory_ledger ..."}
            note="tests/test_auditor.py mutates a payload behind the gate's back and asserts the auditor reports the break at the right seq."
          />
        </div>
      </section>

      <section>
        <PanelTitle accent="#4fd1e8">Verify it yourself, via MCP</PanelTitle>
        <div className="rounded-lg border border-border bg-panel p-4">
          <p className="text-sm text-text-muted">
            {queries?.note ??
              "Read-only queries you can run against the CockroachDB Cloud Managed MCP Server, with this project entirely out of the loop."}
          </p>
          {error && <p className="mt-2 text-sm text-red-300">{error}</p>}
          {!workspaceId && (
            <p className="mt-2 text-sm text-text-faint">Select a workspace to load the queries.</p>
          )}
          {queries && (
            <div className="mt-3 space-y-2">
              {queries.checks.map((c) => (
                <details key={c.name} className="rounded border border-border bg-bg">
                  <summary className="cursor-pointer px-3 py-2 text-xs">
                    <span className="font-mono-data text-text">{c.name}</span>
                    <span className="ml-2 text-text-faint">{c.question}</span>
                  </summary>
                  <pre className="overflow-x-auto border-t border-border px-3 py-2 font-mono-data text-[11px] leading-relaxed text-text-muted">
                    {c.sql}
                  </pre>
                </details>
              ))}
            </div>
          )}
          <p className="mt-3 text-xs text-text-faint">
            Same list from the CLI:{" "}
            <code className="font-mono-data text-accent">python -m audit.auditor --print-sql</code>
          </p>
        </div>
      </section>

      <section>
        <PanelTitle accent="#e08b1a">AWS — what each service actually does here</PanelTitle>
        <div className="overflow-hidden rounded-lg border border-border bg-panel">
          {AWS_SERVICES.map((s) => (
            <div
              key={s.name}
              className="flex flex-col gap-1 border-b border-border px-4 py-3 last:border-b-0 md:flex-row md:items-baseline md:gap-4"
            >
              <span className="font-mono-data text-xs text-accent w-48 shrink-0">{s.name}</span>
              <span className="flex-1 text-sm text-text-muted">{s.role}</span>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-text-faint">
          Deliberately not deployed: Step Functions, API Gateway, multi-region. Each would have
          added an service to the list without doing real work in this system — see
          infrastructure/README.md.
        </p>
      </section>
    </div>
  );
}

const AWS_SERVICES = [
  {
    name: "Lambda",
    role: "GateHandler runs the same FastAPI app as local (api/main.py via Mangum) behind a Function URL, and serves the static console from the same origin. LedgerExportHandler exports the hash chain.",
  },
  {
    name: "Bedrock — Titan V2",
    role: "Text Embeddings V2, 1024 dims, L2-normalized before insert so the <-> operator ranks equivalently to cosine. Every retrieval candidate is scored by these vectors.",
  },
  {
    name: "S3 + Object Lock",
    role: "Ledger export target in governance mode with ~7-year retention, enabled at bucket creation — the only moment it can be. Exported entries cannot be silently rewritten.",
  },
  {
    name: "EventBridge",
    role: "Fires the ledger export on a 5-minute schedule, so tamper-evidence leaves the database it is evidence about.",
  },
  {
    name: "Secrets Manager",
    role: "PALIMPSEST_DSN and the Anthropic key, injected into both Lambdas via CloudFormation dynamic references — never a plaintext value in the CDK source or a build artifact.",
  },
  {
    name: "CloudWatch Logs",
    role: "7-day retention on both functions, including the read-only middleware's refusals of destructive routes on the public demo.",
  },
];

function PanelTitle({ children, accent }: { children: React.ReactNode; accent: string }) {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="h-2 w-2 rounded-full" style={{ background: accent }} />
      <h2 className="text-sm font-semibold tracking-wide">{children}</h2>
    </div>
  );
}

function Evidence({
  title,
  claim,
  code,
  note,
}: {
  title: string;
  claim: string;
  code: string;
  note: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-panel p-4">
      <div className="text-sm font-semibold">{title}</div>
      <p className="mt-1 text-sm text-text-muted">{claim}</p>
      <pre className="mt-2 overflow-x-auto rounded border border-border bg-bg p-3 font-mono-data text-[11px] leading-relaxed text-text-muted">
        {code}
      </pre>
      <p className="mt-2 text-[11px] text-text-faint">{note}</p>
    </div>
  );
}
