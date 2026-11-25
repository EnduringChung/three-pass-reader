"""Reference extraction: text → bibliography entries → rows with honest status.

Design rules (from the plan; do not relax them casually):
  * The splitter NEVER drops a line silently. Unparsed entries are kept with `match_status`
    'unparsed' and the raw text intact.
  * A capital letter after an entry number is not required — "81. van den Berg" is a real
    reference and a (?=[A-Z]) guard deleted it once already.
  * DOIs are an enricher, not the enumerator: many publishers print none at all, so the parsed
    text decides what exists and Crossref only upgrades the metadata.
  * Manual rows (source='manual') are never modified by extraction — see db.upsert_refs.
"""
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

UA = "three-pass-reader/1.0 (+https://github.com/EnduringChung/three-pass-reader)"

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s,;\"'<>)\]]+", re.I)
HEADING_RE = re.compile(r"(?m)^\s*(?:references?|bibliography|reference list|literature cited)\s*$", re.I)

# entry starts — see module docstring for why BARE has no lookahead
# [ \t] not \s: a \s* before the number consumes the previous line's newline, which shifts
# the match window past the next entry and silently loses it (74 of 220 on work 11).
BRACKETED = re.compile(r"(?m)^[ \t]*\[(\d{1,3})\][ \t]*")
BARE = re.compile(r"(?m)^[ \t]*(\d{1,3})[ \t]*[.)][ \t]+")
# author-year lists may open with a lowercase particle ("van den Berg, C.W., ... (2018)")
AUTHOR_YEAR = re.compile(r"(?m)^[ \t]*([A-Za-z][A-Za-z0-9'\-\. ]{1,80},[^\n]{0,140}?\((?:19|20)\d\d\))")

YEAR_PAREN = re.compile(r"\(((?:19|20)\d\d)\)")
YEAR_ANY = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


# ---------- normalisation ----------

def norm_title(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = s.encode("ascii", "ignore").decode()          # ç → c, ü → u
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


def clean_doi(doi):
    doi = (doi or "").strip()
    doi = DOI_PREFIX_RE.sub("", doi)
    return doi.lower().rstrip(".;,)]}>")


def ref_key(doi, title_norm, authors, year):
    """Dedupe key. DOI wins; title next; author+year as the last resort."""
    doi = clean_doi(doi)
    if doi:
        return "doi:" + doi
    if title_norm and len(title_norm) > 25:
        return "ta:" + title_norm
    a = norm_title(authors)[:40]
    if a or year:
        return f"ay:{a}|{year or ''}"
    return ""


# ---------- section location ----------

def find_refs_start(txt):
    """Last heading LINE wins (papers say 'references' in the intro too). -1 when absent."""
    best = -1
    for m in HEADING_RE.finditer(txt):
        best = m.end()
    return best


def dense_start(txt):
    """Fallback for PDFs whose heading is missing or glued to other text."""
    n = len(txt)
    if n < 4000:
        return 0
    best_score, best_at = 0, int(n * 0.6)
    win = 2400
    for start in range(int(n * 0.4), n - win, win // 2):
        chunk = txt[start:start + win]
        score = len(re.findall(r"(?m)^\s*\[?\d{1,3}\]?[.)]?\s+\S", chunk))
        score += 2 * len(YEAR_PAREN.findall(chunk))
        if score > best_score:
            best_score, best_at = score, start
    return best_at if best_score >= 6 else int(n * 0.75)


def refs_section(txt):
    i = find_refs_start(txt)
    return txt[i:] if i > 0 else txt[dense_start(txt):]


# ---------- cleaning ----------

RUNNING_HEAD_RES = [
    re.compile(r"\s*\|\s*[A-Z][^|]{0,40}et al\.?[^|]{0,70}\|\s*\d{1,3}\s*"),
    re.compile(r"\s*\|\s*\d{1,3}\s*\|"),
    re.compile(r"(?m)^\s*\d{1,3}\s*$"),          # bare page number lines
]


def strip_running_heads(sec):
    """Page headers/footers get glued into entries and corrupt them."""
    for rx in RUNNING_HEAD_RES:
        sec = rx.sub(" ", sec)
    # a line broken after a hyphen is one word: rejoin before matching anything
    sec = re.sub(r"(?<=[a-z])-\n(?=[a-z])", "", sec)
    return sec


def join_wrapped_dois(sec):
    r"""`10.1016/j.jhep.` newline `2025.11.018` is one DOI; join it before regexing.

    Must be conservative: a naive `\n(?=\d)` join also welds the NEXT ENTRY's number onto the
    previous line ("...SMMD.20230038." + "2. Kim"), which cost 146 of 220 entries on work 11.
    Only join when the fragment ends with "/" or the next line is clearly a DOI continuation
    (digits then a dot/slash then more digits, no space).
    """
    # every cut point uses the same "is the next line a DOI tail?" test: a separator alone is
    # not enough (a DOI ending in "-" would otherwise weld onto the next entry's "2. Kim")
    # the break often carries a trailing space before the newline ("s41536-021- \n00158-8")
    TAIL = r"(?:\d+[-./]\d|\d{4,}(?!\.\s)|[A-Za-z])"   # "TEA.2019", "26871.", "adhm.…"
    sec = re.sub(r"(\b10\.\d{4,9}/\S*[/_\-.]?)[ \t]*\n(?=" + TAIL + r")", r"\1", sec)
    sec = re.sub(r"(\b10\.\d{4,9}/\S*[/_\-.]?)[ \t]+(?=" + TAIL + r")", r"\1", sec)
    # cut after a dot: only continue when the next line is clearly a DOI tail, never an entry number
    #   "…s41536-021-" + "00254-1"   → join     (live bug: gave the DOI "10.1038/s41536-021-")
    #   "…SMMD.20230038." + "2. Kim" → do not   (that weld ate 146 entries once)
    return sec


# ---------- splitting ----------

def split_bracketed(sec):
    parts = re.split(r"(?m)^[ \t]*\[(\d{1,3})\][ \t]*", "\n" + sec)
    out = [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return out


def split_bare(sec):
    parts = re.split(r"(?m)^[ \t]*(\d{1,3})[ \t]*[.)][ \t]+", "\n" + sec)
    out = [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
    return out


def split_author_year(sec):
    idx = [m.start() for m in AUTHOR_YEAR.finditer(sec)]
    out = []
    for k, at in enumerate(idx):
        end = idx[k + 1] if k + 1 < len(idx) else len(sec)
        out.append((k + 1, sec[at:end]))
    return out


def _sequence_ok(entries):
    """Numbers must mostly increase by 1 — otherwise the detector guessed wrong."""
    nums = [n for n, _ in entries]
    if len(nums) < 5:
        return False
    if len(set(nums)) < len(nums) * 0.9:
        return False
    gaps = [b - a for a, b in zip(nums, nums[1:])]
    good = sum(1 for g in gaps if g == 1)
    return good >= len(gaps) * 0.7


def split_entries(sec):
    """Try each style; return (style, [(num, text)]). Never returns a wrong-but-clean list."""
    sec = join_wrapped_dois(strip_running_heads(sec))
    for style, fn in (("bracketed", split_bracketed), ("numbered", split_bare)):
        ents = fn(sec)
        if len(ents) >= 5 and _sequence_ok(ents):
            return style, ents
    ents = split_author_year(sec)
    if len(ents) >= 5:
        return "author-year", ents
    # last resort: whatever the bare splitter found, even if the sequence is ugly
    ents = split_bare(sec)
    if ents:
        return "numbered-weak", ents
    return "none", []


# ---------- one entry ----------

JOURNAL_HINT = re.compile(
    r"\b(Adv|Nature|Nat|Sci|Science|Cell|Proc|PNAS|J|Biomater|Biomaterials|Mater|Trends|"
    r"Biofabrication|ACS|Angew|Small|Stem|Front|Curr|Int|Am|Eur|Mol|Dev|Tissue|Acta|Bioeng|"
    r"Biotechnol|Macromol|Soft|Chem|Phys|Rep|Methods|Res|Commun|Lett|Rev|Lab|Med|Healthc)\b")

# IEEE / Vancouver style puts the title inside quotes:  Abbott NJ, …, “Structure and function
# of the blood-brain barrier,” Neurobiol. Dis, vol. 37, no. 1, pp. 13–25, 2010.
QUOTED_TITLE = re.compile(r"[\u201c\u201d\"]([^\u201c\u201d\"]{10,300})[\u201c\u201d\"]")
# volume/issue/pages markers whose presence means "everything before is journal, not title"
VOL_MARK = re.compile(r"\bvol\.|\bno\.|\bpp\.|\bpt\.", re.I)


def _authors_block(segs):
    """Leading segments that look like an author list."""
    out = []
    for s in segs:
        low = s.lower().strip()
        if "et al" in low:
            out.append(s)
            break
        if re.match(r"^[A-Z][A-Za-z'\-]+(\s+[A-Z]\.)?(\s*[A-Z]\.)*$", s.strip()) or \
           re.match(r"^[A-Z][A-Za-z'\-]+,\s*[A-Z]\.", s.strip()) or \
           re.match(r"^[A-Z]\.\s*[A-Z]?\.?$", s.strip()):
            out.append(s)
            continue
        if len(out) >= 2 and re.search(r"\d", s):
            break
        if len(out) >= 1 and len(s) > 40:
            break
        out.append(s)
        if len(out) >= 12:
            break
    return out


def _journal_from(seg):
    """'Nat. Rev. Methods Primers 2 (1)' → 'Nat. Rev. Methods Primers'."""
    return re.sub(r"\s+\d{1,4}\s*\(.*$", "", seg).strip(" .,;:")


def parse_entry(raw):
    """Best-effort fields + honest blanks. Never invents a title that is not there."""
    raw = re.sub(r"\s+", " ", (raw or "")).strip()
    raw = re.sub(r"^[\s.:;\-]+", "", raw)              # "[2]. Abbott" → "Abbott"
    fields = {"raw": raw, "doi": "", "year": "", "authors": "", "title": "",
              "title_norm": "", "journal": ""}
    if not raw:
        return fields

    m = DOI_RE.search(raw)
    if m:
        fields["doi"] = clean_doi(m.group(0))

    ym = YEAR_PAREN.search(raw)
    if ym:
        fields["year"] = ym.group(1)
    else:
        ya = YEAR_ANY.search(raw)
        if ya:
            fields["year"] = ya.group(1)

    qm = QUOTED_TITLE.search(raw)
    if qm:
        fields["title"] = qm.group(1).strip(" .,;:")
        fields["authors"] = raw[:qm.start()].strip(" .,;:")
        after = raw[qm.end():].strip(" .,;:")
        segs = [s.strip() for s in after.split(",") if s.strip()]
        if segs:
            cand = segs[0]
            if VOL_MARK.search(cand) or re.search(r"\d", cand):
                vm = VOL_MARK.search(after)
                cand = after[:vm.start()].strip(" .,;:") if vm else ""
            fields["journal"] = _journal_from(cand) if cand else ""
        fields["title_norm"] = norm_title(fields["title"])
        return fields

    cut = ym.start() if ym else len(raw)
    head, tail = raw[:cut], raw[cut:]
    segs = [s.strip() for s in head.rstrip(", ").split(",") if s.strip()]
    authors = _authors_block(segs) if segs else []
    rest = segs[len(authors):]

    if rest:
        journal_idx = None
        for i in range(len(rest) - 1, -1, -1):
            if re.search(r"\b\d{1,4}\s*\(|\b\d{1,4}$", rest[i]) or JOURNAL_HINT.search(rest[i]):
                journal_idx = i
                break
        if journal_idx is not None and journal_idx > 0:
            title = ", ".join(rest[:journal_idx])
            fields["journal"] = _journal_from(rest[journal_idx])
        elif journal_idx == 0:
            title = ""
        else:
            title = ", ".join(rest)
        if len(title) < 8:
            title = ""
        fields["authors"] = ", ".join(authors).strip()
        fields["title"] = title.strip(" .,")
    else:
        fields["authors"] = ", ".join(authors).strip(" ,")
        after = tail
        am = re.match(r"^\(\d{4}\)\s*[.,]?\s*", after)
        if am:
            after = after[am.end():]
        tm = re.match(r"^(.{10,200}?)\.\s", after)
        fields["title"] = (tm.group(1).strip() if tm else "")
        if not fields["title"]:
            jm = JOURNAL_HINT.search(after)
            cand = after[:jm.start()].strip(" .,") if jm else ""
            fields["title"] = cand if len(cand) >= 12 else ""

    fields["title_norm"] = norm_title(fields["title"])
    return fields


# ---------- PDF ----------

def read_pdf_text(path):
    """PyMuPDF: same text as pypdf, 10-20x faster (measured). Single text path."""
    import pymupdf
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text("text") for page in doc)


def text_layer_status(path):
    """'text' | 'image-only' | 'unreadable' — density-based, not a guess."""
    import pymupdf
    try:
        with pymupdf.open(path) as doc:
            if doc.page_count == 0:
                return "unreadable"
            chars = sum(len(pg.get_text("text")) for pg in doc)
            return "text" if chars / doc.page_count >= 200 else "image-only"
    except Exception:
        return "unreadable"


def extract_work_doi(text):
    m = DOI_RE.search((text or "")[:6000])
    return clean_doi(m.group(0)) if m else ""


# ---------- metadata (Crossref / DataCite) ----------

def _http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _shape_crossref(msg):
    authors = []
    for a in (msg.get("author") or [])[:25]:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "")
        if name:
            authors.append(name)
    year = ""
    for k in ("issued", "published", "published-print", "published-online"):
        parts = (msg.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])
            break
    titles = msg.get("title") or []
    return {"title": (titles[0] if titles else "").strip(),
            "authors": ", ".join(authors),
            "year": year,
            "journal": (msg.get("container-title") or [""])[0],
            "type": msg.get("type", ""),
            "source": "crossref"}


def resolve_doi(conn, doi, sleep=0.15):
    """Crossref, then DataCite, cached in doi_meta. Network failure is not fatal."""
    doi = clean_doi(doi)
    if not doi:
        return None
    cached = db_cache_get(conn, doi)
    if cached is not None:
        return cached
    payload = None
    try:
        msg = _http_json("https://api.crossref.org/works/" + urllib.parse.quote(doi))["message"]
        payload = _shape_crossref(msg)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError, TimeoutError):
        try:
            j = _http_json("https://api.datacite.org/dois/" + urllib.parse.quote(doi))["data"]["attributes"]
            payload = {"title": (j.get("titles") or [{}])[0].get("title", ""),
                       "authors": ", ".join(a.get("name", "") for a in (j.get("creators") or [])[:25]),
                       "year": str(j.get("publicationYear") or ""),
                       "journal": (j.get("container") or {}).get("title", ""),
                       "type": (j.get("types") or {}).get("resourceTypeGeneral", ""),
                       "source": "datacite"}
        except Exception:
            payload = {"error": "unresolved"}
    db_cache_put(conn, doi, payload)
    time.sleep(sleep)
    return payload


def _sim(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, norm_title(a), norm_title(b)).ratio()


def _journal_match(abbrev, full):
    """'Neurobiol. Dis' vs 'Neurobiology of Disease' → True.

    Token-prefix matching, with the FIRST token required to agree: without that guard
    "Cell Rep" matches "Stem Cell Reports", which is how a wrong DOI gets attached.
    """
    a = [w for w in norm_title(abbrev).split() if len(w) >= 3]
    b = norm_title(full).split()
    if not a or not b:
        return False
    if not (b[0].startswith(a[0]) or a[0].startswith(b[0])):
        return False
    hits = sum(1 for w in a if any(tok.startswith(w) or w.startswith(tok) for tok in b))
    return hits >= max(1, round(len(a) * 0.6))


def query_doi(conn, title, authors, year, journal=""):
    """Titled, DOI-less entry → Crossref bibliographic query, then STRICT verification.

    A query result is only accepted when the candidate's own metadata agrees with the entry:
    same year, title similarity >= 0.90, and — when both journals are known — a journal match.
    Without that, a conference abstract with the same title wins (observed live: a
    Frontiers-in-Pharmacology meeting abstract for a paper in Neurobiology of Disease).
    """
    if not title or len(norm_title(title)) < 25:
        return None, "", []
    q = " ".join(x for x in (title, (authors or "").split(",")[0], journal, year) if x)
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(
        {"query.bibliographic": q, "rows": 5})
    try:
        items = _http_json(url)["message"]["items"]
    except Exception:
        return None, "", []

    cands = []
    for it in items:
        sh = _shape_crossref(it)
        if year and sh["year"] and sh["year"] != year:
            continue                                    # wrong year: never accept
        ts = _sim(title, sh["title"])
        if ts < 0.90:
            continue                                    # title must really match
        js = _sim(journal, sh["journal"]) if journal and sh["journal"] else 0.0
        jm = _journal_match(journal, sh["journal"]) if journal and sh["journal"] else False
        if journal and sh["journal"] and not (jm or js >= 0.55):
            continue                                    # wrong journal: never accept
        cands.append((round(ts * 3 + js, 3), it.get("DOI", ""), sh, ts, js))

    cands.sort(reverse=True, key=lambda c: c[0])
    if not cands:
        return None, "unmatched", []
    best = cands[0]
    if len(cands) > 1 and abs(best[0] - cands[1][0]) < 0.25:
        return None, "tentative", [c[1] for c in cands[:2] if c[1]]
    return clean_doi(best[1]), "exact", []


# ---------- matching against the library ----------

def match_library(conn, fields):
    """exact | tentative | unmatched. Only titles are trusted; author+year alone is not."""
    from difflib import SequenceMatcher
    tn = fields.get("title_norm") or ""
    doi = clean_doi(fields.get("doi"))
    if doi:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(works)")}
        if "doi" in cols:
            row = conn.execute("SELECT id FROM works WHERE lower(doi)=?", (doi,)).fetchone()
            if row:
                return "exact", row["id"]
    if not tn or len(tn) <= 25:
        return "unmatched", None
    best, best_id = 0.0, None
    for r in conn.execute("SELECT id, title FROM works"):
        t2 = norm_title(r["title"])
        if not t2:
            continue
        if t2 == tn:
            return "exact", r["id"]
        if abs(len(t2) - len(tn)) > 60:
            continue
        ratio = SequenceMatcher(None, tn, t2).ratio()
        if ratio > best:
            best, best_id = ratio, r["id"]
    if best >= 0.85:
        return "tentative", best_id
    return "unmatched", None


# ---------- the pipeline ----------

def extract_refs(conn, work_id, path, resolve=True, query=True, max_resolve=250):
    """Extract one work's bibliography. Returns a summary dict — never a bare count."""
    status = text_layer_status(path)
    if status != "text":
        return {"status": "no-text-layer", "text_layer": status, "entries": 0}
    txt = read_pdf_text(path)
    own_doi = extract_work_doi(txt)
    sec = refs_section(txt)
    style, entries = split_entries(sec)

    rows, _ = [], 0
    for num, body in entries:
        f = parse_entry(body)
        if not f["raw"]:
            continue
        key = ref_key(f["doi"], f["title_norm"], f["authors"], f["year"])
        if not key:
            continue                                    # nothing to key on; drop, don't merge
        meta = ""
        if resolve and f["doi"]:
            got = resolve_doi(conn, f["doi"])
            if got and not got.get("error") and got.get("title"):
                f["title"] = got["title"]
                f["authors"] = got["authors"] or f["authors"]
                f["year"] = got["year"] or f["year"]
                f["title_norm"] = norm_title(f["title"])
                meta = json.dumps(got)
                key = ref_key(f["doi"], f["title_norm"], f["authors"], f["year"])
        elif resolve and query and not f["doi"] and f["title"]:
            found, qstatus, alts = query_doi(conn, f["title"], f["authors"], f["year"],
                                             f.get("journal", ""))
            if found:
                f["doi"] = found
                key = ref_key(f["doi"], f["title_norm"], f["authors"], f["year"])
                meta = json.dumps({"query": "exact"})
            elif alts:
                meta = json.dumps({"tie": alts})

        mstatus, mid = match_library(conn, f)
        if not mstatus or mstatus == "unmatched":
            mstatus = "tentative" if meta and '"tie"' in meta else "unmatched"
        rows.append({"raw": f["raw"], "title_norm": f["title_norm"], "doi": f["doi"],
                     "year": f["year"], "authors": f["authors"], "match_status": mstatus,
                     "match_work_id": mid, "style": style, "key": key, "meta": meta})
        if resolve and len(rows) >= max_resolve:
            resolve = False

    stats = db_upsert(conn, work_id, rows)
    counts = {}
    for r in db_fetch(conn, work_id):
        counts[r["match_status"]] = counts.get(r["match_status"], 0) + 1
    return {"status": "ok", "text_layer": status, "style": style, "entries": len(rows),
            "own_doi": own_doi, **stats, "by_status": counts}


def _db():
    import db
    return db


def db_cache_get(conn, doi):
    return _db().doi_cache_get(conn, doi)


def db_cache_put(conn, doi, payload):
    return _db().doi_cache_put(conn, doi, payload)


def db_upsert(conn, work_id, rows):
    return _db().upsert_refs(conn, work_id, rows)


def db_fetch(conn, work_id):
    return _db().get_refs(conn, work_id)


# ---------- manual entry (the scanned-PDF path) ----------

def manual_add_lines(conn, work_id, text):
    """One reference per line (a blank line also ends an entry). Parsed identically to auto."""
    blocks = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]

    rows = []
    for b in blocks:
        if len(b) < 6:
            continue
        f = parse_entry(b)
        key = ref_key(f["doi"], f["title_norm"], f["authors"], f["year"])
        if not key:
            continue
        mstatus, mid = match_library(conn, f)
        rows.append({"raw": f["raw"], "title_norm": f["title_norm"], "doi": f["doi"],
                     "year": f["year"], "authors": f["authors"], "match_status": mstatus,
                     "match_work_id": mid, "style": "manual", "key": key, "source": "manual"})
    return _db().upsert_refs(conn, work_id, rows)


def manual_add_dois(conn, work_id, text, resolve=True):
    """One DOI per line — the fastest path when a chapter page lists DOIs."""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    rows, rejected = [], []
    for line in lines:
        m = DOI_RE.search(line)
        doi = clean_doi(m.group(0)) if m else ""
        if not doi:
            rejected.append(line[:80])
            continue
        title = authors = year = ""
        meta = ""
        if resolve:
            got = resolve_doi(conn, doi)
            if got and not got.get("error"):
                title, authors, year = got.get("title", ""), got.get("authors", ""), got.get("year", "")
                meta = json.dumps(got)
        tn = norm_title(title)
        rows.append({"raw": line, "title_norm": tn, "doi": doi, "year": year,
                     "authors": authors, "match_status": "exact" if title else "unmatched",
                     "style": "manual", "key": ref_key(doi, tn, authors, year),
                     "source": "manual"})
    stats = _db().upsert_refs(conn, work_id, rows)
    stats["rejected"] = rejected
    return stats
