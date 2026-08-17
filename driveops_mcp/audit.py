"""SQLite plan and audit-event storage."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.middleware.auth_context import get_access_token

from .auth import state_dir
from .schemas import now_iso

ENCRYPTED_PLAN_PREFIX = "fernet:"


class AuditStore:
    def __init__(
        self, db_path: Path | None = None, key_path: Path | None = None
    ) -> None:
        self.db_path = db_path or state_dir() / "driveops.db"
        self.key_path = key_path or self.db_path.with_suffix(".key")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.db_path.parent.chmod(0o700)
        self.db_path.touch(mode=0o600, exist_ok=True)
        if os.name != "nt":
            self.db_path.chmod(0o600)
        self._cipher = Fernet(self._load_encryption_key())
        self._init_db()

    def _load_encryption_key(self) -> bytes:
        configured = os.getenv("DRIVEOPS_PLAN_ENCRYPTION_KEY")
        if configured:
            key = configured.strip().encode("ascii")
        else:
            try:
                fd = os.open(
                    self.key_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key() + b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            key = self.key_path.read_bytes().strip()
            if os.name != "nt":
                self.key_path.chmod(0o600)
        try:
            Fernet(key)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "DRIVEOPS_PLAN_ENCRYPTION_KEY (or the local key file) is not a valid Fernet key."
            ) from exc
        return key

    def _encode_plan(self, plan: dict[str, Any]) -> str:
        payload = json.dumps(plan, sort_keys=True).encode("utf-8")
        return ENCRYPTED_PLAN_PREFIX + self._cipher.encrypt(payload).decode("ascii")

    def _decode_plan(self, payload: str) -> dict[str, Any]:
        if not payload.startswith(ENCRYPTED_PLAN_PREFIX):
            return json.loads(payload)
        token = payload.removeprefix(ENCRYPTED_PLAN_PREFIX).encode("ascii")
        try:
            raw = self._cipher.decrypt(token)
        except InvalidToken as exc:
            raise RuntimeError(
                "A stored plan cannot be decrypted. Restore the encryption key used to create it."
            ) from exc
        return json.loads(raw)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        if os.name != "nt":
            self.db_path.chmod(0o600)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists plans (
                    id text primary key,
                    status text not null,
                    created_at text not null,
                    updated_at text not null,
                    confirmation text not null,
                    undo_confirmation text not null,
                    strategy text not null,
                    folder_id text not null,
                    dry_run integer not null,
                    plan_json text not null,
                    error text
                )
                """
            )
            conn.execute(
                """
                create table if not exists audit_events (
                    id text primary key,
                    plan_id text,
                    action text not null,
                    status text not null,
                    subject_id text,
                    before_json text,
                    after_json text,
                    message text,
                    actor_client_id text,
                    actor_subject text,
                    created_at text not null
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("pragma table_info(audit_events)").fetchall()
            }
            if "actor_client_id" not in columns:
                conn.execute("alter table audit_events add column actor_client_id text")
            if "actor_subject" not in columns:
                conn.execute("alter table audit_events add column actor_subject text")
            rows = conn.execute(
                "select id, plan_json from plans where plan_json not like ?",
                (f"{ENCRYPTED_PLAN_PREFIX}%",),
            ).fetchall()
            for row in rows:
                plan = json.loads(row["plan_json"])
                conn.execute(
                    "update plans set plan_json = ? where id = ?",
                    (self._encode_plan(plan), row["id"]),
                )

    def save_plan(self, plan: dict[str, Any]) -> None:
        ts = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                insert into plans (
                    id, status, created_at, updated_at, confirmation,
                    undo_confirmation, strategy, folder_id, dry_run, plan_json, error
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan["plan_id"],
                    plan["status"],
                    ts,
                    ts,
                    plan["confirmation"],
                    plan["undo_confirmation"],
                    plan["strategy"],
                    plan["folder"]["id"],
                    int(bool(plan.get("dry_run", True))),
                    self._encode_plan(plan),
                    plan.get("error"),
                ),
            )

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "select * from plans where id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Plan {plan_id} not found.")
        return self._decode_plan(row["plan_json"])

    def latest_plan(
        self, *, status: str | None = None, statuses: set[str] | None = None
    ) -> dict[str, Any]:
        if status and statuses:
            raise ValueError("Provide status or statuses, not both.")
        sql = "select * from plans"
        params: list[Any] = []
        if status:
            sql += " where status = ?"
            params.append(status)
        elif statuses:
            ordered = sorted(statuses)
            sql += " where status in (" + ",".join("?" for _ in ordered) + ")"
            params.extend(ordered)
        sql += " order by created_at desc limit 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            requested = status or ", ".join(sorted(statuses or []))
            suffix = f" with status {requested}" if requested else ""
            raise KeyError(f"No DriveOps plan{suffix} found.")
        return self._decode_plan(row["plan_json"])

    def update_plan(
        self,
        plan: dict[str, Any],
        *,
        status: str | None = None,
        error: str | None = None,
    ) -> None:
        if status is not None:
            plan["status"] = status
        if error is not None:
            plan["error"] = error
        plan["updated_at"] = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                update plans
                set status = ?, updated_at = ?, plan_json = ?, error = ?
                where id = ?
                """,
                (
                    plan["status"],
                    plan["updated_at"],
                    self._encode_plan(plan),
                    plan.get("error"),
                    plan["plan_id"],
                ),
            )

    def claim_plan(
        self,
        plan_id: str,
        *,
        expected_statuses: set[str],
        claimed_status: str,
    ) -> dict[str, Any]:
        """Atomically claim a plan before executing external side effects."""

        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty.")
        with self._connect() as conn:
            conn.execute("begin immediate")
            row = conn.execute(
                "select status, plan_json from plans where id = ?", (plan_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Plan {plan_id} not found.")
            if row["status"] not in expected_statuses:
                expected = ", ".join(sorted(expected_statuses))
                raise ValueError(
                    f"Plan {plan_id} is {row['status']}; expected one of: {expected}."
                )
            plan = self._decode_plan(row["plan_json"])
            plan["status"] = claimed_status
            plan["updated_at"] = now_iso()
            conn.execute(
                """
                update plans
                set status = ?, updated_at = ?, plan_json = ?, error = null
                where id = ?
                """,
                (
                    claimed_status,
                    plan["updated_at"],
                    self._encode_plan(plan),
                    plan_id,
                ),
            )
        return plan

    def append_event(
        self,
        *,
        action: str,
        status: str,
        plan_id: str | None = None,
        subject_id: str | None = None,
        before: Any = None,
        after: Any = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        access_token = get_access_token()
        event = {
            "id": str(uuid.uuid4()),
            "plan_id": plan_id,
            "action": action,
            "status": status,
            "subject_id": subject_id,
            "before": before,
            "after": after,
            "message": message,
            "actor_client_id": access_token.client_id if access_token else None,
            "actor_subject": access_token.subject if access_token else None,
            "created_at": now_iso(),
        }
        with self._connect() as conn:
            conn.execute(
                """
                insert into audit_events (
                    id, plan_id, action, status, subject_id, before_json,
                    after_json, message, actor_client_id, actor_subject, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    plan_id,
                    action,
                    status,
                    subject_id,
                    json.dumps(before, sort_keys=True) if before is not None else None,
                    json.dumps(after, sort_keys=True) if after is not None else None,
                    message,
                    event["actor_client_id"],
                    event["actor_subject"],
                    event["created_at"],
                ),
            )
        return event

    def list_events(
        self, *, limit: int = 50, plan_id: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "select * from audit_events"
        params: list[Any] = []
        if plan_id:
            sql += " where plan_id = ?"
            params.append(plan_id)
        sql += " order by created_at desc limit ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            events.append(
                {
                    "id": row["id"],
                    "plan_id": row["plan_id"],
                    "action": row["action"],
                    "status": row["status"],
                    "subject_id": row["subject_id"],
                    "before": json.loads(row["before_json"])
                    if row["before_json"]
                    else None,
                    "after": json.loads(row["after_json"])
                    if row["after_json"]
                    else None,
                    "message": row["message"],
                    "actor_client_id": row["actor_client_id"],
                    "actor_subject": row["actor_subject"],
                    "created_at": row["created_at"],
                }
            )
        return events
