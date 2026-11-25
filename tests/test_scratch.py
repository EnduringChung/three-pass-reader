"""The scratch pad is a draft with kind='scratch'.

It must stay out of the writing list, be created once per paper, and carry over whatever was already
in the old plain-text scratchpad so a change of editor never loses his notes.
"""
import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    import db

    dbp = tmp_path / "reader.db"
    monkeypatch.setattr(appmod, "DB_PATH", str(dbp))
    monkeypatch.setattr(appmod, "DRAFT_IMAGES", tmp_path / "drafts")
    monkeypatch.setattr(appmod, "DATA", tmp_path)
    db.init(str(dbp))
    db._tls.__dict__.pop("conn", None)
    conn = db.get_conn()
    wid = db.upsert_work(conn, "paper-scratch", "A paper with a scratch pad")
    return TestClient(appmod.app), conn, wid


def test_scratch_is_created_once_and_reused(env):
    http, conn, wid = env
    first = http.get(f"/api/works/{wid}/scratch").json()
    second = http.get(f"/api/works/{wid}/scratch").json()
    assert first["id"] == second["id"], "opening the tab twice must not make two pads"
    assert first["kind"] == "scratch"
    assert str(wid) == str(first["work_id"])


def test_scratch_stays_out_of_the_writing_list(env):
    http, conn, wid = env
    http.get(f"/api/works/{wid}/scratch")
    docs = http.get("/api/drafts").json()
    assert all(d["kind"] == "doc" for d in docs)
    assert not any(str(d.get("work_id")) == str(wid) for d in docs)


def test_legacy_plain_text_scratchpad_is_carried_over(env):
    http, conn, wid = env
    conn.execute("INSERT INTO answers(work_id, pass, qkey, value) VALUES (?,?,?,?)",
                 (wid, "scratch", "scratchpad", "Potential application\n   - drug screening\n   - tumour biology"))
    conn.commit()

    d = http.get(f"/api/works/{wid}/scratch").json()
    assert "Potential application" in d["html"]
    assert "drug screening" in d["html"]
    # the sanitiser re-encodes the entity as a literal non-breaking space
    assert ("&nbsp;" in d["html"]) or ("\xa0" in d["html"]), "indentation should survive the conversion"
    assert d["plain"].count("\n") >= 2
    # the answer is cleared once copied, so deleting the pad cannot resurrect stale text
    left = conn.execute("SELECT value FROM answers WHERE work_id=? AND qkey='scratchpad'",
                        (wid,)).fetchone()
    assert (left["value"] or "") == ""


def test_scratch_is_editable_like_any_draft(env):
    http, conn, wid = env
    d = http.get(f"/api/works/{wid}/scratch").json()
    r = http.put(f"/api/drafts/{d['id']}", json={
        "html": "<h2>Idea</h2><p>Vascularise with HUVECs</p>",
        "title": "Scratch — paper"})
    assert r.status_code == 200
    back = http.get(f"/api/drafts/{d['id']}").json()
    assert "<h2>Idea</h2>" in back["html"]


def test_deleting_a_scratch_pad_recreates_empty(env):
    http, conn, wid = env
    d = http.get(f"/api/works/{wid}/scratch").json()
    http.put(f"/api/drafts/{d['id']}", json={"html": "<p>temporary</p>"})
    http.delete(f"/api/drafts/{d['id']}")
    again = http.get(f"/api/works/{wid}/scratch").json()
    assert "temporary" not in (again["html"] or ""), "a fresh pad starts empty"
    assert again["kind"] == "scratch"


def test_scratch_for_unknown_work_is_404(env):
    http, conn, wid = env
    assert http.get("/api/works/999999/scratch").status_code == 404
