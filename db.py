import json
import os
import sqlite3
import secrets
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("INSTANT_DB_PATH", str(Path(__file__).parent / "pd_synthase.db")))

_JSON_FIELDS = {
    "association_words", "candidates_json", "archetypes_json", "preview_artifacts_json",
    "preview_storyboard_json", "deck_proof_plan",
    "slide_specs", "expected_text_map", "quality_results", "reviewed_slides",
    "convert_keep_candidate_ids", "telemetry_context_json",
    "approach_candidates_json", "excepted_inference_elements",
    "inferred_element_decisions", "pitch_aspect_modes",
}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id                    TEXT PRIMARY KEY,
                status                TEXT NOT NULL DEFAULT 'created',
                created_at            TEXT NOT NULL,
                updated_at            TEXT NOT NULL,
                problem_need          TEXT,
                audience              TEXT,
                association_words     TEXT,
                elevator_pitch        TEXT,
                doc_text              TEXT,
                selected_candidate_id TEXT,
                selected_candidate_label TEXT,
                selected_archetype_id TEXT,
                selected_archetype_label TEXT,
                archetypes_json TEXT,
                package_tier          TEXT DEFAULT 'spark',
                human_review          INTEGER DEFAULT 0,
                source_kit            INTEGER DEFAULT 0,
                payment_intent_id     TEXT,
                checkout_kind         TEXT,
                checkout_amount_cents INTEGER,
                checkout_currency     TEXT,
                checkout_client_secret TEXT,
                preview_payment_intent_id TEXT,
                preview_checkout_client_secret TEXT,
                preview_checkout_amount_cents INTEGER,
                preview_payment_status TEXT,
                preview_paid_at       TEXT,
                deck_payment_intent_id TEXT,
                deck_checkout_client_secret TEXT,
                deck_checkout_amount_cents INTEGER,
                deck_payment_status   TEXT,
                deck_paid_at          TEXT,
                recovery_token_hash   TEXT,
                candidates_json       TEXT,
                preview_artifacts_json TEXT,
                preview_storyboard_json TEXT,
                deck_proof_plan       TEXT,
                slide_specs           TEXT,
                expected_text_map     TEXT,
                quality_results       TEXT,
                reviewed_slides       TEXT,
                export_zip_path       TEXT,
                convert_keep_candidate_ids TEXT,
                telemetry_context_json TEXT,
                preview_site_event_emitted_at TEXT,
                deck_site_event_emitted_at TEXT,
                error_message         TEXT
            )
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
        if "problem_need" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN problem_need TEXT")
        if "recovery_token_hash" not in columns:
            conn.execute("ALTER TABLE jobs ADD COLUMN recovery_token_hash TEXT")
        for name, sql_type in {
            "checkout_kind": "TEXT",
            "checkout_amount_cents": "INTEGER",
            "checkout_currency": "TEXT",
            "checkout_client_secret": "TEXT",
            "preview_payment_intent_id": "TEXT",
            "preview_checkout_client_secret": "TEXT",
            "preview_checkout_amount_cents": "INTEGER",
            "preview_payment_status": "TEXT",
            "preview_paid_at": "TEXT",
            "deck_payment_intent_id": "TEXT",
            "deck_checkout_client_secret": "TEXT",
            "deck_checkout_amount_cents": "INTEGER",
            "deck_payment_status": "TEXT",
            "deck_paid_at": "TEXT",
            "preview_artifacts_json": "TEXT",
            "preview_storyboard_json": "TEXT",
            "source_kit": "INTEGER DEFAULT 0",
            "convert_keep_candidate_ids": "TEXT",
            "selected_archetype_id": "TEXT",
            "selected_archetype_label": "TEXT",
            "archetypes_json": "TEXT",
            "explicit_slide_count": "INTEGER",
            "telemetry_context_json": "TEXT",
            "preview_site_event_emitted_at": "TEXT",
            "deck_site_event_emitted_at": "TEXT",
            "approach_candidates_json": "TEXT",
            "conveys": "TEXT",
            "universal_refinement_instruction": "TEXT",
            "excepted_inference_elements": "TEXT",
            "inferred_element_decisions": "TEXT",
            "pitch_aspect_modes": "TEXT",
            "secondary_archetype_id": "TEXT",
            "secondary_archetype_label": "TEXT",
        }.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limits (
                fingerprint  TEXT PRIMARY KEY,
                last_preview TEXT NOT NULL
            )
        """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_recovery_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_job(problem_need: str, audience: str, association_words: list) -> tuple[str, str]:
    job_id = "pd_" + uuid.uuid4().hex[:16]
    recovery_token = secrets.token_urlsafe(9)
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, status, created_at, updated_at, problem_need, audience, association_words, recovery_token_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (job_id, "created", now, now, problem_need, audience, json.dumps(association_words), hash_recovery_token(recovery_token)),
        )
    return job_id, recovery_token


def update_job(job_id: str, **kwargs):
    kwargs["updated_at"] = _now()
    for k, v in list(kwargs.items()):
        if isinstance(v, (dict, list)):
            kwargs[k] = json.dumps(v)
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE jobs SET {fields} WHERE id = ?", values)


def claim_job_status(job_id: str, allowed_statuses: set[str], next_status: str) -> bool:
    placeholders = ", ".join("?" for _ in allowed_statuses)
    values = [next_status, _now(), job_id, *sorted(allowed_statuses)]
    with get_conn() as conn:
        result = conn.execute(
            f"UPDATE jobs SET status = ?, updated_at = ? WHERE id = ? AND status IN ({placeholders})",
            values,
        )
        return result.rowcount == 1


def get_job(job_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for field in _JSON_FIELDS:
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def verify_recovery_token(job: dict, token: str | None) -> bool:
    stored_hash = job.get("recovery_token_hash")
    if not stored_hash:
        # Legacy jobs created before recovery tokens remain recoverable by ID.
        return True
    if not token:
        return False
    return secrets.compare_digest(stored_hash, hash_recovery_token(token.strip()))


def check_rate_limit(fingerprint: str, window_hours: int = 24) -> bool:
    """True if the fingerprint is allowed to start a new preview."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_preview FROM rate_limits WHERE fingerprint = ?", (fingerprint,)
        ).fetchone()
    if not row:
        return True
    last = datetime.fromisoformat(row["last_preview"])
    elapsed = (datetime.now(timezone.utc) - last).total_seconds()
    return elapsed >= window_hours * 3600


def record_rate_limit(fingerprint: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO rate_limits (fingerprint, last_preview) VALUES (?, ?)",
            (fingerprint, _now()),
        )
