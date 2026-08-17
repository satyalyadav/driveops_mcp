from __future__ import annotations

import json
import os
import sqlite3
import stat

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from driveops_mcp.audit import AuditStore


def test_audit_store_round_trips_plan_and_events(tmp_path):
    store = AuditStore(tmp_path / "driveops.db")
    plan = {
        "plan_id": "p1",
        "status": "planned",
        "confirmation": "APPLY-p1",
        "undo_confirmation": "UNDO-p1",
        "strategy": "by_created_month",
        "dry_run": True,
        "folder": {"id": "folder", "name": "Folder"},
        "summary": {"files_seen": 0, "folders_to_create": 0, "files_to_move": 0},
        "steps": [],
    }

    store.save_plan(plan)
    store.append_event(
        action="plan_created", status="ok", plan_id="p1", subject_id="folder"
    )

    assert store.get_plan("p1")["plan_id"] == "p1"
    events = store.list_events(plan_id="p1")
    assert len(events) == 1
    assert events[0]["action"] == "plan_created"
    if os.name != "nt":
        assert stat.S_IMODE(store.db_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600

    with sqlite3.connect(store.db_path) as conn:
        stored = conn.execute("select plan_json from plans where id = 'p1'").fetchone()[
            0
        ]
    assert stored.startswith("fernet:")
    assert '"plan_id": "p1"' not in stored


def test_audit_store_migrates_plaintext_plans(tmp_path):
    db_path = tmp_path / "driveops.db"
    plan = {
        "plan_id": "legacy",
        "status": "planned",
        "confirmation": "APPLY-legacy",
        "undo_confirmation": "UNDO-legacy",
        "strategy": "file_actions",
        "folder": {"id": "root"},
        "steps": [],
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table plans (
                id text primary key, status text not null, created_at text not null,
                updated_at text not null, confirmation text not null,
                undo_confirmation text not null, strategy text not null,
                folder_id text not null, dry_run integer not null,
                plan_json text not null, error text
            )
            """
        )
        conn.execute(
            "insert into plans values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "planned",
                "now",
                "now",
                "APPLY-legacy",
                "UNDO-legacy",
                "file_actions",
                "root",
                1,
                json.dumps(plan),
                None,
            ),
        )

    store = AuditStore(db_path)

    assert store.get_plan("legacy")["plan_id"] == "legacy"
    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            "select plan_json from plans where id = 'legacy'"
        ).fetchone()[0]
    assert stored.startswith("fernet:")


def test_audit_event_records_authenticated_client(tmp_path):
    store = AuditStore(tmp_path / "driveops.db")
    user = AuthenticatedUser(
        AccessToken(
            token="test-token",
            client_id="client-123",
            scopes=["driveops"],
            subject="owner",
        )
    )
    context_token = auth_context_var.set(user)
    try:
        store.append_event(action="test", status="ok")
    finally:
        auth_context_var.reset(context_token)

    event = store.list_events()[0]
    assert event["actor_client_id"] == "client-123"
    assert event["actor_subject"] == "owner"
