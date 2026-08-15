# Palimpsest Console

The forensic SOC console — decision timeline, belief store, and rewind.
Not a chat window (see `CONTEXT.md`'s demo notes): a dark, data-dense
operator UI meant to read clearly on a screen recording.

## Run locally

```bash
npm install
npm run dev
```

Requires `api/main.py` running separately (see the repo root's
`database/README.md` and the FastAPI service under `api/`):

```bash
# from the repo root, in a separate terminal
export PALIMPSEST_DSN="postgresql://root@localhost:26257/palimpsest?sslmode=disable"
uvicorn api.main:app --reload
```

By default the console calls `http://localhost:8000`. Override with
`NEXT_PUBLIC_API_BASE_URL` if the API runs elsewhere.

## Pointing at a workspace

The console needs a `workspace_id` to know which workspace to display.
Seed one (`python -m demo.seed` from the repo root prints it), then paste
it into the input in the top-right corner — it's stored in
`localStorage` so it survives reloads and doesn't need a rebuild when you
reseed with a fresh workspace.

## Views

- **`/timeline`** — live decision feed (polls every 2s), expandable to
  show which memories influenced each verdict, with integrity/status
  badges.
- **`/memories`** — the belief store, filterable by status, with a Blast
  Radius lookup per memory and a SQL pane showing the quarantine-check
  query.
- **`/rewind`** — pick a past decision's HLC and a suspected poisoned
  memory, preview the belief diff and blast radius, then Apply Replay —
  `verdict_flips` is the headline number.
