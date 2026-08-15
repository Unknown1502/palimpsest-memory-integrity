# Opening the upstream Agent Skills PR

`skills/audit-agent-memory-integrity/SKILL.md` is written to be
contributed to
[`cockroachlabs/cockroachdb-skills`](https://github.com/cockroachlabs/cockroachdb-skills)
under its `security-and-governance` domain. This is prepared so you can
open the PR in under 5 minutes — the commands below are exact, copy-paste
ready.

## 1. Fork and clone

```bash
gh repo fork cockroachlabs/cockroachdb-skills --clone
cd cockroachdb-skills
```

(Or fork via the GitHub UI, then `git clone
git@github.com:<your-username>/cockroachdb-skills.git && cd
cockroachdb-skills`.)

## 2. Add the skill

```bash
mkdir -p skills/security-and-governance/audit-agent-memory-integrity
cp /path/to/palimpsest/skills/audit-agent-memory-integrity/SKILL.md \
   skills/security-and-governance/audit-agent-memory-integrity/SKILL.md
```

Replace `/path/to/palimpsest` with this repo's actual path.

## 3. Validate against their spec checker

```bash
python scripts/validate-spec.py skills/security-and-governance/audit-agent-memory-integrity/SKILL.md
```

Fix anything it flags before proceeding. If the script requires
additional frontmatter fields this repo's template expects beyond what's
in `SKILL.md` already (`name`, `description`, `license`), add them to
match the repo's existing skills under `skills/security-and-governance/`
— check a neighboring skill's frontmatter as the reference for any
repo-specific convention not covered by the general Agent Skills
Specification (agentskills.io).

## 4. Branch, commit, push

```bash
git checkout -b add-audit-agent-memory-integrity-skill
git add skills/security-and-governance/audit-agent-memory-integrity/SKILL.md
git commit -m "Add audit-agent-memory-integrity skill

Audits an existing CockroachDB-backed AI agent memory table for missing
provenance tracking, vector-index tenant/quarantine isolation gaps,
absent contradiction handling, and missing temporal audit trails.
Read-only, safety-gated, with a structured severity-ranked findings
output."
git push -u origin add-audit-agent-memory-integrity-skill
```

## 5. Open the PR

```bash
gh pr create \
  --repo cockroachlabs/cockroachdb-skills \
  --title "Add audit-agent-memory-integrity skill (security-and-governance)" \
  --body "Adds a skill that audits an existing CockroachDB-backed AI agent memory/belief table for four specific, checkable integrity gaps: missing provenance tracking, vector-index tenant/quarantine isolation (a CockroachDB C-SPANN-specific concern — prefix columns partition the index into separate per-value trees), absent contradiction/adjudication handling, and missing temporal audit trail (GC TTL for AS OF SYSTEM TIME, or a hash-chained ledger fallback). Read-only by design; reports a structured, severity-ranked findings list rather than taking any write action.

Built and validated against a real schema (a CockroachDB-backed agent memory system with all four properties this skill checks for) while building a submission for the CockroachDB x AWS hackathon."
```

## What to check before merging (their side, not yours)

The upstream repo's own CI runs `scripts/validate-spec.py` against every
skill on PR — if step 3 above passed locally, CI should pass too. Beyond
that, expect maintainers to review for consistency with their existing
`security-and-governance` skills' tone and structure; skim 1-2 neighboring
skills in that domain before opening the PR if you want to preempt any
style feedback.
