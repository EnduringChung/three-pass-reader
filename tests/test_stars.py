"""Favourite ("star") marker on papers.

The star is decoration for the user but state for the DB: it must survive restarts, be exposed by the
library listing, and toggle without a full reload.
"""
import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    import db

    dbp = tmp_path / "reader.db"
    monkeypatch.setattr(appmod, "DB_PATH", str(dbp))
    monkeypatch.setattr(appmod, "DRAFT_IMAGES", tmp_path / "drafts")
    monkeypatch.setattr(appmod, "DATA", tmp_path)
    db.init(str(dbp))
    db._tls.__dict__.pop("conn", None)      # thread-local conns survive between tests; drop it
    conn = db.get_conn()
    wid = db.upsert_work(conn, "paper-one", "A paper about organoids")
    return TestClient(appmod.app), conn, wid


def test_star_is_off_by_default(client):
    http, conn, wid = client
    rows = http.get("/api/papers").json()
    row = [r for r in rows if r["id"] == wid][0]
    assert row["starred"] == 0


def test_star_can_be_set_and_cleared(client):
    http, conn, wid = client
    r = http.patch(f"/api/works/{wid}/star", json={"starred": True})
    assert r.status_code == 200 and r.json()["starred"] == 1
    assert [x for x in http.get("/api/papers").json() if x["id"] == wid][0]["starred"] == 1

    r = http.patch(f"/api/works/{wid}/star", json={"starred": False})
    assert r.json()["starred"] == 0
    assert [x for x in http.get("/api/papers").json() if x["id"] == wid][0]["starred"] == 0


def test_star_without_a_value_toggles(client):
    http, conn, wid = client
    first = http.patch(f"/api/works/{wid}/star", json={}).json()["starred"]
    second = http.patch(f"/api/works/{wid}/star", json={}).json()["starred"]
    assert (first, second) == (1, 0)


def test_starring_an_unknown_work_is_404(client):
    http, conn, wid = client
    assert http.patch("/api/works/999999/star", json={"starred": True}).status_code == 404


def test_star_survives_a_reconnect(tmp_path):
    """It is a column, not UI state: reopening the DB must still show the favourite."""
    import db
    dbp = tmp_path / "reader.db"
    db.init(str(dbp))
    db._tls.__dict__.pop("conn", None)
    conn = db.get_conn()
    wid = db.upsert_work(conn, "keep-me", "Starred paper")
    db.set_star(conn, wid, True)
    db._tls.__dict__.pop("conn", None)          # drop the thread-local connection
    db.init(str(dbp))
    db._tls.__dict__.pop("conn", None)
    row = db.get_conn().execute("SELECT starred FROM works WHERE id=?", (wid,)).fetchone()
    assert row["starred"] == 1


def test_existing_databases_gain_the_column(tmp_path):
    """Migration path: an old DB without `starred` must not explode on first connect."""
    import sqlite3
    import db
    dbp = tmp_path / "old.db"
    raw = sqlite3.connect(dbp)
    raw.execute("CREATE TABLE works (id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT, "
                "active_file_id INT, verdict TEXT, pass1_done INT DEFAULT 0, "
                "pass2_done INT DEFAULT 0, pass3_done INT DEFAULT 0)")
    raw.execute("INSERT INTO works(slug, title) VALUES ('legacy','Legacy paper')")
    raw.commit()
    raw.close()

    db.init(str(dbp))
    db._tls.__dict__.pop("conn", None)
    conn = db.get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(works)")]
    assert "starred" in cols
    row = conn.execute("SELECT id, starred FROM works WHERE slug='legacy'").fetchone()
    assert row["starred"] in (0, None)
