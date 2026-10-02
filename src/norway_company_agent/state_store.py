"""Small SQLite persistence layer for resumable company research state.

The store deliberately keeps the source profile and evidence as JSON.  This
preserves the existing profile API while giving callers atomic checkpoints and
an auditable run/operation ledger.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SQLiteStateStore:
    """SQLite state store; safe to use from one process with explicit commits."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, organisation_number TEXT NOT NULL,
                started_at TEXT, completed_at TEXT, status TEXT, metadata_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                organisation_number TEXT PRIMARY KEY, profile_json TEXT NOT NULL,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_key TEXT PRIMARY KEY, organisation_number TEXT NOT NULL,
                field TEXT NOT NULL, evidence_json TEXT NOT NULL,
                FOREIGN KEY(organisation_number) REFERENCES profiles(organisation_number) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, organisation_number TEXT,
                operation TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
                runtime_ms REAL NOT NULL DEFAULT 0, third_party_cost_usd REAL NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_evidence_org ON evidence(organisation_number);
            CREATE INDEX IF NOT EXISTS idx_operations_run ON operations(run_id);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_profile(self, profile: dict[str, Any], *, updated_at: str | None = None) -> None:
        org = str(profile.get("organisation_number") or "")
        if not org:
            raise ValueError("profile requires organisation_number")
        with self.connection:
            self.connection.execute(
                "INSERT INTO profiles VALUES (?, ?, ?) ON CONFLICT(organisation_number) DO UPDATE SET profile_json=excluded.profile_json, updated_at=excluded.updated_at",
                (org, _json(profile), updated_at),
            )
            self.connection.execute("DELETE FROM evidence WHERE organisation_number = ?", (org,))
            for field, record in (profile.get("evidence") or {}).items():
                self.connection.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?)",
                    (f"{org}:{field}", org, field, _json(record)),
                )

    # Common spelling used by checkpoint callers.
    checkpoint_profile = save_profile

    def load_profile(self, organisation_number: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT profile_json FROM profiles WHERE organisation_number = ?", (str(organisation_number),)).fetchone()
        return json.loads(row[0]) if row else None

    def load_evidence(self, organisation_number: str) -> dict[str, Any]:
        rows = self.connection.execute(
            "SELECT field, evidence_json FROM evidence WHERE organisation_number = ? ORDER BY field",
            (str(organisation_number),),
        ).fetchall()
        return {row["field"]: json.loads(row["evidence_json"]) for row in rows}

    get_evidence = load_evidence

    def save_run(self, run_id: str, organisation_number: str, *, status: str | None = None,
                 started_at: str | None = None, completed_at: str | None = None,
                 metadata: dict[str, Any] | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at, status=excluded.status, metadata_json=excluded.metadata_json",
                (run_id, str(organisation_number), started_at, completed_at, status, _json(metadata or {})),
            )

    def record_operation(self, operation: str, *, run_id: str | None = None,
                         organisation_number: str | None = None, requests: int = 0,
                         runtime_ms: float = 0, third_party_cost_usd: float = 0,
                         metadata: dict[str, Any] | None = None) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO operations(run_id, organisation_number, operation, requests, runtime_ms, third_party_cost_usd, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, organisation_number, operation, int(requests), float(runtime_ms), float(third_party_cost_usd), _json(metadata or {})),
            )

    def load_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        return dict(row) | {"metadata": json.loads(row["metadata_json"])}

    def operations(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM operations" + (" WHERE run_id = ?" if run_id else "") + " ORDER BY id"
        rows = self.connection.execute(query, (run_id,) if run_id else ()).fetchall()
        return [dict(row) | {"metadata": json.loads(row["metadata_json"])} for row in rows]


StateStore = SQLiteStateStore
