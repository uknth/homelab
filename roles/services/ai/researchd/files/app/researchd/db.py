"""SQLite schema and accessors. WAL mode, stdlib `sqlite3` only.

The pipeline runs with concurrency 1 (one resident model on ai01 -- see
config.py / pipeline.py), so write volume here is tiny: one job's worth of
stage/source/excerpt rows at a time. A single shared connection guarded by
a lock is enough; there is no case where a connection pool would earn its
complexity.

Four tables: jobs, stages, sources, excerpts -- exactly the objects the
API exposes (`/api/jobs`, `/api/jobs/{id}`) and the pipeline needs to
resume sensibly after a restart (see pipeline.py's startup recovery).
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    topic        TEXT NOT NULL,
    source_url   TEXT,
    depth        INTEGER NOT NULL,
    slug         TEXT NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT,
    wiki_url     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_slug ON jobs(slug);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);

CREATE TABLE IF NOT EXISTS stages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL,   -- active | done | failed
    detail       TEXT,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_stages_job ON stages(job_id);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    subtopic     TEXT NOT NULL,
    url          TEXT NOT NULL,
    doi          TEXT,
    title        TEXT,
    status       TEXT NOT NULL,   -- pending | fetched | summarised | failed
    error        TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_job ON sources(job_id);

CREATE TABLE IF NOT EXISTS excerpts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    start_offset  INTEGER NOT NULL,
    end_offset    INTEGER NOT NULL,
    text          TEXT NOT NULL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_excerpts_source ON excerpts(source_id);
"""

_lock = threading.Lock()


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def _write(conn: sqlite3.Connection):
    with _lock:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


def create_job(
    conn: sqlite3.Connection, *, job_id: str, topic: str, source_url: str | None,
    depth: int, slug: str, status: str = "queued",
) -> None:
    now = _now()
    with _write(conn) as cur:
        cur.execute(
            "INSERT INTO jobs (id, topic, source_url, depth, slug, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, topic, source_url, depth, slug, status, now, now),
        )


def update_job_status(
    conn: sqlite3.Connection, job_id: str, status: str, *,
    error: str | None = None, wiki_url: str | None = None,
) -> None:
    with _write(conn) as cur:
        cur.execute(
            "UPDATE jobs SET status=?, error=COALESCE(?, error), "
            "wiki_url=COALESCE(?, wiki_url), updated_at=? WHERE id=?",
            (status, error, wiki_url, _now(), job_id),
        )


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def update_job_topic(conn: sqlite3.Connection, job_id: str, topic: str, slug: str) -> None:
    """Used once, right after the resolve step, when a job was submitted
    with only a seed URL: the real topic (and therefore slug) is not known
    until that URL has been fetched."""
    with _write(conn) as cur:
        cur.execute(
            "UPDATE jobs SET topic=?, slug=?, updated_at=? WHERE id=?",
            (topic, slug, _now(), job_id),
        )


def update_job_slug(conn: sqlite3.Connection, job_id: str, slug: str) -> None:
    """Set the slug alone, leaving `topic` untouched -- called once, after
    planning, when the model's short title replaces the raw question as the
    basis for the directory name. The UI still shows the full topic."""
    with _write(conn) as cur:
        cur.execute(
            "UPDATE jobs SET slug=?, updated_at=? WHERE id=?",
            (slug, _now(), job_id),
        )


def get_latest_job_for_slug(conn: sqlite3.Connection, slug: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM jobs WHERE slug=? ORDER BY created_at DESC LIMIT 1", (slug,)
    ).fetchone()
    return dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_active_jobs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status NOT IN "
        "('ready','published','failed','cancelled') ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_job_children(conn: sqlite3.Connection, job_id: str) -> None:
    """Wipe a job's stage/source/excerpt rows -- used when a topic is
    re-run under the same slug, so history does not accumulate rows for
    directories that no longer exist on disk (see write.py, which
    replaces the tree in place rather than creating `-2` duplicates).
    """
    with _write(conn) as cur:
        cur.execute(
            "DELETE FROM excerpts WHERE source_id IN "
            "(SELECT id FROM sources WHERE job_id=?)", (job_id,),
        )
        cur.execute("DELETE FROM sources WHERE job_id=?", (job_id,))
        cur.execute("DELETE FROM stages WHERE job_id=?", (job_id,))


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


def start_stage(conn: sqlite3.Connection, job_id: str, stage: str) -> int:
    with _write(conn) as cur:
        cur.execute(
            "INSERT INTO stages (job_id, stage, status, started_at) "
            "VALUES (?,?, 'active', ?)",
            (job_id, stage, _now()),
        )
        return cur.lastrowid


def finish_stage(conn: sqlite3.Connection, stage_id: int, status: str, detail: str | None = None) -> None:
    with _write(conn) as cur:
        cur.execute(
            "UPDATE stages SET status=?, detail=?, finished_at=? WHERE id=?",
            (status, detail, _now(), stage_id),
        )


def list_stages(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM stages WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# sources / excerpts
# --------------------------------------------------------------------------


def add_source(
    conn: sqlite3.Connection, job_id: str, subtopic: str, url: str, *,
    doi: str | None = None, title: str | None = None, status: str = "pending",
) -> int:
    with _write(conn) as cur:
        cur.execute(
            "INSERT INTO sources (job_id, subtopic, url, doi, title, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (job_id, subtopic, url, doi, title, status, _now()),
        )
        return cur.lastrowid


def update_source_status(
    conn: sqlite3.Connection, source_id: int, status: str, *,
    error: str | None = None, title: str | None = None,
) -> None:
    with _write(conn) as cur:
        cur.execute(
            "UPDATE sources SET status=?, error=COALESCE(?, error), "
            "title=COALESCE(?, title) WHERE id=?",
            (status, error, title, source_id),
        )


def list_sources(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sources WHERE job_id=? ORDER BY id", (job_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def add_excerpt(
    conn: sqlite3.Connection, source_id: int, start_offset: int, end_offset: int,
    text: str, note: str = "",
) -> None:
    with _write(conn) as cur:
        cur.execute(
            "INSERT INTO excerpts (source_id, start_offset, end_offset, text, note) "
            "VALUES (?,?,?,?,?)",
            (source_id, start_offset, end_offset, text, note),
        )


def list_excerpts_for_source(conn: sqlite3.Connection, source_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM excerpts WHERE source_id=? ORDER BY id", (source_id,)
    ).fetchall()
    return [dict(r) for r in rows]
