"""Writer API: sanitiser applied on save, image rules, traversal guard."""
import pathlib

import pytest
from fastapi.testclient import TestClient

import app as appmod
import db as dbm


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "w.db")
    monkeypatch.setattr(dbm, "_db_path", db_path, raising=False)
    dbm._tls.__dict__.pop("conn", None)
    images = tmp_path / "drafts"
    monkeypatch.setattr(appmod, "DRAFT_IMAGES", images, raising=False)
    dbm.connect(db_path).close()
    with TestClient(appmod.app) as c:
        c.images = images
        yield c


def test_create_save_and_read_back(client):
    d = client.post("/api/drafts", json={"title": "My article"}).json()
    did = d["id"]
    r = client.put(f"/api/drafts/{did}", json={"html": '<p>Hello <b>world</b></p>',
                                               "title": "My article"})
    assert r.status_code == 200
    got = client.get(f"/api/drafts/{did}").json()
    assert "<strong>world</strong>" in got["html"]         # b folded to strong
    assert got["plain"].startswith("Hello world")


def test_script_is_stripped_on_save(client):
    did = client.post("/api/drafts", json={}).json()["id"]
    client.put(f"/api/drafts/{did}", json={"html": '<p>ok</p><script>alert(1)</script>'})
    html = client.get(f"/api/drafts/{did}").json()["html"]
    assert "script" not in html.lower() and "ok" in html


def test_save_unknown_draft_404(client):
    assert client.put("/api/drafts/9999", json={"html": "<p>x</p>"}).status_code == 404


def test_image_upload_and_serving(client):
    did = client.post("/api/drafts", json={}).json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 40
    r = client.post(f"/api/drafts/{did}/images",
                    files={"file": ("shot.png", png, "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith(f"/drafts/{did}/img-")
    got = client.get(url)
    assert got.status_code == 200 and got.content.startswith(b"\x89PNG")


def test_image_type_and_size_limits(client):
    did = client.post("/api/drafts", json={}).json()["id"]
    r = client.post(f"/api/drafts/{did}/images",
                    files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 415
    big = b"0" * (9 * 1024 * 1024)
    r = client.post(f"/api/drafts/{did}/images", files={"file": ("big.png", big, "image/png")})
    assert r.status_code == 413


def test_image_route_refuses_traversal(client):
    did = client.post("/api/drafts", json={}).json()["id"]
    for name in ("../../app.py", "..%2fapp.py", "img-001.png/../../x"):
        r = client.get(f"/drafts/{did}/{name}")
        assert r.status_code in (404, 400), (name, r.status_code)


def test_second_image_never_overwrites_the_first(client):
    did = client.post("/api/drafts", json={}).json()["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"1" * 10
    u1 = client.post(f"/api/drafts/{did}/images", files={"file": ("a.png", png, "image/png")}).json()["url"]
    u2 = client.post(f"/api/drafts/{did}/images", files={"file": ("a.png", png, "image/png")}).json()["url"]
    assert u1 != u2
