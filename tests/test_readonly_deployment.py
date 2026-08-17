"""
tests/test_readonly_deployment.py — the public demo's write protection.

The deployed Function URL has no authentication of its own. The only thing
standing between the internet and `revoke every belief` / `replay decisions
in a loop against a metered API key` is api/main.py's ReadOnlyMiddleware. A
regression there is not a cosmetic bug, it is an open door plus a bill, and
it would be invisible locally because local runs have READONLY off.

api.main reads PALIMPSEST_READONLY at import time, so these tests reload the
module under each setting rather than trying to toggle a live app.
"""

from __future__ import annotations

import importlib
import os
import uuid

import pytest
from fastapi.testclient import TestClient


def _client(readonly: bool) -> TestClient:
    os.environ["PALIMPSEST_READONLY"] = "true" if readonly else "false"
    import api.main

    importlib.reload(api.main)
    return TestClient(api.main.app)


@pytest.fixture(autouse=True)
def _restore_env():
    before = os.environ.get("PALIMPSEST_READONLY")
    yield
    if before is None:
        os.environ.pop("PALIMPSEST_READONLY", None)
    else:
        os.environ["PALIMPSEST_READONLY"] = before
    import api.main

    importlib.reload(api.main)


# (path template, description) for every route that destroys belief state or
# spends money. If a new one is added to the API it belongs here too.
DESTRUCTIVE = [
    ("/workspaces/{ws}/memories/{id}/revoke", "revoking a belief"),
    ("/workspaces/{ws}/rewind/{id}/apply", "replaying decisions (real LLM spend)"),
    ("/workspaces/{ws}/approvals/{id}/resolve", "mutating the approval queue"),
]


@pytest.mark.parametrize("path_tpl,description", DESTRUCTIVE)
def test_destructive_routes_are_blocked_when_readonly(path_tpl: str, description: str):
    client = _client(readonly=True)
    path = path_tpl.format(ws=str(uuid.uuid4()), id=str(uuid.uuid4()))

    res = client.post(path, json={"reason": "x", "actor": "y", "decision": "approved"})

    assert res.status_code == 403, f"{description} was NOT blocked: {res.status_code}"
    body = res.json()
    assert body.get("readonly") is True
    assert "read-only" in body.get("detail", "").lower()


@pytest.mark.parametrize("path_tpl,description", DESTRUCTIVE)
def test_destructive_routes_are_reachable_when_not_readonly(path_tpl: str, description: str):
    """
    The guard must be the read-only flag, not a permanently disabled route.
    With READONLY off these should get PAST the middleware -- they will then
    fail on a nonexistent workspace/memory, which is exactly the point: any
    status other than 403 proves the middleware let them through.
    """
    client = _client(readonly=False)
    path = path_tpl.format(ws=str(uuid.uuid4()), id=str(uuid.uuid4()))

    res = client.post(path, json={"reason": "x", "actor": "y", "decision": "approved"})

    assert res.status_code != 403, f"{description} was blocked with READONLY off"


def test_read_routes_still_work_when_readonly(workspace_id: str):
    """Read-only must mean read-ONLY, not read-nothing."""
    client = _client(readonly=True)

    assert client.get("/health").json() == {"status": "ok", "readonly": True}
    assert client.get(f"/workspaces/{workspace_id}/memories").status_code == 200
    assert client.get(f"/workspaces/{workspace_id}/decisions").status_code == 200
    assert client.get(f"/workspaces/{workspace_id}/ledger/verify").status_code == 200
    assert client.get(f"/workspaces/{workspace_id}/audit").status_code == 200


def test_audit_endpoints_are_safe_under_readonly(workspace_id: str):
    """
    The audit surface is the one a public visitor is most likely to hit, and
    it must expose the verification queries without exposing a way to run
    anything else.
    """
    client = _client(readonly=True)

    report = client.get(f"/workspaces/{workspace_id}/audit").json()
    assert "metrics" in report and "checks" in report
    assert all(c["sql"] for c in report["checks"]), "every check must ship the SQL behind it"

    queries = client.get(f"/workspaces/{workspace_id}/audit/queries").json()
    sql_blobs = [c["sql"] for c in queries["checks"]] + [queries["ledger_sql"], queries["metrics_sql"]]
    for sql in sql_blobs:
        lowered = sql.lower()
        assert lowered.lstrip().startswith("select"), f"non-SELECT exposed: {sql[:60]}"
        for forbidden in ("insert ", "update ", "delete ", "drop ", "alter ", "grant "):
            assert forbidden not in lowered, f"mutating keyword {forbidden!r} in exposed SQL"


def test_rewind_preview_stays_available_on_the_public_demo():
    """
    POST /rewind is deliberately NOT blocked: it computes a preview and
    writes one inert row, with no LLM calls and no belief state change. That
    preview is the project's whole visual argument, so a read-only
    deployment that hid it would be protecting the wrong thing.
    """
    client = _client(readonly=True)
    res = client.post(
        f"/workspaces/{uuid.uuid4()}/rewind",
        json={"target_hlc": "1234567890.0000000000", "trigger_memory": str(uuid.uuid4())},
    )
    assert res.status_code != 403
