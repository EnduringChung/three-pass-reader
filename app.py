"""Three-Pass Reader — FastAPI app (Phase 1: read -> capture -> find)."""
import base64
import json
import pathlib
import re

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import backup
import db
import docximport
import export
import refs
import writerdoc
from scanner import scan_roots

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
CAPTURES = DATA / "captures"
DRAFT_IMAGES = DATA / "drafts"
DB_PATH = str(DATA / "reader.db")

DEFAULT_CONFIG = {
    "roots": [],                                          # folders to scan for PDFs
    "import_dir": str(DATA / "imports"),
    "port": 8765,
    "exports_dir": str(ROOT / "exports"),
    "matrix_xlsx": "",                                    # optional: an existing review matrix
}


def load_config():
    """config.json is personal (absolute folders), so a fresh clone starts from defaults and writes one."""
    path = ROOT / "config.json"
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            return {**DEFAULT_CONFIG, **cfg}
        except (OSError, json.JSONDecodeError):
            pass
    path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return dict(DEFAULT_CONFIG)


CONFIG = load_config()

# a fresh clone has no data/ tree yet; sqlite will not create a database inside a missing folder
for _d in (DATA, CAPTURES, DRAFT_IMAGES, pathlib.Path(CONFIG.get("exports_dir") or (ROOT / "exports"))):
    pathlib.Path(_d).mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Three-Pass Reader")
db.init(DB_PATH)


@app.middleware("http")
async def no_static_cache(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path in ("/", "/reader", "/search"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def conn():
    return db.get_conn()


def q(sql, *args):
    return conn().execute(sql, args).fetchall()


@app.get("/api/papers")
def papers():
    rows = q(
        "SELECT w.id, w.slug, w.title, w.verdict, w.starred, w.pass1_done, w.pass2_done, "
        "w.pass3_done, f.id AS file_id, f.path, f.is_active "
        "FROM works w LEFT JOIN files f ON f.id=w.active_file_id ORDER BY w.title")
    return [dict(r) for r in rows]


class StarBody(BaseModel):
    starred: bool | None = None          # omit to toggle


@app.patch("/api/works/{work_id}/star")
def work_star(work_id: int, body: StarBody):
    row = conn().execute("SELECT starred FROM works WHERE id=?", (work_id,)).fetchone()
    if not row:
        return JSONResponse({"detail": "no such work"}, status_code=404)
    want = (not row["starred"]) if body.starred is None else body.starred
    return {"ok": True, "starred": db.set_star(conn(), work_id, want)}


@app.post("/api/rescan")
def rescan():
    n = scan_roots(conn(), CONFIG["roots"], full=True)
    return {"ok": True, "scan": n}


@app.get("/api/roots")
def get_roots():
    return {"roots": CONFIG["roots"], "import_dir": CONFIG.get("import_dir", str(DATA / "imports"))}


@app.post("/api/roots")
async def add_root(request: Request):
    body = await request.json()
    p = pathlib.Path(body["path"])
    if not p.is_dir():
        raise HTTPException(400, f"not a directory: {p}")
    rp = str(p.resolve())
    if rp not in CONFIG["roots"]:
        CONFIG["roots"].append(rp)
        _save_config()
    n = scan_roots(conn(), [rp])
    return {"ok": True, "roots": CONFIG["roots"], "scan": n}


@app.delete("/api/roots")
async def del_root(request: Request):
    body = await request.json()
    rp = str(pathlib.Path(body["path"]).resolve())
    CONFIG["roots"] = [r for r in CONFIG["roots"] if r != rp]
    _save_config()
    return {"ok": True, "roots": CONFIG["roots"]}


def _save_config():
    (ROOT / "config.json").write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")


@app.post("/api/upload")
async def upload(request: Request):
    """Multipart upload of one or more PDFs -> imports dir -> scanned as works."""
    import_dir = pathlib.Path(CONFIG.get("import_dir", str(DATA / "imports")))
    import_dir.mkdir(parents=True, exist_ok=True)
    form = await request.form()
    saved = []
    for upload_file in form.getlist("files"):
        name = pathlib.Path(upload_file.filename).name
        if not name.lower().endswith(".pdf") or name.startswith("._"):
            continue
        dest = import_dir / name
        i = 1
        while dest.exists():  # never overwrite
            dest = import_dir / f"{pathlib.Path(name).stem} ({i}).pdf"
            i += 1
        dest.write_bytes(await upload_file.read())
        saved.append(dest.name)
    n = scan_roots(conn(), [str(import_dir)]) if saved else (0, 0, 0)
    return {"ok": True, "saved": saved, "scan": n}


@app.get("/pdf/{file_id}")
def pdf(file_id: int):
    row = conn().execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
    if row is None:
        raise HTTPException(404)
    return FileResponse(row["path"], media_type="application/pdf")


@app.get("/api/templates/{pass_name}")
def templates(pass_name: str):
    p = ROOT / "templates" / f"{pass_name}.json"
    if not p.exists():
        raise HTTPException(404)
    return json.loads(p.read_text(encoding="utf-8"))


# ---------- answers ----------

@app.get("/api/works/{work_id}/answers/{pass_name}")
def get_answers(work_id: int, pass_name: str):
    return db.get_answers(conn(), work_id, pass_name)


@app.put("/api/works/{work_id}/answers/{pass_name}")
async def put_answer(work_id: int, pass_name: str, request: Request):
    body = await request.json()
    db.upsert_answer(conn(), work_id, pass_name, body["qkey"], body.get("value", ""))
    return {"ok": True}


# ---------- cards ----------

@app.get("/api/works/{work_id}/cards")
def get_cards(work_id: int, pass_name: str = None):
    return db.get_cards(conn(), work_id, pass_name)


@app.post("/api/works/{work_id}/cards")
async def create_card(work_id: int, request: Request):
    body = await request.json()
    cid = db.create_card(conn(), work_id, body.get("pass", "pass2"),
                         ord_=body.get("ord", 0), status=body.get("status", "seed"))
    return {"id": cid}


@app.patch("/api/cards/{card_id}")
async def patch_card(card_id: int, request: Request):
    body = await request.json()
    db.update_card(conn(), card_id, body)
    row = conn().execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
    return dict(row)


@app.delete("/api/cards/{card_id}")
def del_card(card_id: int):
    db.delete_card(conn(), card_id)
    return {"ok": True}


# ---------- anchors / capture ----------

@app.post("/api/capture")
async def capture(request: Request):
    body = await request.json()  # {file_id, page, rect, data_url}
    work = conn().execute("SELECT work_id FROM files WHERE id=?", (body["file_id"],)).fetchone()
    if work is None:
        raise HTTPException(404)
    slug_row = conn().execute("SELECT slug FROM works WHERE id=?", (work["work_id"],)).fetchone()
    outdir = CAPTURES / slug_row["slug"]
    outdir.mkdir(parents=True, exist_ok=True)
    header, b64 = body["data_url"].split(",", 1)
    n = len(list(outdir.glob("*.png"))) + 1
    rel = f"captures/{slug_row['slug']}/cap-{n:03d}.png"
    (DATA / rel).write_bytes(base64.b64decode(b64))
    aid = db.add_anchor(conn(), body["file_id"], body["page"], json.dumps(body.get("rect")), rel)
    return {"anchor_id": aid, "image_path": rel}


@app.get("/api/anchors/{anchor_id}/image")
def anchor_image(anchor_id: int):
    a = db.get_anchor(conn(), anchor_id)
    if a is None:
        raise HTTPException(404)
    return FileResponse(DATA / a["image_path"], media_type="image/png")


@app.put("/api/cards/{card_id}/anchors")
async def card_add_anchor(card_id: int, request: Request):
    body = await request.json()  # {anchor_id}
    n = conn().execute("SELECT COUNT(*) c FROM card_anchors WHERE card_id=?",
                      (card_id,)).fetchone()["c"]
    db.link_anchor(conn(), card_id, body["anchor_id"], ord_=n)
    return {"ok": True}


# ---------- card links (Zettelkasten) ----------

@app.get("/api/cards/{card_id}/links")
def card_links(card_id: int):
    return db.get_card_links(conn(), card_id)


@app.post("/api/cards/{card_id}/links")
async def add_card_link(card_id: int, request: Request):
    body = await request.json()
    try:
        db.link_cards(conn(), card_id, body["dst_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/cards/{card_id}/links/{dst_id}")
def del_card_link(card_id: int, dst_id: int):
    db.unlink_cards(conn(), card_id, dst_id)
    return {"ok": True}


@app.get("/api/picker")
def picker(q: str):
    return db.search_card_picker(conn(), q)


# ---------- search ----------

@app.get("/api/search")
def search(q: str):
    try:
        return db.search_cards(conn(), q)
    except Exception:
        return []


# ---------- listitems ----------

@app.get("/api/works/{work_id}/items/{pass_name}/{kind}")
def items(work_id: int, pass_name: str, kind: str):
    return db.get_listitems(conn(), work_id, pass_name, kind)


@app.post("/api/works/{work_id}/items/{pass_name}/{kind}")
async def add_item(work_id: int, pass_name: str, kind: str, request: Request):
    body = await request.json()
    n = conn().execute("SELECT COUNT(*) c FROM listitems WHERE work_id=? AND pass=? AND kind=?",
                      (work_id, pass_name, kind)).fetchone()["c"]
    return {"id": db.add_listitem(conn(), work_id, pass_name, kind, body.get("value", ""), ord_=n)}


@app.delete("/api/items/{item_id}")
def del_item(item_id: int):
    db.delete_listitem(conn(), item_id)
    return {"ok": True}


# ---------- highlights ----------

@app.get("/api/files/{file_id}/highlights")
def highlights(file_id: int, page: int | None = None):
    return db.get_highlights(conn(), file_id, page)


@app.post("/api/files/{file_id}/highlights")
async def add_highlight(file_id: int, request: Request):
    body = await request.json()
    rects = body.get("rects") or []
    if not rects or not all(isinstance(r, list) and len(r) == 4 for r in rects):
        return JSONResponse({"detail": "rects must be [[x0,y0,x1,y1], ...]"}, status_code=400)
    color = body.get("color", "claim")
    if color not in db.HL_COLORS:
        return JSONResponse({"detail": f"bad colour: {color}"}, status_code=400)
    hid = db.add_highlight(conn(), file_id, int(body["page"]), color, rects,
                           body.get("text", ""), body.get("note", ""))
    return {"id": hid}


@app.patch("/api/highlights/{hid}")
async def patch_highlight(hid: int, request: Request):
    body = await request.json()
    try:
        db.update_highlight(conn(), hid, color=body.get("color"), note=body.get("note"))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return {"ok": True}


@app.delete("/api/highlights/{hid}")
def del_highlight(hid: int):
    db.delete_highlight(conn(), hid)
    return {"ok": True}



# ---------- verdict / backup / export ----------

@app.patch("/api/works/{work_id}/verdict")
async def set_verdict(work_id: int, request: Request):
    body = await request.json()
    try:
        v = db.set_verdict(conn(), work_id, body.get("verdict", ""))
    except ValueError as e:
        return JSONResponse({"detail": str(e)}, status_code=400)
    return {"ok": True, "verdict": v}


@app.post("/api/backup")
async def make_backup(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    label = (body or {}).get("label", "") or ""
    dest, manifest = backup.snapshot(drafts=DATA / "drafts", label=label)
    return {"path": str(dest), "counts": manifest["counts"],
            "files": len(manifest["files"]), "bytes": manifest["total_bytes"]}


@app.get("/api/backups")
def list_backups():
    return backup.list_snapshots()


@app.post("/api/works/{work_id}/export")
def export_work(work_id: int):
    try:
        res = export.export_work(conn(), work_id, CONFIG["exports_dir"],
                                 CONFIG.get("matrix_xlsx"))
    except LookupError as e:
        return JSONResponse({"detail": str(e)}, status_code=404)
    return res



# ---------- references ----------

class ExtractOpts(BaseModel):
    resolve: bool = True
    query: bool = True


class ManualBody(BaseModel):
    text: str = ""
    dois: str = ""
    resolve: bool = True


class BatchBody(BaseModel):
    work_ids: list[int] = []
    resolve: bool = True
    query: bool = False

def _file_for(work_id):
    row = conn().execute(
        "SELECT f.id, f.path, f.text_layer FROM files f JOIN works w ON f.id=w.active_file_id "
        "WHERE w.id=?", (work_id,)).fetchone()
    if row is None:
        row = conn().execute(
            "SELECT id, path, text_layer FROM files WHERE work_id=? ORDER BY id DESC LIMIT 1",
            (work_id,)).fetchone()
    return row


@app.get("/api/works/{work_id}/refs")
def list_refs(work_id: int):
    f = _file_for(work_id)
    status = (f["text_layer"] if f else "") or ""
    if f and not status:
        # first look at this file: decide from the PDF itself so a scan is flagged before
        # the user presses a button that can only fail
        status = refs.text_layer_status(f["path"])
        db.set_text_layer(conn(), f["id"], status)
    return {"text_layer": status or "unknown", "refs": db.get_refs(conn(), work_id)}


@app.post("/api/works/{work_id}/extract-refs")
def extract_refs(work_id: int, body: ExtractOpts | None = None):
    body = body or ExtractOpts()
    f = _file_for(work_id)
    if not f:
        return JSONResponse({"detail": "no file for this work"}, status_code=404)
    out = refs.extract_refs(conn(), work_id, f["path"], resolve=body.resolve, query=body.query)
    db.set_text_layer(conn(), f["id"], out.get("text_layer", ""))
    if out.get("own_doi"):
        db.set_work_doi(conn(), work_id, out["own_doi"])
    return out


@app.patch("/api/refs/{ref_id}")
async def patch_ref(ref_id: int, request: Request):
    body = await request.json()
    return {"ok": db.update_ref(conn(), ref_id, body)}


@app.delete("/api/refs/{ref_id}")
def del_ref(ref_id: int):
    db.delete_ref(conn(), ref_id)
    return {"ok": True}


@app.post("/api/works/{work_id}/refs/manual")
def refs_manual(work_id: int, body: ManualBody):
    if body.dois:
        out = refs.manual_add_dois(conn(), work_id, body.dois, resolve=body.resolve)
    else:
        out = refs.manual_add_lines(conn(), work_id, body.text)
    out["refs"] = db.get_refs(conn(), work_id)
    return out


@app.get("/api/refs/common")
def refs_common(min_works: int = 2):
    return db.refs_common(conn(), min_works=min_works)


@app.post("/api/batch/extract-refs")
def batch_extract(body: BatchBody):
    ids = [int(x) for x in body.work_ids]
    results = []
    for wid in ids:
        f = _file_for(wid)
        if not f:
            results.append({"work_id": wid, "status": "failed", "error": "no file"})
            continue
        try:
            out = refs.extract_refs(conn(), wid, f["path"], resolve=body.resolve,
                                    query=body.query)
            db.set_text_layer(conn(), f["id"], out.get("text_layer", ""))
            if out.get("own_doi"):
                db.set_work_doi(conn(), wid, out["own_doi"])
            results.append({"work_id": wid, **out})
        except Exception as e:                       # never report a crash as "0 entries"
            results.append({"work_id": wid, "status": "failed",
                            "error": f"{type(e).__name__}: {e}"})
    return {"results": results}



# ---------- writer ----------

class DraftBody(BaseModel):
    title: str | None = None
    html: str | None = None
    work_id: int | None = None


def _plain_to_html(text):
    """Legacy scratchpad text → editor HTML, keeping indentation so nothing looks rewritten."""
    import html as _html
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        stripped = line.lstrip(" \t")
        depth = len(line) - len(stripped)
        pad = "&nbsp;" * (depth * 2)
        out.append(f"<p>{pad}{_html.escape(stripped)}</p>")
    return "".join(out)


@app.get("/api/works/{work_id}/scratch")
def work_scratch(work_id: int):
    """Get (creating on first use) the scratch pad for this paper, as a rich-text draft.

    The old scratchpad was a plain textarea stored in `answers`; the first time it is opened its
    text is carried over, so nothing written here before the switch is lost.
    """
    work = db.get_work(conn(), work_id)
    if not work:
        return JSONResponse({"detail": "no such work"}, status_code=404)
    row = db.get_scratch_draft(conn(), work_id)
    if row:
        return dict(row)
    did = db.create_draft(conn(), f"Scratch — {(work['title'] or '')[:60]}", work_id=work_id,
                          kind="scratch")
    legacy = conn().execute("SELECT value FROM answers WHERE work_id=? AND pass='scratch' "
                            "AND qkey='scratchpad'", (work_id,)).fetchone()
    if legacy and (legacy["value"] or "").strip():
        safe = writerdoc.sanitize(_plain_to_html(legacy["value"]))
        plain = writerdoc.to_plain(safe)
        db.update_draft(conn(), did, html_text=safe, plain=plain)
        if plain.strip():          # only after the copy is safely in the draft
            conn().execute("UPDATE answers SET value='' WHERE work_id=? AND pass='scratch' "
                           "AND qkey='scratchpad'", (work_id,))
            conn().commit()
    return db.get_draft(conn(), did)


@app.get("/api/drafts")
def drafts_list(work_id: int | None = None):
    return db.list_drafts(conn(), work_id=work_id)


@app.post("/api/drafts")
def draft_create(body: DraftBody):
    did = db.create_draft(conn(), body.title or "Untitled", body.work_id)
    if body.html:
        safe = writerdoc.sanitize(body.html)
        db.update_draft(conn(), did, html_text=safe, plain=writerdoc.to_plain(safe))
    return db.get_draft(conn(), did)


MAX_DOCX_BYTES = 25 * 1024 * 1024


@app.post("/api/drafts/import")
async def draft_import(file: UploadFile = File(...)):
    """Import a .docx as a new draft. Images are copied into the draft's own folder."""
    name = (file.filename or "document.docx")
    if not name.lower().endswith(".docx"):
        return JSONResponse({"detail": "only .docx can be imported (save old .doc as .docx first)"},
                            status_code=415)
    data = await file.read()
    if len(data) > MAX_DOCX_BYTES:
        return JSONResponse({"detail": "document larger than 25 MB"}, status_code=413)
    tmp = DATA / "imports"
    tmp.mkdir(parents=True, exist_ok=True)
    src = tmp / f"upload-{pathlib.Path(name).name}"
    src.write_bytes(data)

    did = db.create_draft(conn(), pathlib.Path(name).stem, source_name=pathlib.Path(name).name)
    folder = DRAFT_IMAGES / str(did)
    folder.mkdir(parents=True, exist_ok=True)
    counter = {"n": 0}

    def save_image(ext, blob):
        counter["n"] += 1
        fname = f"img-{counter['n']:03d}.{ext}"
        (folder / fname).write_bytes(blob)
        return f"/drafts/{did}/{fname}"

    try:
        raw_html, title, stats = docximport.docx_to_html(src, save_image)
    except Exception as e:                                   # a corrupt file must not 500 silently
        db.delete_draft(conn(), did)
        return JSONResponse({"detail": f"could not read that .docx: {e}"}, status_code=400)
    finally:
        try:
            src.unlink()
        except OSError:
            pass

    safe = writerdoc.sanitize(raw_html)
    db.update_draft(conn(), did, html_text=safe, plain=writerdoc.to_plain(safe),
                    title=(title or pathlib.Path(name).stem)[:200])
    out = db.get_draft(conn(), did)
    out["stats"] = stats
    return out


@app.get("/api/drafts/{draft_id}")
def draft_get(draft_id: int):
    d = db.get_draft(conn(), draft_id)
    if not d:
        return JSONResponse({"detail": "not found"}, status_code=404)
    return d


@app.put("/api/drafts/{draft_id}")
def draft_save(draft_id: int, body: DraftBody):
    if not db.get_draft(conn(), draft_id):
        return JSONResponse({"detail": "not found"}, status_code=404)
    safe = None
    plain = None
    if body.html is not None:
        safe = writerdoc.sanitize(body.html)          # never store what the browser handed us
        plain = writerdoc.to_plain(safe)
    db.update_draft(conn(), draft_id, html_text=safe, plain=plain, title=body.title)
    return {"ok": True, "words": writerdoc.word_count(plain) if plain is not None else None}


@app.delete("/api/drafts/{draft_id}")
def draft_delete(draft_id: int):
    """Remove the document from the writer. Never touches files outside data/drafts/<id>/ —
    an imported .docx stays exactly where it was, and is not even referenced by path."""
    import shutil
    folder = DRAFT_IMAGES / str(draft_id)
    if folder.is_dir() and folder.parent.resolve() == DRAFT_IMAGES.resolve():
        shutil.rmtree(folder, ignore_errors=True)      # our own imported image copies only
    db.delete_draft(conn(), draft_id)
    return {"ok": True, "files_deleted": "own image copies only"}


IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
               "image/gif": ".gif"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@app.post("/api/drafts/{draft_id}/images")
async def draft_image(draft_id: int, request: Request):
    if not db.get_draft(conn(), draft_id):
        return JSONResponse({"detail": "no such draft"}, status_code=404)
    form = await request.form()
    up = None
    for _, v in form.multi_items():
        if getattr(v, "filename", None):
            up = v
            break
    if up is None:
        return JSONResponse({"detail": "no file"}, status_code=400)
    ext = IMAGE_TYPES.get((up.content_type or "").lower())
    if not ext:
        return JSONResponse({"detail": f"unsupported type: {up.content_type}"}, status_code=415)
    data = await up.read()
    if len(data) > MAX_IMAGE_BYTES:
        return JSONResponse({"detail": "image larger than 8 MB"}, status_code=413)
    folder = DRAFT_IMAGES / str(draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    n = len(list(folder.glob("img-*"))) + 1              # never overwrites an existing image
    name = f"img-{n:03d}{ext}"
    (folder / name).write_bytes(data)
    return {"url": f"/drafts/{draft_id}/{name}", "bytes": len(data)}


@app.get("/drafts/{draft_id}/{name}")
def draft_image_get(draft_id: int, name: str):
    """Serve only files inside this draft's own image folder."""
    if not re.fullmatch(r"img-\d{1,4}\.(png|jpg|jpeg|webp|gif)", name):
        raise HTTPException(status_code=404)
    path = (DRAFT_IMAGES / str(draft_id) / name).resolve()
    if not str(path).startswith(str(DRAFT_IMAGES.resolve())) or not path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.get("/api/citations")
def citations(q: str = "", limit: int = 30):
    return db.search_citable(conn(), q, limit)


@app.get("/write")
def write_page():
    return FileResponse(ROOT / "static" / "write.html")



@app.get("/api/refs")
def refs_by_ids(ids: str = ""):
    """Rows for the citation picker / bibliography, in the order asked for."""
    out = []
    for raw in [x for x in ids.split(",") if x.strip().isdigit()][:200]:
        r = conn().execute("SELECT r.*, w.title AS source_title FROM refs r "
                           "LEFT JOIN works w ON w.id=r.work_id WHERE r.id=?",
                           (int(raw),)).fetchone()
        if r:
            out.append(dict(r))
    return out


@app.post("/api/drafts/{draft_id}/export")
def draft_export(draft_id: int):
    d = db.get_draft(conn(), draft_id)
    if not d:
        return JSONResponse({"detail": "not found"}, status_code=404)
    out_dir = pathlib.Path(CONFIG["exports_dir"]) / "writing"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (d["title"] or "draft").lower()).strip("-")[:60] or f"draft-{draft_id}"
    md = f"# {d['title']}\n\n" + writerdoc.to_markdown(d["html"])
    path = out_dir / f"{slug}.md"
    path.write_text(md, encoding="utf-8")
    return {"markdown": str(path), "words": writerdoc.word_count(d["plain"])}

# ---------- static ----------

@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/reader")
def reader():
    return FileResponse(ROOT / "static" / "reader.html")


@app.get("/search")
def search_page():
    return FileResponse(ROOT / "static" / "search.html")


@app.get("/refs")
def refs_page():
    return FileResponse(ROOT / "static" / "refs.html")


static = StaticFiles(directory=str(ROOT / "static"))
app.mount("/static", static)
