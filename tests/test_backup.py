"""Backup/restore: a snapshot must round-trip the DB and captures, and refuse a bad one."""
import json
import shutil

import pytest

import backup as bk
import db as dbm


def make_project(tmp_path):
    root = tmp_path / "proj"
    data = root / "data"
    data.mkdir(parents=True)
    conn = dbm.connect(str(data / "reader.db"))
    w = dbm.upsert_work(conn, "bk-1", "Backup Paper")
    f = dbm.add_file(conn, w, "D:/x/bk1.pdf", "abc")
    cid = dbm.create_card(conn, w, "pass2", observation="aggregate cells fastest")
    aid = dbm.add_anchor(conn, f, 3, "[0.1,0.2,0.5,0.6]", "captures/bk-1/cap-001.png")
    dbm.link_anchor(conn, cid, aid)
    cap = data / "captures" / "bk-1"
    cap.mkdir(parents=True)
    (cap / "cap-001.png").write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")
    conn.close()
    return root, data


def test_snapshot_hashes_and_counts(tmp_path):
    root, data = make_project(tmp_path)
    dest, manifest = bk.snapshot(db_path=data / "reader.db", captures=data / "captures",
                                 out_dir=tmp_path / "backups")
    assert (dest / "reader.db").exists()
    assert (dest / "captures" / "bk-1" / "cap-001.png").exists()
    assert manifest["counts"]["cards"] == 1 and manifest["counts"]["anchors"] == 1
    assert manifest["counts"]["_integrity"] == "ok"
    assert len(manifest["files"]) == 2
    _, bad = bk.verify(dest)
    assert bad == []


def test_verify_detects_tampering(tmp_path):
    root, data = make_project(tmp_path)
    dest, _ = bk.snapshot(db_path=data / "reader.db", captures=data / "captures",
                          out_dir=tmp_path / "backups")
    png = dest / "captures" / "bk-1" / "cap-001.png"
    png.write_bytes(b"tampered")
    _, bad = bk.verify(dest)
    assert bad and bad[0][1] == "hash mismatch"


def test_restore_roundtrip(tmp_path):
    root, data = make_project(tmp_path)
    dest, _ = bk.snapshot(db_path=data / "reader.db", captures=data / "captures",
                          out_dir=tmp_path / "backups")
    target = tmp_path / "restored"
    info = bk.restore(dest, data_dir=target)
    assert info["counts"]["cards"] == 1
    assert (target / "captures" / "bk-1" / "cap-001.png").read_bytes().startswith(b"\x89PNG")

    conn = dbm.connect(str(target / "reader.db"))
    assert "aggregate" in dbm.search_cards(conn, "aggregate")[0]["obs_snip"]
    conn.close()


def test_restore_refuses_existing_db_without_force(tmp_path):
    root, data = make_project(tmp_path)
    dest, _ = bk.snapshot(db_path=data / "reader.db", captures=data / "captures",
                          out_dir=tmp_path / "backups")
    with pytest.raises(FileExistsError):
        bk.restore(dest, data_dir=data)
    info = bk.restore(dest, data_dir=data, force=True)     # --force overwrites
    assert info["counts"]["cards"] == 1


def test_restore_refuses_tampered_snapshot(tmp_path):
    root, data = make_project(tmp_path)
    dest, _ = bk.snapshot(db_path=data / "reader.db", captures=data / "captures",
                          out_dir=tmp_path / "backups")
    (dest / "reader.db").write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        bk.restore(dest, data_dir=tmp_path / "nope", force=True)


def test_list_snapshots(tmp_path):
    root, data = make_project(tmp_path)
    out = tmp_path / "backups"
    bk.snapshot(db_path=data / "reader.db", captures=data / "captures", out_dir=out)
    bk.snapshot(db_path=data / "reader.db", captures=data / "captures", out_dir=out)
    snaps = bk.list_snapshots(out)
    assert len(snaps) == 2
    assert all(s["counts"]["cards"] == 1 for s in snaps)


def test_snapshot_and_restore_include_draft_images(tmp_path):
    root, data = make_project(tmp_path)
    drafts = data / "drafts" / "1"
    drafts.mkdir(parents=True)
    (drafts / "img-001.png").write_bytes(b"\x89PNG draft image")
    dest, manifest = bk.snapshot(db_path=data / "reader.db", captures=data / "captures",
                                 drafts=data / "drafts", out_dir=tmp_path / "backups")
    assert (dest / "drafts" / "1" / "img-001.png").exists()
    assert any(f["path"].startswith("drafts/") for f in manifest["files"])

    target = tmp_path / "restored2"
    bk.restore(dest, data_dir=target)
    assert (target / "drafts" / "1" / "img-001.png").read_bytes().startswith(b"\x89PNG")
