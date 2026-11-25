"""SQLite layer: works, files, anchors, cards (+FTS), answers, listitems, refs."""
import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE, title TEXT,
  verdict TEXT,
  pass1_done INT DEFAULT 0, pass2_done INT DEFAULT 0, pass3_done INT DEFAULT 0,
  active_file_id INT, added_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS files (
  id INTEGER PRIMARY KEY, work_id INT, path TEXT UNIQUE,
  label TEXT DEFAULT '', sha256 TEXT, is_active INT DEFAULT 1
);
CREATE TABLE IF NOT EXISTS anchors (
  id INTEGER PRIMARY KEY, file_id INT, page INT,
  rect TEXT, image_path TEXT
);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY, work_id INT, pass TEXT,
  status TEXT DEFAULT 'seed',
  observation TEXT DEFAULT '', interpretation TEXT DEFAULT '',
  question TEXT DEFAULT '', resolution TEXT DEFAULT '',
  audit TEXT DEFAULT '{}',
  ord INT DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS card_anchors (
  card_id INT, anchor_id INT, ord INT DEFAULT 0,
  PRIMARY KEY (card_id, anchor_id)
);
CREATE TABLE IF NOT EXISTS card_links (
  src_id INT, dst_id INT, created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (src_id, dst_id)
);
CREATE TABLE IF NOT EXISTS answers (
  work_id INT, pass TEXT, qkey TEXT, value TEXT DEFAULT '',
  updated_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (work_id, pass, qkey)
);
CREATE TABLE IF NOT EXISTS listitems (
  id INTEGER PRIMARY KEY, work_id INT, pass TEXT, kind TEXT,
  value TEXT DEFAULT '', done INT DEFAULT 0, ord INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS refs (
  id INTEGER PRIMARY KEY, work_id INT, raw TEXT, title_norm TEXT DEFAULT '',
  doi TEXT DEFAULT '', year TEXT DEFAULT '', authors TEXT DEFAULT '',
  match_status TEXT DEFAULT 'unparsed', match_work_id INT,
  star INT DEFAULT 0, fix TEXT DEFAULT '', why TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_refs_norm ON refs(title_norm);
CREATE TABLE IF NOT EXISTS highlights (
  id INTEGER PRIMARY KEY,
  file_id INT NOT NULL,
  page INT NOT NULL,
  color TEXT NOT NULL DEFAULT 'claim',
  text TEXT DEFAULT '',
  rects TEXT NOT NULL,
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hl_file ON highlights(file_id, page);
CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY,
  title TEXT DEFAULT 'Untitled',
  html TEXT DEFAULT '',
  plain TEXT DEFAULT '',
  work_id INT,
  source_name TEXT DEFAULT '',
  kind TEXT DEFAULT 'doc',
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_drafts_work ON drafts(work_id);
CREATE TABLE IF NOT EXISTS doi_meta (
  doi TEXT PRIMARY KEY,
  json TEXT DEFAULT '',
  fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
  observation, interpretation, question, resolution, content='cards', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS cards_ai AFTER INSERT ON cards BEGIN
  INSERT INTO cards_fts(rowid, observation, interpretation, question, resolution)
  VALUES (new.id, new.observation, new.interpretation, new.question, new.resolution);
END;
CREATE TRIGGER IF NOT EXISTS cards_ad AFTER DELETE ON cards BEGIN
  INSERT INTO cards_fts(cards_fts, rowid, observation, interpretation, question, resolution)
  VALUES ('delete', old.id, old.observation, old.interpretation, old.question, old.resolution);
END;
CREATE TRIGGER IF NOT EXISTS cards_au AFTER UPDATE ON cards BEGIN
  INSERT INTO cards_fts(cards_fts, rowid, observation, interpretation, question, resolution)
  VALUES ('delete', old.id, old.observation, old.interpretation, old.question, old.resolution);
  INSERT INTO cards_fts(rowid, observation, interpretation, question, resolution)
  VALUES (new.id, new.observation, new.interpretation, new.question, new.resolution);
END;
"""


def connect(db_path):
    """Open (creating) the DB with schema applied; returns sqlite3 connection (row_factory set)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def migrate(conn):
    """Column additions that CREATE TABLE IF NOT EXISTS cannot express. Idempotent."""
    def cols(table):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}

    ref_cols = cols("refs")
    for name, decl in (("style", "TEXT DEFAULT ''"), ("key", "TEXT DEFAULT ''"),
                       ("source", "TEXT DEFAULT 'auto'"), ("meta", "TEXT DEFAULT ''")):
        if name not in ref_cols:
            conn.execute(f"ALTER TABLE refs ADD COLUMN {name} {decl}")
    if "kind" not in cols("drafts"):
        conn.execute("ALTER TABLE drafts ADD COLUMN kind TEXT DEFAULT 'doc'")
    if "starred" not in cols("works"):
        conn.execute("ALTER TABLE works ADD COLUMN starred INTEGER DEFAULT 0")
    if "source_name" not in cols("drafts"):
        conn.execute("ALTER TABLE drafts ADD COLUMN source_name TEXT DEFAULT ''")
    if "text_layer" not in cols("files"):
        conn.execute("ALTER TABLE files ADD COLUMN text_layer TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refs_key ON refs(key)")
    conn.commit()


_tls = threading.local()
_db_path = None


def init(db_path):
    global _db_path
    _db_path = db_path


def get_conn():
    """Thread-local connection (FastAPI runs sync endpoints in a threadpool)."""
    c = getattr(_tls, "conn", None)
    if c is None:
        c = connect(_db_path)
        _tls.conn = c
    return c


# ---------- works / files ----------

def upsert_work(conn, slug, title):
    cur = conn.execute("SELECT id FROM works WHERE slug=?", (slug,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO works(slug, title) VALUES (?,?)", (slug, title))
    conn.commit()
    return cur.lastrowid


def add_file(conn, work_id, path, sha256, label=""):
    cur = conn.execute("SELECT id FROM files WHERE path=?", (path,))
    row = cur.fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO files(work_id, path, sha256, label) VALUES (?,?,?,?)",
        (work_id, path, sha256, label))
    conn.execute("UPDATE works SET active_file_id=? WHERE id=? AND active_file_id IS NULL",
                 (cur.lastrowid, work_id))
    conn.commit()
    return cur.lastrowid


def get_work(conn, work_id):
    return conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()


# ---------- answers ----------

def upsert_answer(conn, work_id, pass_name, qkey, value):
    conn.execute(
        "INSERT INTO answers(work_id, pass, qkey, value, updated_at) VALUES (?,?,?,?,datetime('now')) "
        "ON CONFLICT(work_id, pass, qkey) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (work_id, pass_name, qkey, value))
    conn.commit()


def get_answers(conn, work_id, pass_name):
    rows = conn.execute(
        "SELECT qkey, value, updated_at FROM answers WHERE work_id=? AND pass=?",
        (work_id, pass_name)).fetchall()
    return {r["qkey"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}


# ---------- anchors ----------

def add_anchor(conn, file_id, page, rect, image_path):
    cur = conn.execute(
        "INSERT INTO anchors(file_id, page, rect, image_path) VALUES (?,?,?,?)",
        (file_id, page, rect, image_path))
    conn.commit()
    return cur.lastrowid


def get_anchor(conn, anchor_id):
    return conn.execute("SELECT * FROM anchors WHERE id=?", (anchor_id,)).fetchone()


# ---------- cards ----------

def create_card(conn, work_id, pass_name, ord_=0, **fields):
    cols = ["observation", "interpretation", "question", "resolution", "audit", "status"]
    vals = {c: fields.get(c, "") for c in cols}
    cur = conn.execute(
        f"INSERT INTO cards(work_id, pass, ord, {', '.join(cols)}) VALUES (?,?,?,{', '.join('?' * len(cols))})",
        (work_id, pass_name, ord_, *[vals[c] for c in cols]))
    conn.commit()
    return cur.lastrowid


def update_card(conn, card_id, fields):
    allowed = ["observation", "interpretation", "question", "resolution", "audit", "status", "ord"]
    sets, vals = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    if not sets:
        return
    sets.append("updated_at=datetime('now')")
    vals.append(card_id)
    conn.execute(f"UPDATE cards SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()


def delete_card(conn, card_id):
    conn.execute("DELETE FROM card_anchors WHERE card_id=?", (card_id,))
    conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
    conn.commit()


def get_cards(conn, work_id, pass_name=None):
    if pass_name:
        rows = conn.execute(
            "SELECT * FROM cards WHERE work_id=? AND pass=? ORDER BY ord, id",
            (work_id, pass_name)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM cards WHERE work_id=? ORDER BY ord, id", (work_id,)).fetchall()
    cards = [dict(r) for r in rows]
    for c in cards:
        c["anchors"] = [
            dict(r) for r in conn.execute(
                "SELECT a.* FROM anchors a JOIN card_anchors ca ON ca.anchor_id=a.id "
                "WHERE ca.card_id=? ORDER BY ca.ord", (c["id"],)).fetchall()]
    return cards


def link_anchor(conn, card_id, anchor_id, ord_=0):
    conn.execute(
        "INSERT OR IGNORE INTO card_anchors(card_id, anchor_id, ord) VALUES (?,?,?)",
        (card_id, anchor_id, ord_))
    conn.commit()


# ---------- card <-> card links (Zettelkasten) ----------

def link_cards(conn, src_id, dst_id):
    if src_id == dst_id:
        raise ValueError("cannot link a card to itself")
    conn.execute("INSERT OR IGNORE INTO card_links(src_id, dst_id) VALUES (?,?)", (src_id, dst_id))
    conn.commit()


def unlink_cards(conn, src_id, dst_id):
    conn.execute("DELETE FROM card_links WHERE src_id=? AND dst_id=?", (src_id, dst_id))
    conn.commit()


def get_card_links(conn, card_id):
    """Outgoing links + incoming backlinks, each with work title for display."""
    out = [dict(r) for r in conn.execute(
        "SELECT c.id, c.work_id, c.pass, c.status, c.question, c.observation, w.title "
        "FROM card_links l JOIN cards c ON c.id=l.dst_id JOIN works w ON w.id=c.work_id "
        "WHERE l.src_id=?", (card_id,)).fetchall()]
    back = [dict(r) for r in conn.execute(
        "SELECT c.id, c.work_id, c.pass, c.status, c.question, c.observation, w.title "
        "FROM card_links l JOIN cards c ON c.id=l.src_id JOIN works w ON w.id=c.work_id "
        "WHERE l.dst_id=?", (card_id,)).fetchall()]
    return {"links": out, "backlinks": back}


def search_card_picker(conn, query, limit=20):
    """Card picker for linking: id, title-ish label, work title."""
    rows = conn.execute(
        "SELECT c.id, c.work_id, c.pass, c.status, c.question, c.observation, w.title "
        "FROM cards_fts f JOIN cards c ON c.id=f.rowid JOIN works w ON w.id=c.work_id "
        "WHERE cards_fts MATCH ? LIMIT ?", (query, limit)).fetchall()
    return [dict(r) for r in rows]


# ---------- search ----------

def search_cards(conn, query, limit=50):
    rows = conn.execute(
        "SELECT c.id, c.work_id, c.pass, c.status, w.title, "
        "snippet(cards_fts, 0, '[', ']', ' … ', 12) AS obs_snip, "
        "snippet(cards_fts, 2, '[', ']', ' … ', 12) AS q_snip, "
        "(SELECT COUNT(*) FROM card_anchors ca WHERE ca.card_id=c.id) AS n_anchors, "
        "(SELECT a.id FROM card_anchors ca JOIN anchors a ON a.id=ca.anchor_id "
        "  WHERE ca.card_id=c.id ORDER BY ca.ord LIMIT 1) AS first_anchor "
        "FROM cards_fts f JOIN cards c ON c.id=f.rowid JOIN works w ON w.id=c.work_id "
        "WHERE cards_fts MATCH ? LIMIT ?", (query, limit)).fetchall()
    return [dict(r) for r in rows]


# ---------- listitems ----------

def add_listitem(conn, work_id, pass_name, kind, value, ord_=0):
    cur = conn.execute(
        "INSERT INTO listitems(work_id, pass, kind, value, ord) VALUES (?,?,?,?,?)",
        (work_id, pass_name, kind, value, ord_))
    conn.commit()
    return cur.lastrowid


def get_listitems(conn, work_id, pass_name, kind):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM listitems WHERE work_id=? AND pass=? AND kind=? ORDER BY ord, id",
        (work_id, pass_name, kind)).fetchall()]


def delete_listitem(conn, item_id):
    conn.execute("DELETE FROM listitems WHERE id=?", (item_id,))
    conn.commit()


# ---------- highlights ----------

HL_COLORS = ("claim", "evidence", "method", "doubt", "jargon")


def add_highlight(conn, file_id, page, color, rects, text="", note=""):
    if color not in HL_COLORS:
        raise ValueError(f"bad highlight colour: {color}")
    cur = conn.execute(
        "INSERT INTO highlights(file_id, page, color, text, rects, note) VALUES (?,?,?,?,?,?)",
        (file_id, page, color, text[:500], json.dumps(rects), note))
    conn.commit()
    return cur.lastrowid


def get_highlights(conn, file_id, page=None):
    sql = "SELECT * FROM highlights WHERE file_id=?"
    args = [file_id]
    if page is not None:
        sql += " AND page=?"
        args.append(page)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY page, id", args)]
    for r in rows:
        r["rects"] = json.loads(r["rects"] or "[]")
    return rows


def update_highlight(conn, hid, color=None, note=None):
    if color is not None:
        if color not in HL_COLORS:
            raise ValueError(f"bad highlight colour: {color}")
        conn.execute("UPDATE highlights SET color=? WHERE id=?", (color, hid))
    if note is not None:
        conn.execute("UPDATE highlights SET note=? WHERE id=?", (note, hid))
    conn.commit()


def delete_highlight(conn, hid):
    conn.execute("DELETE FROM highlights WHERE id=?", (hid,))
    conn.commit()


# ---------- verdict ----------

VERDICTS = ("advance", "park", "skip")


def set_verdict(conn, work_id, verdict):
    """verdict: 'advance' | 'park' | 'skip' | '' (clears)."""
    verdict = (verdict or "").strip()
    if verdict and verdict not in VERDICTS:
        raise ValueError(f"bad verdict: {verdict}")
    conn.execute("UPDATE works SET verdict=? WHERE id=?", (verdict or None, work_id))
    conn.commit()
    return verdict or None


# ---------- references ----------

REF_COLS = ("raw", "title_norm", "doi", "year", "authors", "match_status",
            "match_work_id", "style", "key", "source", "meta")


def upsert_refs(conn, work_id, rows):
    """Insert/update extracted refs keyed on (work_id, key).

    Rules that must not regress:
      * star / fix / why are NEVER touched here — his curation cannot be regenerated.
      * rows with source='manual' are skipped entirely (his typing outranks the parser).
      * a manual row already holding the key wins; the auto row is dropped.
    Returns {'added', 'updated', 'skipped_manual', 'collisions'}.
    """
    stats = {"added": 0, "updated": 0, "skipped_manual": 0, "collisions": 0}
    for r in rows:
        key = r.get("key") or ""
        if not key:
            continue
        existing = conn.execute(
            "SELECT id, source FROM refs WHERE work_id=? AND key=?", (work_id, key)).fetchone()
        if existing:
            if existing["source"] == "manual":
                stats["collisions"] += 1
                stats["skipped_manual"] += 1
                continue
            conn.execute(
                "UPDATE refs SET raw=?, title_norm=?, doi=?, year=?, authors=?, "
                "match_status=?, match_work_id=?, style=?, meta=? WHERE id=?",
                (r.get("raw", ""), r.get("title_norm", ""), r.get("doi", ""), r.get("year", ""),
                 r.get("authors", ""), r.get("match_status", "unparsed"),
                 r.get("match_work_id"), r.get("style", ""), r.get("meta", ""), existing["id"]))
            stats["updated"] += 1
        else:
            conn.execute(
                "INSERT INTO refs(work_id, raw, title_norm, doi, year, authors, match_status, "
                "match_work_id, style, key, source, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (work_id, r.get("raw", ""), r.get("title_norm", ""), r.get("doi", ""),
                 r.get("year", ""), r.get("authors", ""), r.get("match_status", "unparsed"),
                 r.get("match_work_id"), r.get("style", ""), key, r.get("source", "auto"),
                 r.get("meta", "")))
            stats["added"] += 1
    conn.commit()
    return stats


def get_refs(conn, work_id):
    return [dict(r) for r in conn.execute(
        "SELECT r.*, w.title AS match_work_title FROM refs r "
        "LEFT JOIN works w ON w.id=r.match_work_id "
        "WHERE r.work_id=? ORDER BY r.id", (work_id,)).fetchall()]


def get_ref(conn, ref_id):
    row = conn.execute("SELECT * FROM refs WHERE id=?", (ref_id,)).fetchone()
    return dict(row) if row else None


def update_ref(conn, ref_id, fields):
    allowed = ["star", "fix", "why", "match_status", "match_work_id", "doi", "year",
               "authors", "title_norm", "raw", "key"]
    sets, vals = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k}=?")
            vals.append(fields[k])
    if not sets:
        return False
    vals.append(ref_id)
    conn.execute(f"UPDATE refs SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return True


def delete_ref(conn, ref_id):
    conn.execute("DELETE FROM refs WHERE id=?", (ref_id,))
    conn.commit()


def refs_for_work_keys(conn, work_id):
    return {r["key"] for r in conn.execute(
        "SELECT key FROM refs WHERE work_id=?", (work_id,)).fetchall()}


def refs_common(conn, min_works=2, limit=200):
    """References ranked by how many DISTINCT works cite them."""
    rows = conn.execute(
        "SELECT key, COUNT(DISTINCT work_id) AS n_works, COUNT(*) AS n_rows "
        "FROM refs WHERE key != '' GROUP BY key HAVING n_works >= ? "
        "ORDER BY n_works DESC, n_rows DESC LIMIT ?", (min_works, limit)).fetchall()
    out = []
    for r in rows:
        cites = [dict(x) for x in conn.execute(
            "SELECT r.id, r.work_id, r.raw, r.doi, r.year, r.authors, r.title_norm, r.star, w.title "
            "FROM refs r JOIN works w ON w.id=r.work_id WHERE r.key=? ORDER BY r.work_id", (r["key"],))]
        out.append({"key": r["key"], "n_works": r["n_works"], "n_rows": r["n_rows"], "cites": cites})
    return out


def doi_cache_get(conn, doi):
    row = conn.execute("SELECT json FROM doi_meta WHERE doi=?", (doi,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])          # positional: works with or without row_factory
    except (ValueError, TypeError, IndexError):
        return None


def doi_cache_put(conn, doi, payload):
    conn.execute(
        "INSERT INTO doi_meta(doi, json, fetched_at) VALUES (?,?,datetime('now')) "
        "ON CONFLICT(doi) DO UPDATE SET json=excluded.json, fetched_at=excluded.fetched_at",
        (doi, json.dumps(payload)))
    conn.commit()


def set_text_layer(conn, file_id, status):
    conn.execute("UPDATE files SET text_layer=? WHERE id=?", (status, file_id))
    conn.commit()


def set_work_doi(conn, work_id, doi):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(works)")}
    if "doi" not in cols:
        conn.execute("ALTER TABLE works ADD COLUMN doi TEXT DEFAULT ''")
    conn.execute("UPDATE works SET doi=? WHERE id=?", (doi or "", work_id))
    conn.commit()


def set_star(conn, work_id, starred):
    """Favourite marker. Returns the stored value so the UI never has to guess."""
    val = 1 if starred else 0
    conn.execute("UPDATE works SET starred=? WHERE id=?", (val, work_id))
    conn.commit()
    row = conn.execute("SELECT starred FROM works WHERE id=?", (work_id,)).fetchone()
    return int(row["starred"]) if row else 0


# ---------- writer drafts ----------

def create_draft(conn, title="Untitled", work_id=None, source_name="", kind="doc"):
    cur = conn.execute("INSERT INTO drafts(title, work_id, source_name, kind) VALUES (?,?,?,?)",
                       (title, work_id, source_name, kind))
    conn.commit()
    return cur.lastrowid


def get_scratch_draft(conn, work_id):
    """The per-paper scratch pad is a draft with kind='scratch' (rich text, same editor as /write)."""
    return conn.execute("SELECT * FROM drafts WHERE work_id=? AND kind='scratch' "
                        "ORDER BY id LIMIT 1", (work_id,)).fetchone()


def get_draft(conn, draft_id):
    row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    return dict(row) if row else None


def list_drafts(conn, work_id=None, include_all=True, kind="doc"):
    """`kind='doc'` by default: scratch pads belong to a paper, not to the writing list."""
    sql = ("SELECT id, title, work_id, source_name, kind, updated_at, length(plain) AS n_chars "
           "FROM drafts")
    where, args = [], []
    if work_id is not None and not include_all:
        where.append("work_id=?")
        args.append(work_id)
    if kind is not None:
        where.append("kind=?")
        args.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [dict(r) for r in conn.execute(sql + " ORDER BY updated_at DESC, id DESC", args)]


def update_draft(conn, draft_id, html_text=None, plain=None, title=None):
    sets, vals = [], []
    for k, v in (("html", html_text), ("plain", plain), ("title", title)):
        if v is not None:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return False
    sets.append("updated_at=datetime('now')")
    vals.append(draft_id)
    conn.execute(f"UPDATE drafts SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    return True


def delete_draft(conn, draft_id):
    conn.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    conn.commit()


def search_citable(conn, query="", limit=30):
    """Refs + works for the citation picker."""
    q = (query or "").strip().lower()
    out = []
    for r in conn.execute(
        "SELECT r.id, r.work_id, r.authors, r.year, r.title_norm, r.doi, r.raw, "
        "w.title AS source_title FROM refs r LEFT JOIN works w ON w.id=r.work_id "
        "ORDER BY r.work_id, r.id").fetchall():
        hay = " ".join(str(r[k] or "") for k in
                       ("authors", "year", "title_norm", "doi", "raw", "source_title")).lower()
        if q and q not in hay:
            continue
        out.append({"kind": "ref", "ref_id": r["id"], "work_id": r["work_id"],
                    "authors": r["authors"] or "", "year": r["year"] or "",
                    "title": r["title_norm"] or "", "doi": r["doi"] or "",
                    "raw": r["raw"] or "", "source": r["source_title"] or ""})
        if len(out) >= limit:
            break
    if len(out) < limit:
        for w in conn.execute("SELECT id, title, verdict FROM works ORDER BY title").fetchall():
            if q and q not in (w["title"] or "").lower():
                continue
            out.append({"kind": "work", "ref_id": None, "work_id": w["id"], "authors": "",
                        "year": "", "title": w["title"] or "", "doi": "", "raw": "",
                        "source": "library"})
            if len(out) >= limit:
                break
    return out
