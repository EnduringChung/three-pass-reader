"""Draft CRUD + the citation picker query."""
import db as dbm


def make_db(tmp_path):
    return dbm.connect(str(tmp_path / "d.db"))


def test_draft_roundtrip_and_listing(tmp_path):
    conn = make_db(tmp_path)
    a = dbm.create_draft(conn, "First", None)
    b = dbm.create_draft(conn, "Second", 7)
    assert dbm.get_draft(conn, a)["title"] == "First"
    assert [d["id"] for d in dbm.list_drafts(conn)] == [b, a]          # newest first
    assert [d["id"] for d in dbm.list_drafts(conn, work_id=7, include_all=False)] == [b]
    dbm.update_draft(conn, a, html_text="<p>x</p>", plain="x", title="Renamed")
    got = dbm.get_draft(conn, a)
    assert got["title"] == "Renamed" and got["html"] == "<p>x</p>" and got["plain"] == "x"
    dbm.delete_draft(conn, a)
    assert dbm.get_draft(conn, a) is None


def test_citable_search_finds_refs_and_works(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "bbb", "Blood-brain barrier model")
    dbm.add_file(conn, w, "D:/x/bbb.pdf", "abc")
    dbm.upsert_refs(conn, w, [{"raw": "Abbott NJ (2010). Structure and function. Neurobiol Dis.",
                               "title_norm": "structure and function of the blood brain barrier",
                               "authors": "Abbott NJ", "year": "2010", "key": "doi:10.1/abbott",
                               "style": "bracketed"}])
    hits = dbm.search_citable(conn, "abbott")
    assert hits and hits[0]["kind"] == "ref" and hits[0]["year"] == "2010"
    assert "Structure and function" in hits[0]["raw"], "picker must carry the full reference"
    hits2 = dbm.search_citable(conn, "barrier")
    assert any(h["kind"] == "work" for h in hits2)


def test_list_carries_source_name_so_delete_can_say_the_file_is_safe(tmp_path):
    import db
    dbp = tmp_path / "reader.db"
    db.init(str(dbp))
    conn = db.get_conn()
    did = db.create_draft(conn, "Imported", source_name="paper.docx")
    rows = db.list_drafts(conn)
    row = [r for r in rows if r["id"] == did][0]
    assert row["source_name"] == "paper.docx"
