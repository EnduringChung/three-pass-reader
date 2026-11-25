"""Scan library roots for PDFs -> works + files (idempotent, sha256-stable)."""
import hashlib
import re
from pathlib import Path

SKIP_EXT = {".html", ".htm", ".zip", ".dmg", ".exe", ".docx", ".xlsx", ".pptx"}


def slugify(name):
    s = re.sub(r"[^A-Za-z0-9]+", "-", Path(name).stem.lower()).strip("-")
    return s[:80] or "untitled"


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def iter_pdfs(root):
    for p in Path(root).rglob("*"):
        if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith("._"):
            yield p


def scan_roots(conn, roots, full=False, log=print):
    """Upsert every PDF under roots. full=True on a library-wide scan (deactivates vanished files)."""
    import db as dbm
    n_new = 0
    seen_files = set()
    for root in roots:
        for pdf in iter_pdfs(root):
            path = str(pdf.resolve())
            seen_files.add(path)
            slug = slugify(pdf.name)
            work_id = dbm.upsert_work(conn, slug, pdf.stem)
            digest = sha256_of(path)
            existing = conn.execute(
                "SELECT id, sha256 FROM files WHERE path=?", (path,)).fetchone()
            if existing is None:
                dbm.add_file(conn, work_id, path, digest)
                n_new += 1
            elif existing["sha256"] != digest:
                # file changed on disk -> update hash, keep same file row (anchors stay valid)
                conn.execute("UPDATE files SET sha256=? WHERE id=?", (digest, existing["id"]))
                conn.commit()
    # hide DB rows whose files vanished (do not delete: anchors must survive)
    if full:
        rows = conn.execute("SELECT id, path FROM files").fetchall()
        for r in rows:
            if r["path"] not in seen_files:
                conn.execute("UPDATE files SET is_active=0 WHERE id=?", (r["id"],))
        conn.commit()
    n_works = conn.execute("SELECT COUNT(*) c FROM works").fetchone()["c"]
    n_files = conn.execute("SELECT COUNT(*) c FROM files WHERE is_active=1").fetchone()["c"]
    log(f"scan: {n_works} works, {n_files} active files, {n_new} new")
    return n_works, n_files, n_new
