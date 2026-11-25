"""Export: markdown must be complete and the matrix workbook must never be mutated."""
import hashlib
import json
import pathlib

import openpyxl
import pytest

import db as dbm
import export as ex


def make_db(tmp_path):
    conn = dbm.connect(str(tmp_path / "reader.db"))
    w = dbm.upsert_work(conn, "ex-1", "Export Paper: Hepatic")
    dbm.add_file(conn, w, "D:/x/ex1.pdf", "abc")
    dbm.upsert_answer(conn, w, "pass1", "category", "experimental + DLP")
    dbm.upsert_answer(conn, w, "pass1", "contributions", "thick vascularised construct")
    dbm.upsert_answer(conn, w, "pass2", "pipeline", "cells -> DLP -> perfusion")
    cid = dbm.create_card(conn, w, "pass2", observation="viability drops past 5 mm",
                          interpretation="diffusion limit", question="n per group?")
    dbm.add_listitem(conn, w, "pass2", "parking_lot", "GelMA", 0)
    return conn, w


def make_capture(tmp_path):
    data = tmp_path / "data"
    p = data / "captures" / "ex-1"
    p.mkdir(parents=True)
    (p / "cap-001.png").write_bytes(b"\x89PNG fake")
    return data


def make_matrix(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "All papers"
    ws.append(["Paper Type", "Authors", "Title", "Link", "Abstract", "Research Question",
               "Brief methodology", "Major Findings", "My Synthesis 1", "My synthesis 2",
               "My synthesis 3", "Notes"])
    ws.append(["Journal", "Someone", "", "", "", "", "", "", "", "", "", ""])
    wb.save(path)
    return path


def test_markdown_contents(tmp_path):
    conn, w = make_db(tmp_path)
    data = make_capture(tmp_path)
    fid = conn.execute("SELECT id FROM files WHERE work_id=?", (w,)).fetchone()["id"]
    cid = conn.execute("SELECT id FROM cards WHERE work_id=?", (w,)).fetchone()["id"]
    aid = dbm.add_anchor(conn, fid, 2, "[0,0,1,1]", "captures/ex-1/cap-001.png")
    dbm.link_anchor(conn, cid, aid)

    res = ex.export_work(conn, w, tmp_path / "exports", data_dir=data)
    md = pathlib.Path(res["markdown"]).read_text(encoding="utf-8")
    assert "# Export Paper: Hepatic" in md
    assert "experimental + DLP" in md                     # answer carried through
    assert "viability drops past 5 mm" in md              # card observation
    assert "diffusion limit" in md and "n per group?" in md
    assert "GelMA" in md                                  # parking lot list
    assert "](ex-1_assets/" in md and "p.2" in md          # image link is relative + paged
    assert res["images"] == 1
    assert (tmp_path / "exports" / "notes" / "ex-1_assets").exists()


def test_markdown_omits_empty_fields(tmp_path):
    conn = dbm.connect(str(tmp_path / "r.db"))
    w = dbm.upsert_work(conn, "bare", "Bare Paper")
    res = ex.export_work(conn, w, tmp_path / "exports", data_dir=tmp_path / "data")
    md = pathlib.Path(res["markdown"]).read_text(encoding="utf-8")
    assert "None" not in md and "**Category:**" not in md
    assert md.startswith("# Bare Paper")


def test_matrix_copy_only_and_row_written(tmp_path):
    conn, w = make_db(tmp_path)
    src = make_matrix(tmp_path / "matrix.xlsx")
    before = hashlib.sha256(src.read_bytes()).hexdigest()

    res = ex.export_work(conn, w, tmp_path / "exports", matrix_xlsx=src,
                         data_dir=tmp_path / "data")
    assert res["xlsx"], res
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before   # original untouched

    wb = openpyxl.load_workbook(res["xlsx"])
    ws = wb["All papers"]
    hdr = [c.value for c in ws[1]]
    rows = [[c.value for c in r] for r in ws.iter_rows(min_row=2)]
    filled = [r for r in rows if (r[hdr.index("Title")] or "").strip()]
    assert filled, rows
    row = filled[-1]
    assert row[hdr.index("Title")] == "Export Paper: Hepatic"
    assert row[hdr.index("Paper Type")] == "experimental + DLP"
    assert "notes:" in (row[hdr.index("Notes")] or "")
    # synthesis columns stay blank: the app must not invent his judgement
    for col in ("My Synthesis 1", "My synthesis 2", "My synthesis 3", "Abstract"):
        assert not (row[hdr.index(col)] or "").strip()


def test_matrix_missing_workbook_does_not_lose_markdown(tmp_path):
    conn, w = make_db(tmp_path)
    res = ex.export_work(conn, w, tmp_path / "exports",
                         matrix_xlsx=tmp_path / "nope.xlsx", data_dir=tmp_path / "data")
    assert pathlib.Path(res["markdown"]).exists()
    assert res["xlsx"] is None and "xlsx_error" in res
