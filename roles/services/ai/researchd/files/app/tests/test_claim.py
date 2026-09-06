"""The ready -> publishing claim/unclaim contract (main.py's /claim,
/unclaim; db.py's claim_job, unclaim_job).

This is the fix for n8n's dead workflow-static-data guard: researchd now
owns the lock, as a single conditional UPDATE, so a job can never be
ingested twice concurrently and never wedges forever if n8n's SSH ingest
dies mid-flight. See db.claim_job's docstring for the full story.

Most of this is exercised at the `db` layer directly -- fast, and it is
where the actual atomicity/stale-recovery logic lives. The HTTP-layer
tests (claim/unclaim/bundle status codes) go through a real FastAPI app,
mirroring test_engines.py's test_list_engines_endpoint_... pattern; see
the `app_main` fixture below for why that pattern needs its own caveat
about researchd.main being importable only once per test session.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from researchd import db

# --------------------------------------------------------------------------
# db.claim_job / db.unclaim_job -- the atomic UPDATE itself
# --------------------------------------------------------------------------


def test_claim_succeeds_once_and_fails_the_second_time(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.create_job(
        conn, job_id="j1", topic="t", source_url=None, depth=1,
        slug="T", status="ready",
    )

    assert db.claim_job(conn, "j1", stale_minutes=60) is True
    assert db.get_job(conn, "j1")["status"] == "publishing"

    # Second claim: the job is no longer `ready`, and the first claim is
    # brand new (not stale), so the WHERE clause matches nothing.
    assert db.claim_job(conn, "j1", stale_minutes=60) is False


def test_unclaim_returns_a_publishing_job_to_ready(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.create_job(
        conn, job_id="j1", topic="t", source_url=None, depth=1,
        slug="T", status="ready",
    )
    db.claim_job(conn, "j1", stale_minutes=60)

    assert db.unclaim_job(conn, "j1") is True
    assert db.get_job(conn, "j1")["status"] == "ready"

    # And it is claimable again immediately -- this is what makes a failed
    # ingest retry on the next poll instead of being stuck.
    assert db.claim_job(conn, "j1", stale_minutes=60) is True


def test_unclaim_is_a_no_op_on_a_job_that_is_not_publishing(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.create_job(
        conn, job_id="j1", topic="t", source_url=None, depth=1,
        slug="T", status="ready",
    )

    assert db.unclaim_job(conn, "j1") is False
    assert db.get_job(conn, "j1")["status"] == "ready"


def test_a_stale_publishing_claim_is_reclaimable(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.create_job(
        conn, job_id="j1", topic="t", source_url=None, depth=1,
        slug="T", status="ready",
    )
    db.claim_job(conn, "j1", stale_minutes=60)

    # Back-date the claim as if n8n's SSH ingest died two hours ago --
    # long past a 60-minute staleness window.
    stale_claimed_at = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 2 * 3600),
    ) + "Z"
    conn.execute("UPDATE jobs SET claimed_at=? WHERE id='j1'", (stale_claimed_at,))
    conn.commit()

    assert db.claim_job(conn, "j1", stale_minutes=60) is True
    assert db.get_job(conn, "j1")["status"] == "publishing"


def test_a_fresh_publishing_claim_is_not_reclaimable(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.create_job(
        conn, job_id="j1", topic="t", source_url=None, depth=1,
        slug="T", status="ready",
    )
    db.claim_job(conn, "j1", stale_minutes=60)

    # claimed_at is "now" -- nowhere near stale_minutes=60 in the past.
    assert db.claim_job(conn, "j1", stale_minutes=60) is False
    assert db.get_job(conn, "j1")["status"] == "publishing"


# --------------------------------------------------------------------------
# HTTP layer -- /claim, /unclaim, and get_bundle's status gate
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app_main(tmp_path_factory):
    """`researchd.main` builds its `Pipeline` (and therefore opens its
    sqlite db) at IMPORT TIME, reading whatever `researchd.config.settings`
    is bound to at that moment -- see test_engines.py's
    test_list_engines_endpoint_... for the same constraint. This fixture can
    only steer that construction the FIRST time anything in the test
    session imports `researchd.main`; if some other test module already
    has, this reuses whatever Pipeline/db that left behind instead.

    Every test below only relies on `app_main.pipeline` being a real,
    working Pipeline against SOME on-disk sqlite db -- never on which
    settings happened to win that race -- which is why they read
    `app_main.pipeline.conn` / `.settings.tree_dir` back out rather than
    assuming a particular `tmp_path`.
    """
    if "researchd.main" not in sys.modules:
        import os

        os.environ.setdefault(
            "RESEARCHD_STATIC_DIR", str(Path(__file__).resolve().parent.parent / "static"),
        )
        import researchd.config as config_mod

        data_dir = tmp_path_factory.mktemp("researchd-claim-http")
        config_mod.settings = config_mod.Settings(
            data_dir=str(data_dir), gptr_url="http://127.0.0.1:1", default_engine="native",
        )

    import researchd.main as main_mod

    return main_mod


@pytest.fixture(scope="module")
def client(app_main):
    from fastapi.testclient import TestClient

    with TestClient(app_main.app) as c:
        yield c


def _make_ready_job(app_main, job_id: str, slug: str) -> None:
    db.create_job(
        app_main.pipeline.conn, job_id=job_id, topic="t", source_url=None,
        depth=1, slug=slug, status="ready",
    )


def test_claim_endpoint_returns_200_then_409(app_main, client):
    _make_ready_job(app_main, "http-claim-1", "Http-Claim-1")

    first = client.post("/api/jobs/http-claim-1/claim")
    assert first.status_code == 200
    assert first.json()["status"] == "publishing"

    second = client.post("/api/jobs/http-claim-1/claim")
    assert second.status_code == 409


def test_unclaim_endpoint_returns_it_to_ready(app_main, client):
    _make_ready_job(app_main, "http-claim-2", "Http-Claim-2")
    client.post("/api/jobs/http-claim-2/claim")

    resp = client.post("/api/jobs/http-claim-2/unclaim")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert db.get_job(app_main.pipeline.conn, "http-claim-2")["status"] == "ready"

    # Retryable: a second claim now succeeds.
    assert client.post("/api/jobs/http-claim-2/claim").status_code == 200


def test_bundle_still_downloads_while_publishing(app_main, client):
    job_id, slug = "http-claim-3", "Http-Claim-3"
    _make_ready_job(app_main, job_id, slug)
    client.post(f"/api/jobs/{job_id}/claim")
    assert db.get_job(app_main.pipeline.conn, job_id)["status"] == "publishing"

    tree_path = Path(app_main.pipeline.settings.tree_dir) / slug
    tree_path.mkdir(parents=True, exist_ok=True)
    (tree_path / f"{slug}.md").write_text("# stub note\n")

    resp = client.get(f"/api/jobs/{job_id}/bundle.tar.gz")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/gzip"
