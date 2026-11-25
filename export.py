"""Export a work: markdown notes + a row in a COPY of the literature matrix.

Rules this module obeys:
  * It never mutates the original matrix workbook — it copies the file to
    exports/ first and appends there.
  * It only writes what the app actually knows. Cells it has nothing for are
    left untouched (blank), never filled with a guess or a summary.
  * Files are written under the configured exports dir; the archive is never
    touched.
"""
import datetime
import json
import pathlib
import re
import shutil

ROOT = pathlib.Path(__file__).parent
TEMPLATES = ROOT / "templates"


def _labels(pass_name):
    """qkey -> label, in template order."""
    p = TEMPLATES / f"{pass_name}.json"
    if not p.exists():
        return {}
    tpl = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for f in (tpl.get("fields") or []) + (tpl.get("more_fields") or []):
        out[f["qkey"]] = f.get("label") or f["qkey"]
    return out


def _answers(conn, work_id, pass_name):
    rows = conn.execute(
        "SELECT qkey, value FROM answers WHERE work_id=? AND pass=?", (work_id, pass_name)
    ).fetchall()
    labels = _labels(pass_name)
    return [(labels.get(r["qkey"], r["qkey"]), r["value"])
            for r in rows if (r["value"] or "").strip()]


def _cards(conn, work_id):
    rows = conn.execute(
        "SELECT * FROM cards WHERE work_id=? ORDER BY pass, ord, id", (work_id,)).fetchall()
    cards = []
    for r in rows:
        card = dict(r)
        card["anchors"] = [dict(a) for a in conn.execute(
            "SELECT a.* FROM anchors a JOIN card_anchors ca ON ca.anchor_id=a.id "
            "WHERE ca.card_id=? ORDER BY ca.ord", (r["id"],)).fetchall()]
        cards.append(card)
    return cards


def _lists(conn, work_id):
    rows = conn.execute(
        "SELECT pass, kind, value, done FROM listitems WHERE work_id=? ORDER BY pass, kind, ord, id",
        (work_id,)).fetchall()
    grouped = {}
    for r in rows:
        grouped.setdefault((r["pass"], r["kind"]), []).append(r)
    return grouped


def work_markdown(conn, work_id, assets_rel="assets"):
    """Markdown for one work. Empty fields are omitted, not printed as blanks."""
    w = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    if not w:
        raise LookupError(f"work {work_id} not found")
    slug = w["slug"]
    lines = [f"# {w['title']}", ""]
    meta = [f"`{slug}`", f"work id {work_id}"]
    if w["verdict"]:
        meta.append(f"**verdict: {w['verdict']}**")
    meta.append("exported " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines += [" · ".join(meta), ""]

    for pass_name, heading in (("pass1", "Pass 1 — screening"),
                               ("pass2", "Pass 2 — figures & evidence"),
                               ("pass3", "Pass 3 — critique & synthesis")):
        ans = _answers(conn, work_id, pass_name)
        if not ans:
            continue
        lines += [f"## {heading}", ""]
        for label, value in ans:
            lines.append(f"**{label}:** {value.strip()}")
            lines.append("")

    cards = _cards(conn, work_id)
    if cards:
        lines += ["## Evidence cards", ""]
        for c in cards:
            lines.append(f"### Card {c['id']} — {c['pass']} · {c['status']}")
            if (c["observation"] or "").strip():
                lines += ["", f"**Observation:** {c['observation'].strip()}"]
            if (c["interpretation"] or "").strip():
                lines += ["", f"**Interpretation:** {c['interpretation'].strip()}"]
            if (c["question"] or "").strip():
                lines += ["", f"**Question:** {c['question'].strip()}"]
            if (c["resolution"] or "").strip():
                lines += ["", f"**Resolution:** {c['resolution'].strip()}"]
            try:
                audit = json.loads(c["audit"] or "{}")
            except json.JSONDecodeError:
                audit = {}
            audit = {k: v for k, v in audit.items() if v not in ("", None, [], {})}
            if audit:
                lines += ["", "**Audit:** " + ", ".join(f"{k}={v}" for k, v in audit.items())]
            for a in c["anchors"]:
                if a.get("image_path"):
                    name = pathlib.Path(a["image_path"]).name
                    lines += ["", f"![p.{a['page']}]({assets_rel}/{c['id']}-{name})"]
                else:
                    lines += ["", f"*(anchor: page {a['page']})*"]
            lines.append("")

    grouped = _lists(conn, work_id)
    if grouped:
        lines += ["## Lists", ""]
        for (pass_name, kind), items in grouped.items():
            lines += [f"### {pass_name} · {kind.replace('_', ' ')}", ""]
            for it in items:
                mark = "x" if it["done"] else " "
                lines.append(f"- [{mark}] {it['value']}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:80] or "work"


def export_work(conn, work_id, exports_dir, matrix_xlsx=None, data_dir=None):
    """Write markdown (+ copied capture images) and append a matrix row to a dated copy."""
    exports_dir = pathlib.Path(exports_dir)
    data_dir = pathlib.Path(data_dir) if data_dir else (ROOT / "data")
    notes_dir = exports_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    w = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    if not w:
        raise LookupError(f"work {work_id} not found")
    slug = w["slug"] or _slugify(w["title"])
    assets_name = f"{slug}_assets"
    assets_dir = notes_dir / assets_name
    if assets_dir.exists():
        shutil.rmtree(assets_dir)

    cards = _cards(conn, work_id)
    copied = 0
    for c in cards:
        for a in c["anchors"]:
            if not a.get("image_path"):
                continue
            src = data_dir / a["image_path"]
            if not src.exists():
                continue
            assets_dir.mkdir(exist_ok=True)
            shutil.copy2(src, assets_dir / f"{c['id']}-{src.name}")
            copied += 1

    md_path = notes_dir / f"{slug}.md"
    md_path.write_text(work_markdown(conn, work_id, assets_rel=assets_name), encoding="utf-8")
    result = {"markdown": str(md_path), "images": copied, "xlsx": None}

    if matrix_xlsx:
        try:
            result["xlsx"] = append_matrix_row(conn, work_id, exports_dir, matrix_xlsx, md_path, exports_dir)
        except Exception as e:                    # a missing/odd workbook must not lose the markdown
            result["xlsx_error"] = f"{type(e).__name__}: {e}"
    return result


def matrix_values(conn, work_id, md_path, exports_dir):
    """Only fields the app genuinely holds. Everything else stays blank."""
    w = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    cat = conn.execute(
        "SELECT value FROM answers WHERE work_id=? AND pass='pass1' AND qkey='category'",
        (work_id,)).fetchone()
    note_bits = []
    if w["verdict"]:
        note_bits.append(f"verdict: {w['verdict']}")
    try:
        rel = pathlib.Path(md_path).relative_to(pathlib.Path(exports_dir).parent)
    except ValueError:
        rel = pathlib.Path(md_path)
    note_bits.append(f"notes: {rel.as_posix()}")
    return {
        "Paper Type": (cat["value"].strip() if cat and cat["value"] else ""),
        "Title": w["title"],
        "Notes": " · ".join(note_bits),
    }


def append_matrix_row(conn, work_id, exports_dir, matrix_xlsx, md_path, exports_dir_root=None):
    """Append to a dated COPY of his workbook; the original is only ever read."""
    import openpyxl

    exports_dir = pathlib.Path(exports_dir)
    matrix_xlsx = pathlib.Path(matrix_xlsx)
    if not matrix_xlsx.exists():
        raise FileNotFoundError(f"matrix workbook not found: {matrix_xlsx}")
    dated = exports_dir / f"matrix_{datetime.date.today().isoformat()}.xlsx"
    if not dated.exists():
        shutil.copy2(matrix_xlsx, dated)          # original is never opened for writing

    wb = openpyxl.load_workbook(dated)
    sheet_name = "All papers" if "All papers" in wb.sheetnames else wb.sheetnames[0]
    ws = wb[sheet_name]
    header = [(c.value or "").strip() if isinstance(c.value, str) else c.value
              for c in ws[1]]
    values = matrix_values(conn, work_id, md_path, exports_dir_root or exports_dir)

    title_col = header.index("Title") + 1 if "Title" in header else None
    if title_col is None:
        raise ValueError(f"no 'Title' column in sheet {sheet_name}")

    # first row whose title cell is empty, else one past the last used row
    row = ws.max_row + 1
    for r in range(2, ws.max_row + 2):
        cell = ws.cell(row=r, column=title_col).value
        if not (cell or "").strip():
            row = r
            break

    for key, val in values.items():
        if key in header and val:
            ws.cell(row=row, column=header.index(key) + 1, value=val)
    wb.save(dated)                                # same path: load_workbook read it already
    return str(dated)
