"""API tests for the highlight endpoints (temp DB, never the live reader.db)."""
import pytest
from fastapi.testclient import TestClient

import app as appmod
import db as dbm


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    monkeypatch.setattr(dbm, "_db_path", db_path, raising=False)
    dbm._tls.__dict__.pop("conn", None)          # drop any connection bound to the old path
    conn = dbm.connect(db_path)
    work = dbm.upsert_work(conn, "api-1", "API 1")
    file_id = dbm.add_file(conn, work, "D:/x/api1.pdf", "abc")
    conn.close()
    with TestClient(appmod.app) as c:
        c.file_id = file_id
        yield c


def test_highlight_crud(client):
    fid = client.file_id
    r = client.post(f"/api/files/{fid}/highlights",
                    json={"page": 2, "color": "evidence",
                          "rects": [[0.1, 0.1, 0.6, 0.14], [0.1, 0.15, 0.4, 0.19]],
                          "text": "the data shows"})
    assert r.status_code == 200, r.text
    hid = r.json()["id"]

    rows = client.get(f"/api/files/{fid}/highlights").json()
    assert len(rows) == 1 and rows[0]["color"] == "evidence"
    assert len(rows[0]["rects"]) == 2
    assert client.get(f"/api/files/{fid}/highlights?page=2").json()[0]["id"] == hid
    assert client.get(f"/api/files/{fid}/highlights?page=7").json() == []

    assert client.patch(f"/api/highlights/{hid}", json={"color": "doubt"}).status_code == 200
    assert client.get(f"/api/files/{fid}/highlights").json()[0]["color"] == "doubt"

    assert client.delete(f"/api/highlights/{hid}").status_code == 200
    assert client.get(f"/api/files/{fid}/highlights").json() == []


def test_highlight_rejects_bad_payload(client):
    fid = client.file_id
    assert client.post(f"/api/files/{fid}/highlights",
                       json={"page": 1, "rects": []}).status_code == 400
    assert client.post(f"/api/files/{fid}/highlights",
                       json={"page": 1, "rects": [[0, 0, 1]]}).status_code == 400
    assert client.post(f"/api/files/{fid}/highlights",
                       json={"page": 1, "color": "chartreuse",
                             "rects": [[0, 0, 1, 1]]}).status_code == 400
