import json
import os
import sqlite3
import secrets
import hashlib
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("INSTANT_DB_PATH", str(Path(__file__).parent / "local_state" / "workshop.db")))

_JSON_FIELDS = {
    "association_words", "candidates_json", "archetypes_json", "preview_artifacts_json",
    "preview_storyboard_json", "deck_proof_plan",
    "slide_specs", "expected_text_map", "quality_results", "reviewed_slides",
    "convert_keep_candidate_ids", "telemetry_context_json",
    "approach_candidates_json", "excepted_inference_elements",
    "inferred_element_decisions", "pitch_aspect_modes",
    "stage_states", "stage_history", "intake_options", "image_analysis", "identity",
    "prototype_candidates_json", "progress_log",
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
            "stage_states": "TEXT",
            "stage_history": "TEXT",
            "intake_options": "TEXT",
            "image_analysis": "TEXT",
            "identity": "TEXT",
            "name": "TEXT",
            "infer_prototype": "INTEGER DEFAULT 0",
            "prototype_candidates_json": "TEXT",
            "selected_prototype_id": "TEXT",
            "progress_log": "TEXT",
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


def filter_jobs(
    status: str | None = None,
    archetype: str | None = None,
    label: str | None = None,
    audience: str | None = None,
    vibe: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return jobs matching any combination of classifier parameters, newest first.

    status   — exact match on status column
    archetype — matches selected_archetype_id or any effective_archetype_id in approach_candidates_json
    label     — matches selected_candidate_label or any approach label
    audience  — substring match on audience column
    vibe      — substring match inside approach_candidates_json vibe_semantics
    """
    clauses, params = [], []
    if status:
        clauses.append("status = ?"); params.append(status)
    if audience:
        clauses.append("audience LIKE ?"); params.append(f"%{audience}%")
    if archetype:
        clauses.append("(selected_archetype_id = ? OR approach_candidates_json LIKE ?)")
        params.extend([archetype, f'%"{archetype}"%'])
    if label:
        clauses.append("(selected_candidate_label = ? OR approach_candidates_json LIKE ?)")
        params.extend([label, f'%"{label}"%'])
    if vibe:
        clauses.append("approach_candidates_json LIKE ?"); params.append(f"%{vibe}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (
        "SELECT id, status, created_at, updated_at, audience, "
        "selected_candidate_label, selected_archetype_label, approach_candidates_json "
        f"FROM jobs {where} ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)

    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        raw = d.pop("approach_candidates_json") or "[]"
        try:
            candidates = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            candidates = []
        # exact-match post-filter for label/archetype (LIKE can over-select)
        if label and label != d.get("selected_candidate_label"):
            if not any(a.get("label") == label for a in candidates):
                continue
        if archetype and archetype != d.get("selected_archetype_id"):
            if not any(
                a.get("effective_archetype_id") == archetype or
                (a.get("archetype_a") or {}).get("archetype_id") == archetype or
                (a.get("archetype_b") or {}).get("archetype_id") == archetype
                for a in candidates
            ):
                continue
        d["approach_labels"] = [a.get("label", "") for a in candidates if a.get("label")]
        results.append(d)
    return results


def get_job_by_label(label: str) -> dict | None:
    """Find the most recent job where selected_candidate_label matches, then fall back to approach_candidates_json scan."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE selected_candidate_label = ? ORDER BY created_at DESC LIMIT 1", (label,)
        ).fetchone()
        if not row:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE approach_candidates_json LIKE ? ORDER BY created_at DESC LIMIT 50",
                (f'%"{label}"%',)
            ).fetchall()
            for r in rows:
                try:
                    candidates = json.loads(r["approach_candidates_json"] or "[]")
                    if any(a.get("label") == label for a in candidates):
                        row = r
                        break
                except (json.JSONDecodeError, TypeError):
                    continue
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


# ── PostHog telemetry ─────────────────────────────────────────────────────────
_PH_TOKEN = os.environ.get("POSTHOG_TOKEN", "phc_DaZmzZ9fKszDeecfqdETWQwMq5uSskHG4UutVvj92GUQ")
_PH_HOST  = "https://us.i.posthog.com"


def _ph_capture(distinct_id: str, event: str, props: dict | None = None) -> None:
    """Fire-and-forget PostHog capture. Swallows all errors — pipeline is unaffected."""
    import threading, urllib.request
    def _send():
        try:
            payload = json.dumps({
                "api_key": _PH_TOKEN,
                "event": event,
                "distinct_id": distinct_id,
                "properties": {**(props or {}), "$lib": "ps-pipeline"},
                "timestamp": _now(),
            }).encode()
            req = urllib.request.Request(
                f"{_PH_HOST}/capture/",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()


# ── Stage state machine ───────────────────────────────────────────────────────

def get_stage_states(job_id: str) -> dict:
    """Return the stage_states dict for a job (empty dict if none yet)."""
    job = get_job(job_id)
    if not job:
        return {}
    v = job.get("stage_states")
    if isinstance(v, dict):
        return v
    return {}


def set_stage_state(job_id: str, stage_id: str, state: str, error: str | None = None) -> None:
    """Atomically update stage_states + append to stage_history + fire PostHog event.

    Single SQL UPDATE — no Python read-modify-write, no window for concurrent
    threads to overwrite each other's transitions.
    """
    entry: dict = {"stage": stage_id, "state": state, "ts": time.time()}
    if error:
        entry["error"] = error[:500]

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET stage_states = JSON_SET(COALESCE(stage_states, '{}'), '$.' || ?, ?),
                stage_history = JSON_INSERT(COALESCE(stage_history, '[]'), '$[#]', JSON(?)),
                updated_at = ?
            WHERE id = ?
            """,
            (stage_id, state, json.dumps(entry), _now(), job_id),
        )

    _ph_capture(job_id, f"stage_{state}", {
        "stage": stage_id,
        "job_id": job_id,
        **({"error": error[:200]} if error else {}),
    })


def append_progress(job_id: str, stage: str, message: str, pct: float | None = None) -> None:
    """Write a named checkpoint to progress_log — read by the SSE stream endpoint."""
    entry: dict = {"ts": time.time(), "stage": stage, "message": message}
    if pct is not None:
        entry["pct"] = round(pct, 2)
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET progress_log = JSON_INSERT(COALESCE(progress_log, '[]'), '$[#]', JSON(?)),
                updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(entry), _now(), job_id),
        )


def get_jobs_needing_resume() -> list[dict]:
    """Return jobs that have stage_states but are not in a terminal status."""
    terminal = {"complete", "error", "expired"}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, stage_states FROM jobs "
            "WHERE stage_states IS NOT NULL AND stage_states != '{}' AND stage_states != '' "
            "ORDER BY created_at DESC"
        ).fetchall()
    result = []
    for row in rows:
        if row["status"] not in terminal:
            result.append({"id": row["id"], "status": row["status"]})
    return result


def set_identity(job_id: str, identity_patch: dict) -> None:
    """Merge identity_patch into the job's identity dict.

    Existing keys are preserved; patch keys overwrite. PostHog $identify is
    called when email is present, aliasing job_id (the anonymous distinct_id)
    to the email for session merging.
    """
    job = get_job(job_id)
    if not job:
        return
    existing = job.get("identity") or {}
    if not isinstance(existing, dict):
        existing = {}
    merged = {**existing, **identity_patch}
    update_job(job_id, identity=json.dumps(merged))

    email = merged.get("email")
    if email:
        _ph_capture(email, "$identify", {"$anon_distinct_id": job_id})
    _ph_capture(job_id, "identity_updated", {
        "has_email": bool(email),
        "has_stripe_customer": bool(merged.get("stripe_customer_id")),
    })
