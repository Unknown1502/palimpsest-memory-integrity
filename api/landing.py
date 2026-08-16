"""
api/landing.py — the HTML page served at `/`.

Exists because the deployed Function URL is the project's public demo link:
without a route at `/`, a visitor's first impression is FastAPI's bare
`{"detail":"Not Found"}`, which reads as a broken deployment rather than a
working API with no index route.

The page is deliberately self-contained (no external CSS/JS/fonts) so it
renders identically offline, behind a corporate proxy, and from a Lambda
cold start, and so it can never be broken by a third-party CDN.

It picks a real workspace_id out of the database at request time and builds
working links from it, rather than hardcoding one that would rot the moment
the demo data is reseeded. If the database is unreachable the page still
renders -- it degrades to explaining the project without live links, which
is strictly better than a stack trace.
"""

from __future__ import annotations

import html
import logging

import psycopg

logger = logging.getLogger("palimpsest.api")

REPO_URL = "https://github.com/Unknown1502/palimpsest-memory-integrity"


def pick_demo_workspace(dsn: str) -> tuple[str | None, dict]:
    """
    Newest workspace that actually has decisions -- an empty workspace makes
    every link on the page look broken even though the API is fine.
    """
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT w.workspace_id,
                           (SELECT count(*) FROM decisions d WHERE d.workspace_id = w.workspace_id),
                           (SELECT count(*) FROM memories m WHERE m.workspace_id = w.workspace_id)
                    FROM workspaces w
                    ORDER BY (SELECT count(*) FROM decisions d WHERE d.workspace_id = w.workspace_id) DESC,
                             w.created_at DESC
                    LIMIT 1
                    """
                )
                row = cur.fetchone()
                if not row:
                    return None, {}
                return str(row[0]), {"decisions": row[1], "memories": row[2]}
    except Exception as exc:  # noqa: BLE001 - the page must render regardless
        logger.warning("landing page could not reach the database: %s", exc)
        return None, {}


_STYLE = """
:root{--bg:#0a0d12;--panel:#11161f;--line:#1e2733;--fg:#c7d3e0;--dim:#7d8b9c;
--accent:#2bb3c9;--warn:#c9a227;--bad:#b4304a;--good:#3fa46a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.wrap{max-width:860px;margin:0 auto;padding:48px 24px 72px}
h1{font-size:22px;letter-spacing:.14em;margin:0 0 6px;color:#fff}
h1 .dot{color:var(--accent)}
.sub{color:var(--dim);margin:0 0 28px}
h2{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--dim);
margin:34px 0 12px;font-weight:600}
.card{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:16px 18px}
p{margin:0 0 12px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
ul{margin:0;padding-left:18px}
li{margin:7px 0}
code{background:#0d1218;border:1px solid var(--line);border-radius:3px;padding:1px 5px;color:#9fb4c7}
.pill{display:inline-block;font-size:11px;letter-spacing:.1em;text-transform:uppercase;
padding:3px 9px;border-radius:99px;border:1px solid;margin-right:6px}
.pill.ro{color:var(--warn);border-color:#5a4a12;background:#241d07}
.pill.live{color:var(--good);border-color:#1d5236;background:#0c2418}
.blocked{color:var(--bad)}
.meta{color:var(--dim);font-size:12px;margin-top:6px}
footer{margin-top:40px;color:var(--dim);font-size:12px;
border-top:1px solid var(--line);padding-top:16px}
"""


def render(dsn: str, readonly: bool) -> str:
    workspace_id, counts = pick_demo_workspace(dsn)

    if workspace_id:
        ws = html.escape(workspace_id)
        live = f"""
        <h2>Live data — try it</h2>
        <div class="card">
          <ul>
            <li><a href="/workspaces/{ws}/decisions?limit=10">/decisions</a>
                — triage verdicts, each citing the memories that drove it</li>
            <li><a href="/workspaces/{ws}/memories">/memories</a>
                — the belief store, with integrity level and capability ceiling per belief</li>
            <li><a href="/workspaces/{ws}/ledger">/ledger</a>
                — the hash-chained audit trail</li>
            <li><a href="/workspaces/{ws}/ledger/verify"><b>/ledger/verify</b></a>
                — re-derives every hash from scratch and reports whether the chain is intact</li>
            <li><a href="/workspaces/{ws}/approvals">/approvals</a>
                — the human-in-the-loop queue</li>
          </ul>
          <div class="meta">workspace <code>{ws}</code>
            &nbsp;·&nbsp; {counts.get("decisions", 0)} decisions
            &nbsp;·&nbsp; {counts.get("memories", 0)} memories</div>
        </div>"""
    else:
        live = """
        <h2>Live data</h2>
        <div class="card">
          <p>No seeded workspace is reachable right now, so there are no live links
             to hand you. The API itself is up — <a href="/health">/health</a>
             confirms it.</p>
          <p class="meta">Endpoints follow
             <code>/workspaces/{workspace_id}/...</code>; run
             <code>python -m demo.seed</code> against this database to create one.</p>
        </div>"""

    ro_block = """
        <h2>Why some endpoints return 403</h2>
        <div class="card">
          <p>This deployment is <b>read-only</b>. It has no authentication layer of its
             own, so the routes that would let anyone destroy state or spend real
             money are disabled here:</p>
          <ul>
            <li><span class="blocked">POST /memories/{id}/revoke</span> — destroys belief state</li>
            <li><span class="blocked">POST /rewind/{id}/apply</span> — replays decisions, real LLM spend</li>
            <li><span class="blocked">POST /approvals/{id}/resolve</span> — mutates the approval queue</li>
          </ul>
          <p style="margin-top:12px"><code>POST /rewind</code> is deliberately still
             open — it computes the belief-diff and blast-radius <i>preview</i>, makes
             no model calls, and changes no belief. That preview is the argument this
             project exists to make.</p>
        </div>""" if readonly else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Palimpsest API</title><style>{_STYLE}</style></head>
<body><div class="wrap">
  <h1><span class="dot">●</span> PALIMPSEST</h1>
  <p class="sub">Memory integrity layer for AI agents</p>
  <p>
    <span class="pill live">Live</span>
    {'<span class="pill ro">Read-only</span>' if readonly else ''}
  </p>

  <h2>What this is</h2>
  <div class="card">
    <p>Agents write beliefs to long-term memory and read them back with total
       trust. Poison that memory once — plant an instruction in a ticket comment
       an agent ingests — and every future retrieval launders it back in as
       trusted context.</p>
    <p style="margin:0">Palimpsest gates every belief through an integrity
       lattice (a belief's source authority caps what kind of decision it may
       influence), adjudicates contradictions atomically inside a single
       CockroachDB <code>SERIALIZABLE</code> transaction, and can rewind:
       reconstruct what an agent believed at any past decision via
       <code>AS OF SYSTEM TIME</code>, find every decision a poisoned belief
       touched, and replay them against corrected memory.</p>
  </div>

  {live}
  {ro_block}

  <footer>
    Source, architecture diagrams, threat model and setup:
    <a href="{REPO_URL}">{REPO_URL}</a><br>
    Running on CockroachDB Cloud · AWS Lambda · Amazon Titan embeddings.
  </footer>
</div></body></html>"""
