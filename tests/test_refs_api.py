"""API tests for the references endpoints (temp DB; no network)."""
import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as appmod
import db as dbm
import refs

PROJECT = pathlib.Path(__file__).resolve().parents[1]


def real_pdf(wid=1):
    dbfile = PROJECT / "data" / "reader.db"
    if not dbfile.exists():
        pytest.skip("live DB not available")
    conn = sqlite3.connect(str(dbfile))
    try:
        row = conn.execute("SELECT path FROM files WHERE work_id=?", (wid,)).fetchone()
    finally:
        conn.close()
    if not row or not pathlib.Path(row[0]).exists():
        pytest.skip(f"work {wid} pdf missing")
    return row[0]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setattr(dbm, "_db_path", db_path, raising=False)
    dbm._tls.__dict__.pop("conn", None)
    conn = dbm.connect(db_path)
    work = dbm.upsert_work(conn, "refs-api", "Refs API paper")
    fpath = real_pdf(1)
    fid = dbm.add_file(conn, work, fpath, "abc")
    dbm.set_text_layer(conn, fid, "text")
    conn.close()
    with TestClient(appmod.app) as c:
        c.work_id = work
        yield c


def test_extract_then_list(client):
    r = client.post(f"/api/works/{client.work_id}/extract-refs",
                    json={"resolve": False, "query": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["entries"] == 31
    assert body["style"] == "bracketed"
    assert (body["own_doi"] or "").startswith("10.")

    listing = client.get(f"/api/works/{client.work_id}/refs").json()
    assert listing["text_layer"] == "text"
    assert len(listing["refs"]) == 31
    row = listing["refs"][0]
    assert row["source"] == "auto" and row["key"]


def test_patch_star_why_fix_persists(client):
    client.post(f"/api/works/{client.work_id}/extract-refs", json={"resolve": False, "query": False})
    rid = client.get(f"/api/works/{client.work_id}/refs").json()["refs"][0]["id"]
    assert client.patch(f"/api/refs/{rid}",
                        json={"star": 1, "why": "core", "fix": "p.9 checked"}).status_code == 200
    row = client.get(f"/api/works/{client.work_id}/refs").json()["refs"][0]
    assert row["star"] == 1 and row["why"] == "core" and row["fix"] == "p.9 checked"


def test_manual_lines_and_duplicates(client):
    text = ("Koning, M. (2022). Vasculogenesis in kidney organoids. npj Regen. Med. 7, 40.\n"
            "Chen, W. (2024). Tailoring cell sheets for biomedical applications. "
            "Smart Medicine 3, e20230038.\n")
    r1 = client.post(f"/api/works/{client.work_id}/refs/manual", json={"text": text})
    assert r1.status_code == 200, r1.text
    assert r1.json()["added"] == 2
    r2 = client.post(f"/api/works/{client.work_id}/refs/manual", json={"text": text})
    assert r2.json()["added"] == 0
    rows = client.get(f"/api/works/{client.work_id}/refs").json()["refs"]
    assert len(rows) == 2 and all(x["source"] == "manual" for x in rows)


def test_manual_dois_reject_junk(client):
    r = client.post(f"/api/works/{client.work_id}/refs/manual",
                    json={"dois": "10.1038/s41586-020-1234-5\nnot a doi\n", "resolve": False})
    body = r.json()
    assert body["added"] == 1 and len(body["rejected"]) == 1


def test_extraction_does_not_touch_manual_rows(client):
    client.post(f"/api/works/{client.work_id}/refs/manual",
                json={"text": "Koning, M. (2022). Vasculogenesis in kidney organoids. npj Regen. Med. 7, 40."})
    before = client.get(f"/api/works/{client.work_id}/refs").json()["refs"]
    client.post(f"/api/works/{client.work_id}/extract-refs", json={"resolve": False, "query": False})
    after = client.get(f"/api/works/{client.work_id}/refs").json()["refs"]
    manual_after = [r for r in after if r["source"] == "manual"]
    assert len(manual_after) == 1 and manual_after[0]["raw"] == before[0]["raw"]


def test_delete_manual_row(client):
    client.post(f"/api/works/{client.work_id}/refs/manual",
                json={"text": "Koning, M. (2022). Vasculogenesis in kidney organoids. npj Regen. Med. 7, 40."})
    rid = client.get(f"/api/works/{client.work_id}/refs").json()["refs"][0]["id"]
    assert client.delete(f"/api/refs/{rid}").status_code == 200
    assert client.get(f"/api/works/{client.work_id}/refs").json()["refs"] == []


def test_common_endpoint_counts_distinct_works(client):
    w2 = None
    conn = dbm.get_conn()
    w2 = dbm.upsert_work(conn, "refs-api-2", "Second paper")
    dbm.upsert_refs(conn, client.work_id, [{"raw": "shared", "key": "doi:10.1/shared",
                                            "style": "bracketed"}])
    dbm.upsert_refs(conn, w2, [{"raw": "shared", "key": "doi:10.1/shared",
                                "style": "bracketed"}])
    common = client.get("/api/refs/common?min_works=2").json()
    assert len(common) == 1 and common[0]["n_works"] == 2


def test_scan_reports_no_text_layer(client, tmp_path, monkeypatch):
    conn = dbm.get_conn()
    scan = real_pdf(97)
    work = dbm.upsert_work(conn, "refs-scan", "Scanned chapter")
    dbm.add_file(conn, work, scan, "def")
    r = client.post(f"/api/works/{work}/extract-refs", json={"resolve": False, "query": False})
    body = r.json()
    assert body["status"] == "no-text-layer"
    assert body["entries"] == 0
