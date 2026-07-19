from __future__ import annotations

import os
import stat

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
