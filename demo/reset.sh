#!/usr/bin/env bash
# demo/reset.sh — wipe + reseed the demo workspace for repeat filming.
set -euo pipefail

: "${PALIMPSEST_DSN:?Set PALIMPSEST_DSN first. See database/README.md.}"

python - <<'PY'
import os
from demo.seed import wipe, seed

dsn = os.environ["PALIMPSEST_DSN"]
deleted = wipe(dsn)
print(f"Wiped {deleted} existing demo workspace(s).")
info = seed(dsn)
print(f"Reseeded: workspace_id={info['workspace_id']} agent_id={info['agent_id']}")
PY

echo "Demo workspace reset. Run: python -m demo.attack_scenario"
