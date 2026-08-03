"""Durable execution runtime for compiled RiboTome graphs.

The graph describes what may run.  This module is the sole authority for what
did run: revisioned state, accepted stages, evidence, artifacts, and resume.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ribotome_graph import Graph, NodeExecutionError


TERMINAL = {"completed", "cancelled"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


class RuntimeError(Exception):
    pass


class RunNotFound(RuntimeError):
    pass


class RiboTomeRuntime:
    """One authoritative run interface shared by CLI and HTTP adapters."""

    def __init__(self, graph: Graph, root: str | Path):
        self.graph = graph
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_root = self.root / "artifacts"
        self.artifact_root.mkdir(exist_ok=True)
        self.db_path = self.root / "ribotome.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, start_node TEXT NOT NULL, end_node TEXT NOT NULL,
              status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
              values_json TEXT NOT NULL, completed_json TEXT NOT NULL,
              waiting_json TEXT, error_json TEXT, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS executions (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
              attempt INTEGER NOT NULL, status TEXT NOT NULL,
              input_hash TEXT NOT NULL, output_hash TEXT, worker TEXT NOT NULL,
              started_at TEXT NOT NULL, finished_at TEXT, error_json TEXT,
              FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, node_id TEXT NOT NULL,
              execution_id TEXT NOT NULL, name TEXT NOT NULL, media_type TEXT NOT NULL,
              sha256 TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
              revision INTEGER NOT NULL, kind TEXT NOT NULL, node_id TEXT,
              payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES runs(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS execution_attempt
              ON executions(run_id, node_id, attempt);
            """)

    def create(self, start: str, end: str, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        plan = self.graph.plan(start, end)
        supplied = dict(inputs or {})
        declared = {name for node in self.graph.nodes.values() for name in node.inputs}
        unknown = set(supplied) - declared
        if unknown:
            raise NodeExecutionError(f"undeclared external inputs: {sorted(unknown)}")
        ports = {**plan.optional_inputs, **plan.required_inputs}
        for name, value in supplied.items():
            if name in ports:
                ports[name].validate(value, label=f"external input {name!r}")
        run_id, now = uuid.uuid4().hex, _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, start, end, "ready", 0, _json(supplied), "[]", None, None, now, now),
            )
            self._event(db, run_id, 0, "run.created", None, {"plan": plan.as_dict(), "supplied": sorted(supplied)})
        return self.get(run_id)

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM runs ORDER BY created_at DESC")]
        return [self.get(run_id) for run_id in ids]

    def get(self, run_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFound(f"unknown run {run_id!r}")
            executions = [dict(r) for r in db.execute(
                "SELECT id,node_id,attempt,status,input_hash,output_hash,worker,started_at,finished_at,error_json FROM executions WHERE run_id=? ORDER BY started_at,id",
                (run_id,),
            )]
        for item in executions:
            item["error"] = json.loads(item.pop("error_json")) if item["error_json"] else None
        plan = self.graph.plan(row["start_node"], row["end_node"])
        values = json.loads(row["values_json"])
        return {
            "id": row["id"], "status": row["status"], "revision": row["revision"],
            "from": row["start_node"], "to": row["end_node"],
            "completed": json.loads(row["completed_json"]),
            "waiting": json.loads(row["waiting_json"]) if row["waiting_json"] else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "outputs": {name: values.get(name) for name in plan.outputs if name in values},
            "executions": executions, "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def events(self, run_id: str) -> list[dict[str, Any]]:
        self.get(run_id)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        self.get(run_id)
        with self._connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM artifacts WHERE run_id=? ORDER BY created_at,id", (run_id,))]

    async def advance_async(self, run_id: str, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._merge_inputs(run_id, dict(inputs or {}))
        while True:
            row = self._row(run_id)
            if row["status"] in TERMINAL:
                return self.get(run_id)
            if row["status"] == "running":
                raise RuntimeError(f"run {run_id} is already being advanced")
            plan = self.graph.plan(row["start_node"], row["end_node"])
            values = json.loads(row["values_json"])
            completed = set(json.loads(row["completed_json"]))
            node_id = next((n for n in plan.node_ids if n not in completed and all(d not in plan.node_ids or d in completed for d in self.graph.nodes[n].depends)), None)
            if node_id is None:
                self._set_status(run_id, "completed", "run.completed", None)
                return self.get(run_id)
            node = self.graph.nodes[node_id]
            missing = [name for name, port in node.inputs.items() if port.required and name not in values]
            if missing or node.human:
                kind = "human_decision" if node.human or any(name in node.decision_inputs for name in missing) else "external_input"
                self._suspend(run_id, node_id, kind, missing)
                return self.get(run_id)
            node_input = {name: values[name] for name in node.inputs if name in values}
            for name, port in node.inputs.items():
                port.validate(node_input.get(name), label=f"node {node_id!r} input {name!r}")
            attempt = self._next_attempt(run_id, node_id)
            execution_id = uuid.uuid4().hex
            worker = self._worker_name(node.run)
            started = _now()
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                claimed = db.execute("UPDATE runs SET status='running',waiting_json=NULL,error_json=NULL,updated_at=? WHERE id=? AND status!='running'", (started, run_id))
                if claimed.rowcount != 1:
                    raise RuntimeError(f"run {run_id} is already being advanced")
                db.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (execution_id, run_id, node_id, attempt, "running", _hash(node_input), None, worker, started, None, None))
            try:
                result = node.run(node_input)  # type: ignore[misc]
                if inspect.isawaitable(result):
                    result = await result
                output = self._validate_output(node_id, result)
                self._accept(run_id, node_id, execution_id, output)
            except Exception as exc:
                self._fail(run_id, node_id, execution_id, exc)
                return self.get(run_id)

    def advance(self, run_id: str, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return asyncio.run(self.advance_async(run_id, inputs))

    def cancel(self, run_id: str) -> dict[str, Any]:
        self._set_status(run_id, "cancelled", "run.cancelled", None)
        return self.get(run_id)

    def complete_human(self, run_id: str, outputs: Mapping[str, Any]) -> dict[str, Any]:
        """Accept a suspended human worker's typed outputs and continue."""
        row = self._row(run_id)
        waiting = json.loads(row["waiting_json"]) if row["waiting_json"] else None
        if row["status"] != "suspended" or not waiting:
            raise RuntimeError(f"run {run_id} is not waiting for a human stage")
        node_id = waiting["node"]
        node = self.graph.nodes[node_id]
        if not node.human:
            raise RuntimeError(f"stage {node_id!r} is waiting for inputs, not human output")
        output = self._validate_output(node_id, outputs)
        execution_id, now = uuid.uuid4().hex, _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO executions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (execution_id, run_id, node_id, self._next_attempt(run_id, node_id), "running", _hash({}), None, "human", now, None, None))
        self._accept(run_id, node_id, execution_id, output)
        return self.advance(run_id)

    def _row(self, run_id: str) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(f"unknown run {run_id!r}")
        return row

    def _merge_inputs(self, run_id: str, supplied: dict[str, Any]) -> None:
        if not supplied:
            return
        row = self._row(run_id)
        if row["status"] in TERMINAL:
            raise RuntimeError(f"run {run_id} is {row['status']}")
        plan = self.graph.plan(row["start_node"], row["end_node"])
        declared = {**plan.optional_inputs, **plan.required_inputs}
        unknown = set(supplied) - set(declared)
        if unknown:
            raise NodeExecutionError(f"inputs are not external to this run: {sorted(unknown)}")
        values = json.loads(row["values_json"])
        completed = set(json.loads(row["completed_json"]))
        for name, value in supplied.items():
            declared[name].validate(value, label=f"external input {name!r}")
            consumer_done = any(name in self.graph.nodes[n].inputs for n in completed)
            if consumer_done and name in values and values[name] != value:
                raise RuntimeError(f"cannot change consumed input {name!r}")
            values[name] = value
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()[0]
            revision = current + 1
            db.execute("UPDATE runs SET values_json=?,revision=?,status='ready',waiting_json=NULL,error_json=NULL,updated_at=? WHERE id=?", (_json(values), revision, _now(), run_id))
            self._event(db, run_id, revision, "inputs.supplied", None, {"names": sorted(supplied)})

    def _validate_output(self, node_id: str, result: Any) -> dict[str, Any]:
        node = self.graph.nodes[node_id]
        if not isinstance(result, Mapping):
            raise NodeExecutionError(f"node {node_id!r} returned {type(result).__name__}, expected mapping")
        if set(result) != set(node.outputs):
            raise NodeExecutionError(f"node {node_id!r} output mismatch; missing={sorted(set(node.outputs)-set(result))}, undeclared={sorted(set(result)-set(node.outputs))}")
        output = dict(result)
        for name, port in node.outputs.items():
            port.validate(output.get(name), label=f"node {node_id!r} output {name!r}")
        return output

    def _accept(self, run_id: str, node_id: str, execution_id: str, output: dict[str, Any]) -> None:
        run_dir = self.artifact_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = run_dir / f"{node_id}.json"
        artifact_path.write_text(json.dumps(output, indent=2, default=str) + "\n", encoding="utf-8")
        sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            values, completed = json.loads(row["values_json"]), json.loads(row["completed_json"])
            collision = set(output) & set(values)
            if collision:
                raise NodeExecutionError(f"node {node_id!r} attempted to overwrite values {sorted(collision)}")
            values.update(output); completed.append(node_id)
            revision, now = row["revision"] + 1, _now()
            db.execute("UPDATE runs SET values_json=?,completed_json=?,revision=?,status='ready',waiting_json=NULL,error_json=NULL,updated_at=? WHERE id=?", (_json(values), _json(completed), revision, now, run_id))
            db.execute("UPDATE executions SET status='accepted',output_hash=?,finished_at=? WHERE id=?", (_hash(output), now, execution_id))
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, run_id, node_id, execution_id, "outputs", "application/json", sha, str(artifact_path), now))
            self._event(db, run_id, revision, "stage.accepted", node_id, {"execution_id": execution_id, "outputs": sorted(output), "artifact_sha256": sha})

    def _suspend(self, run_id: str, node_id: str, kind: str, missing: list[str]) -> None:
        waiting = {"node": node_id, "kind": kind, "required_inputs": missing}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            revision = row[0] + 1
            db.execute("UPDATE runs SET status='suspended',revision=?,waiting_json=?,updated_at=? WHERE id=?", (revision, _json(waiting), _now(), run_id))
            self._event(db, run_id, revision, "run.suspended", node_id, waiting)

    def _fail(self, run_id: str, node_id: str, execution_id: str, exc: Exception) -> None:
        classification = "contract" if isinstance(exc, (ValueError, NodeExecutionError)) else "worker"
        error = {"node": node_id, "classification": classification, "type": type(exc).__name__, "message": str(exc)}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            revision, now = row[0] + 1, _now()
            db.execute("UPDATE executions SET status='failed',finished_at=?,error_json=? WHERE id=?", (now, _json(error), execution_id))
            db.execute("UPDATE runs SET status='failed',revision=?,error_json=?,updated_at=? WHERE id=?", (revision, _json(error), now, run_id))
            self._event(db, run_id, revision, "stage.failed", node_id, error)

    def _set_status(self, run_id: str, status: str, kind: str, node_id: str | None) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise RunNotFound(f"unknown run {run_id!r}")
            revision = row[0] + 1
            db.execute("UPDATE runs SET status=?,revision=?,waiting_json=NULL,updated_at=? WHERE id=?", (status, revision, _now(), run_id))
            self._event(db, run_id, revision, kind, node_id, {})

    def _next_attempt(self, run_id: str, node_id: str) -> int:
        with self._connect() as db:
            return db.execute("SELECT COALESCE(MAX(attempt),0)+1 FROM executions WHERE run_id=? AND node_id=?", (run_id, node_id)).fetchone()[0]

    @staticmethod
    def _worker_name(worker: Any) -> str:
        return f"{getattr(worker, '__module__', 'unknown')}:{getattr(worker, '__qualname__', repr(worker))}"

    @staticmethod
    def _event(db: sqlite3.Connection, run_id: str, revision: int, kind: str, node_id: str | None, payload: Any) -> None:
        db.execute("INSERT INTO events(run_id,revision,kind,node_id,payload_json,created_at) VALUES (?,?,?,?,?,?)", (run_id, revision, kind, node_id, _json(payload), _now()))
