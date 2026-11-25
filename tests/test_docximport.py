"""Importing Word documents into the writer.

The point of these tests is fidelity: headings stay headings, indented lists keep their depth,
tables survive, images get copied into the draft's own folder, and anything Word produces that the
editor cannot represent degrades to text instead of disappearing.
"""
import io
import pathlib
import struct
import zlib

import pytest
from docx import Document
from docx.shared import Pt

import docximport
import writerdoc


def _png(width=8, height=8, colour=(200, 30, 30)):
    """A minimal valid PNG, so the image path can be tested without Pillow."""
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def _sample(path):
    doc = Document()
    doc.core_properties.title = "Organoid vascularisation draft"

    doc.add_heading("Introduction", level=1)
    p = doc.add_paragraph()
    run = p.add_run("Organoids need a vessel network")
    run.bold = True
    p.add_run(" to survive beyond diffusion limits")
    p.add_run(", as argued elsewhere").italic = True

    doc.add_heading("Aims", level=2)
    doc.add_paragraph("Primary aim", style="List Bullet")

    # nested bullets: level 1 and level 2
    doc.add_paragraph("sub-point one", style="List Bullet 2")
    doc.add_paragraph("sub-point two", style="List Bullet 2")
    doc.add_paragraph("second primary aim", style="List Bullet")

    doc.add_paragraph("numbered step", style="List Number")

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Construct"
    table.cell(0, 1).text = "Viability (%)"
    table.cell(1, 0).text = "Hepatic"
    table.cell(1, 1).text = "92"

    doc.add_picture(io.BytesIO(_png()))
    doc.add_paragraph("A caption paragraph after the figure")
    doc.save(str(path))
    return path


def test_headings_runs_and_structure(tmp_path):
    src = _sample(tmp_path / "sample.docx")
    images = []
    raw, title, stats = docximport.docx_to_html(src, lambda e, b: images.append(e) or "/drafts/1/img-001.png")
    safe = writerdoc.sanitize(raw)

    assert title == "Organoid vascularisation draft"
    assert "<h1>Introduction</h1>" in safe
    assert "<h2>Aims</h2>" in safe
    assert "<strong>Organoids need a vessel network</strong>" in safe
    assert "<em>, as argued elsewhere</em>" in safe
    assert stats["headings"] == 2
    assert images == ["png"], "the embedded image must reach the sink"
    assert "<img" in safe


def test_nested_lists_keep_their_depth(tmp_path):
    src = _sample(tmp_path / "sample.docx")
    raw, _, _ = docximport.docx_to_html(src, lambda e, b: "")
    safe = writerdoc.sanitize(raw)

    assert safe.count("<ul>") >= 2, "level-2 bullets must nest, not flatten"
    assert safe.count("<ul>") == safe.count("</ul>")
    assert safe.count("<li>") == safe.count("</li>")
    # the nested list has to live INSIDE the preceding <li>, or the browser hoists it out
    assert "Primary aim<ul>" in safe
    assert "<ol>" in safe, "List Number must become an ordered list"


def test_tables_survive_and_reach_markdown(tmp_path):
    src = _sample(tmp_path / "sample.docx")
    raw, _, stats = docximport.docx_to_html(src, lambda e, b: "")
    safe = writerdoc.sanitize(raw)

    assert stats["tables"] == 1
    assert "<table>" in safe and safe.count("<td>") == 4
    md = writerdoc.to_markdown(safe)
    assert "| Construct | Viability (%) |" in md
    assert "| Hepatic | 92 |" in md
    assert writerdoc.to_plain(safe).startswith("Introduction")


def test_unknown_styles_degrade_to_paragraphs_not_silence(tmp_path):
    doc = Document()
    doc.add_paragraph("plain text", style="Normal")
    from docx.enum.style import WD_STYLE_TYPE
    doc.styles.add_style("Odd Custom Style", WD_STYLE_TYPE.PARAGRAPH)
    doc.add_paragraph("something odd", style="Odd Custom Style")
    path = tmp_path / "odd.docx"
    doc.save(str(path))

    raw, _, _ = docximport.docx_to_html(path, lambda e, b: "")
    safe = writerdoc.sanitize(raw)
    assert "plain text" in safe and "something odd" in safe


def test_hyperlink_keeps_target(tmp_path):
    doc = Document()
    p = doc.add_paragraph()
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), doc.part.relate_to("https://example.org/paper",
                                            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                                            is_external=True))
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "the paper"
    run.append(text)
    link.append(run)
    p._p.append(link)
    path = tmp_path / "link.docx"
    doc.save(str(path))

    raw, _, _ = docximport.docx_to_html(path, lambda e, b: "")
    safe = writerdoc.sanitize(raw)
    assert '<a href="https://example.org/paper">the paper</a>' in safe


def test_import_endpoint_creates_a_draft(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod

    dbp = tmp_path / "reader.db"
    monkeypatch.setattr(appmod, "DB_PATH", str(dbp))
    monkeypatch.setattr(appmod, "DRAFT_IMAGES", tmp_path / "drafts")
    monkeypatch.setattr(appmod, "DATA", tmp_path)
    import db
    db.init(str(dbp))

    src = _sample(tmp_path / "imported.docx")
    client = TestClient(appmod.app)
    with open(src, "rb") as f:
        r = client.post("/api/drafts/import", files={"file": ("imported.docx", f.read(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "Organoid vascularisation draft"
    assert "<h1>" in d["html"]
    assert d["stats"]["tables"] == 1
    assert d["source_name"] == "imported.docx"
    images = list((tmp_path / "drafts" / str(d["id"])).glob("img-*"))
    assert len(images) == 1 and images[0].read_bytes().startswith(b"\x89PNG")


def test_import_rejects_doc_and_oversize(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    import db

    dbp = tmp_path / "reader.db"
    monkeypatch.setattr(appmod, "DB_PATH", str(dbp))
    monkeypatch.setattr(appmod, "DRAFT_IMAGES", tmp_path / "drafts")
    monkeypatch.setattr(appmod, "DATA", tmp_path)
    db.init(str(dbp))
    client = TestClient(appmod.app)

    r = client.post("/api/drafts/import", files={"file": ("old.doc", b"\xd0\xcf\x11\xe0", "application/msword")})
    assert r.status_code == 415
    assert "docx" in r.json()["detail"]

    big = b"0" * (appmod.MAX_DOCX_BYTES + 1)
    r = client.post("/api/drafts/import", files={"file": ("big.docx", big,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 413


def test_deleting_an_imported_draft_never_touches_the_source_file(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    import db

    dbp = tmp_path / "reader.db"
    monkeypatch.setattr(appmod, "DB_PATH", str(dbp))
    monkeypatch.setattr(appmod, "DRAFT_IMAGES", tmp_path / "drafts")
    monkeypatch.setattr(appmod, "DATA", tmp_path)
    db.init(str(dbp))

    src = _sample(tmp_path / "keep-me.docx")
    before = src.read_bytes()
    client = TestClient(appmod.app)
    d = client.post("/api/drafts/import",
                    files={"file": ("keep-me.docx", before,
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}).json()
    assert d["source_name"] == "keep-me.docx"

    r = client.delete(f"/api/drafts/{d['id']}")
    assert r.status_code == 200
    assert src.exists(), "the user's .docx must survive deleting the document"
    assert src.read_bytes() == before, "and must be byte-identical"

    # our own image copies go, the source does not
    assert not (tmp_path / "drafts" / str(d["id"])).exists()
    assert client.get(f"/api/drafts/{d['id']}").status_code == 404
