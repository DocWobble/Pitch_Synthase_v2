#!/usr/bin/env python3
"""Re-run finalization on the guiclid job against its current (post color-fix)
slide proofs -- the export on disk predates that fix and is stale."""
from __future__ import annotations
import asyncio, os, sys
from pathlib import Path

_HERE = Path(__file__).parent
_INTELLURIC_ENV = Path("/home/director/intelluric/local/intelluric/intelluric-site.env")
for _line in _INTELLURIC_ENV.read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

os.environ["INSTANT_DB_PATH"] = str(_HERE / "local_state" / "workshop.db")
os.environ["INSTANT_JOBS_DIR"] = str(_HERE / "local_state" / "jobs")
sys.path.insert(0, str(_HERE))
import db, workers

db.init_db()
JOB_ID = "pd_5e121a3bd64f4a61"

async def main():
    db.update_job(JOB_ID, status="finalization_queued", error_message=None)
    await workers.finalization_worker(JOB_ID)
    job = db.get_job(JOB_ID)
    print("final status:", job.get("status"), file=sys.stderr)
    if job.get("error_message"):
        print("error:", job["error_message"], file=sys.stderr)
    print("export_zip_path:", job.get("export_zip_path"), file=sys.stderr)

asyncio.run(main())
