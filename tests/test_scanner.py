import scanner


def _mkpdf(p):
    # minimal valid PDF
    p.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                  b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                  b"3 0 obj<</Type/Page/Parent 2 0 R>>endobj\n"
                  b"trailer<</Root 1 0 R>>\n%%EOF")


def test_scan_idempotent(tmp_path):
    import db as dbm
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    _mkpdf(lib / "Ataie et al., 2026 Advanced Science.pdf")
    _mkpdf(lib / "sub" / "Walpole 2015.pdf")
    (lib / "notes.html").write_text("<html>")   # must be skipped
    (lib / "bundle.zip").write_bytes(b"zip")    # must be skipped

    db = tmp_path / "t.db"
    conn = dbm.connect(str(db))
    w, f, new = scanner.scan_roots(conn, [str(lib)])
    assert w == 2 and f == 2 and new == 2
    w2, f2, new2 = scanner.scan_roots(conn, [str(lib)])
    assert (w2, f2, new2) == (2, 2, 0)          # idempotent

    # same slug, changed content -> same file row, anchors untouched
    row = conn.execute("SELECT id FROM files").fetchone()
    aid = dbm.add_anchor(conn, row["id"], 3, None, "x.png")
    _mkpdf(lib / "Walpole 2015.pdf")
    (lib / "Walpole 2015.pdf").write_bytes(b"%PDF-1.4 changed")
    scanner.scan_roots(conn, [str(lib)])
    a = conn.execute("SELECT * FROM anchors WHERE id=?", (aid,)).fetchone()
    assert a is not None and a["file_id"] == row["id"]


def test_slugify():
    assert scanner.slugify("Ataie et al., 2026 Advanced Science.pdf") == "ataie-et-al-2026-advanced-science"
    assert scanner.slugify("!!!.pdf") == "untitled"
