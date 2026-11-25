"""Snapshot + restore for the Three-Pass Reader.

The real backup is one command that copies the SQLite database, the capture
directory and a manifest of sha256 hashes; markdown/xlsx exports are exit
routes, not backups.

CLI:
    python backup.py                 # snapshot to backups/<timestamp>/
    python backup.py --list          # show snapshots with sizes
    python backup.py --restore DIR   # restore DIR into data/ (refuses unless --force)
"""
import datetime
import hashlib
import json
import pathlib
import shutil
import sqlite3
import sys

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
DB_PATH = DATA / "reader.db"
CAPTURES = DATA / "captures"
DRAFT_IMAGES = DATA / "drafts"
BACKUPS = ROOT / "backups"

TABLES = ("works", "files", "anchors", "cards", "card_anchors", "card_links",
          "answers", "listitems", "refs", "highlights")


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def counts(db_file):
    """Row counts per known table; a missing table is simply absent, not an error."""
    out = {}
    conn = sqlite3.connect(str(db_file))
    try:
        for t in TABLES:
            try:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                pass
        out["_integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return out


def snapshot(db_path=DB_PATH, captures=CAPTURES, drafts=None, out_dir=BACKUPS, label=""):
    """Write a consistent snapshot; returns (snapshot_dir, manifest)."""
    db_path = pathlib.Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"no database at {db_path}")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = pathlib.Path(out_dir) / (ts + (f"_{label}" if label else ""))
    n = 1
    while dest.exists():                     # two snapshots in the same second
        dest = pathlib.Path(out_dir) / f"{ts}_{n}"
        n += 1
    dest.mkdir(parents=True)

    # sqlite's own backup API: safe while the server holds a connection open
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest / "reader.db"))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    captures = pathlib.Path(captures)
    if captures.exists():
        shutil.copytree(captures, dest / "captures")
    drafts = pathlib.Path(drafts) if drafts else None
    if drafts and drafts.exists():
        shutil.copytree(drafts, dest / "drafts")          # writer images sit beside the DB

    files = []
    for p in sorted(dest.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            files.append({"path": p.relative_to(dest).as_posix(),
                          "bytes": p.stat().st_size,
                          "sha256": _sha256(p)})
    manifest = {
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_db": str(db_path),
        "counts": counts(dest / "reader.db"),
        "files": files,
        "total_bytes": sum(f["bytes"] for f in files),
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return dest, manifest


def verify(snapshot_dir):
    """Check every file in the manifest still hashes to what was recorded."""
    snapshot_dir = pathlib.Path(snapshot_dir)
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    bad = []
    for f in manifest["files"]:
        p = snapshot_dir / f["path"]
        if not p.exists():
            bad.append((f["path"], "missing"))
        elif _sha256(p) != f["sha256"]:
            bad.append((f["path"], "hash mismatch"))
    return manifest, bad


def restore(snapshot_dir, data_dir=DATA, force=False):
    """Copy a verified snapshot back over data/. Refuses if data/ has a DB and not force."""
    snapshot_dir = pathlib.Path(snapshot_dir)
    data_dir = pathlib.Path(data_dir)
    manifest, bad = verify(snapshot_dir)
    if bad:
        raise ValueError(f"snapshot failed verification: {bad[:5]}")
    target_db = data_dir / "reader.db"
    if target_db.exists() and not force:
        raise FileExistsError(f"{target_db} exists — pass force=True to overwrite")
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(snapshot_dir / "reader.db", target_db)
    for sub in ("captures", "drafts"):
        if (snapshot_dir / sub).exists():
            if (data_dir / sub).exists():
                shutil.rmtree(data_dir / sub)
            shutil.copytree(snapshot_dir / sub, data_dir / sub)
    return {"restored_from": str(snapshot_dir), "counts": counts(target_db)}


def list_snapshots(out_dir=BACKUPS):
    out = []
    out_dir = pathlib.Path(out_dir)
    if not out_dir.exists():
        return out
    for d in sorted(out_dir.iterdir(), reverse=True):
        mf = d / "manifest.json"
        if not d.is_dir() or not mf.exists():
            continue
        m = json.loads(mf.read_text(encoding="utf-8"))
        out.append({"dir": str(d), "created_at": m["created_at"],
                    "total_bytes": m.get("total_bytes", 0),
                    "counts": m.get("counts", {})})
    return out


def main(argv):
    args = [a for a in argv[1:]]
    if "--list" in args:
        for s in list_snapshots():
            mb = s["total_bytes"] / 1e6
            print(f"{s['dir']}  {s['created_at']}  {mb:.1f} MB  "
                  f"cards={s['counts'].get('cards')} anchors={s['counts'].get('anchors')}")
        return 0
    if "--restore" in args:
        i = args.index("--restore")
        target = args[i + 1] if len(args) > i + 1 else None
        if not target:
            print("usage: python backup.py --restore <snapshot-dir> [--force]")
            return 2
        try:
            info = restore(target, force="--force" in args)
        except (ValueError, FileExistsError) as e:
            print(f"refused: {e}")
            return 1
        print(f"restored {info['restored_from']}")
        print("counts:", info["counts"])
        return 0
    dest, manifest = snapshot()
    print(f"snapshot: {dest}")
    print(f"files: {len(manifest['files'])}  bytes: {manifest['total_bytes']}")
    print("counts:", manifest["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
