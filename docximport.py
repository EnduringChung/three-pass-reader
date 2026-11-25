""".docx → editor HTML.

Word hands us paragraphs, runs, tables and images. This maps the styles a real document uses onto the
editor's whitelist (h1–h3, p, ul/ol, blockquote, pre, table, figure/img). Unknown styles degrade to
plain paragraphs, never to nothing — losing his text on import would be the worst possible failure.

Output still goes through `writerdoc.sanitize()`; this module only proposes structure.
"""
import html
import re

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

HEADING_TAGS = {"title": "h1", "subtitle": "h2", "heading 1": "h1", "heading 2": "h2", "heading 3": "h3",
                "heading 4": "h3", "heading 5": "h3", "heading 6": "h3"}
QUOTE_STYLES = {"quote", "block quote", "intense quote", "blockquote"}
CODE_STYLES = {"code", "source code", "html preformatted", "macro text", "plain text"}


def _esc(text):
    return html.escape(text or "", quote=False)


def _block_tag(style_name):
    """Word style name → editor block tag. Anything unrecognised stays a paragraph."""
    s = (style_name or "").strip().lower()
    if s in HEADING_TAGS:
        return HEADING_TAGS[s]
    m = re.match(r"heading\s+(\d)", s)
    if m:
        n = int(m.group(1))
        return "h1" if n == 1 else ("h2" if n == 2 else "h3")
    if s in QUOTE_STYLES:
        return "blockquote"
    if s in CODE_STYLES:
        return "pre"
    if s.startswith("list") or s in ("bullet 1", "bullet 2", "number 1", "number 2"):
        return "li"
    return "p"


def _list_level(paragraph):
    """Indent depth for a list item: numPr/ilvl when present, else the style's own index.

    Word expresses depth two different ways — a numbering definition on the paragraph, or a
    pre-built style such as "List Bullet 2" — and imports must honour both.
    """
    level = 0
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None and ppr.numPr.ilvl is not None:
        try:
            level = int(ppr.numPr.ilvl.val)
        except (TypeError, ValueError):
            level = 0
    m = re.match(r"list\s+(?:bullet|number|paragraph)?\s*(\d)", (paragraph.style.name or "").lower())
    if m:
        level = max(level, int(m.group(1)) - 1)
    return level


def _render_list(items):
    """[(level, kind, html)] → properly nested <ul>/<ol>.

    A nested list must open INSIDE the preceding <li>, otherwise the browser hoists it out as a
    sibling and Word's outline collapses to one level. Each stack frame remembers whether its <li>
    is still open, so unwinding closes the parent item too.
    """
    out, stack = [], []          # frames: {"kind", "level", "li_open"}
    for lvl, kind, inner in items:
        while stack and stack[-1]["level"] > lvl:          # climb out of deeper lists
            if stack[-1]["li_open"]:
                out.append("</li>")
                stack[-1]["li_open"] = False
            out.append(f"</{stack[-1]['kind']}>")
            stack.pop()
        if not stack or stack[-1]["level"] < lvl:
            out.append(f"<{kind}>")                        # opens inside the parent's open <li>
            stack.append({"kind": kind, "level": lvl, "li_open": False})
        elif stack[-1]["kind"] != kind:                    # same depth, different list type
            if stack[-1]["li_open"]:
                out.append("</li>")
                stack[-1]["li_open"] = False
            out.append(f"</{stack[-1]['kind']}>")
            stack.pop()
            out.append(f"<{kind}>")
            stack.append({"kind": kind, "level": lvl, "li_open": False})
        if stack[-1]["li_open"]:                           # a sibling: close the previous item
            out.append("</li>")
            stack[-1]["li_open"] = False
        out.append(f"<li>{inner}")
        stack[-1]["li_open"] = True
    while stack:
        if stack[-1]["li_open"]:
            out.append("</li>")
            stack[-1]["li_open"] = False
        out.append(f"</{stack[-1]['kind']}>")
        stack.pop()
    return "".join(out)


def _list_kind(paragraph):
    """'ol' for numbered lists, 'ul' otherwise (bullets and list-paragraph continuations)."""
    style = (paragraph.style.name or "").lower()
    if "number" in style:
        return "ol"
    ppr = paragraph._p.pPr
    if ppr is not None and ppr.numPr is not None:
        num_id = ppr.numPr.numId.val if ppr.numPr.numId is not None else None
        numbering = getattr(paragraph.part, "numbering_part", None)
        if numbering is not None and num_id is not None:
            try:
                fmt = numbering.numbering_part.element.xpath(
                    f".//w:num[@w:numId='{num_id}']/w:abstractNumId/@w:val")
                if fmt:
                    abstract = numbering.numbering_part.element.xpath(
                        f".//w:abstractNum[@w:abstractNumId='{fmt[0]}']//w:numFmt/@w:val")
                    if abstract and str(abstract[0]).lower() not in ("bullet", "none"):
                        return "ol"
            except Exception:
                pass
    return "ul"


class _Ctx:
    """Carries the image sink and the counters for the import summary."""

    def __init__(self, save_image):
        self.save_image = save_image          # (ext, bytes) -> url string
        self.images = 0
        self.tables = 0
        self.paragraphs = 0
        self.headings = 0

    # --- inline ---------------------------------------------------------
    def inline(self, paragraph_element, paragraph):
        out = []
        for child in paragraph_element.iterchildren():
            tag = child.tag
            if tag == qn("w:r"):
                out.append(self._run(Run(child, paragraph), paragraph))
            elif tag == qn("w:hyperlink"):
                rid = child.get(qn("r:id"))
                target = ""
                try:
                    target = paragraph.part.rels[rid].target_ref if rid else ""
                except Exception:
                    target = ""
                text = "".join(t.text or "" for t in child.iter(qn("w:t")))
                if text:
                    if target and re.match(r"^https?://", target, re.I):
                        out.append(f'<a href="{html.escape(target, quote=True)}">{_esc(text)}</a>')
                    else:
                        out.append(_esc(text))
        return "".join(out)

    def _run(self, run, paragraph):
        pieces = []
        text = run.text or ""
        for blip in run._element.iter(qn("a:blip")):
            rid = blip.get(qn("r:embed"))
            if not rid:
                continue
            try:
                part = paragraph.part.related_parts[rid]
            except Exception:
                continue
            ext = (part.partname.ext or "png").lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                ext = "png"
            url = self.save_image(ext, part.blob)
            if url:
                self.images += 1
                pieces.append(f'<img src="{html.escape(url, quote=True)}" alt="">')
        if not text.strip() and not pieces:
            return ""
        body = _esc(text).replace("\n", "<br>")
        body = "".join(pieces) + body if pieces else body
        if not text.strip():
            return body
        if run.bold:
            body = f"<strong>{body}</strong>"
        if run.italic:
            body = f"<em>{body}</em>"
        if run.underline:
            body = f"<u>{body}</u>"
        if run.font is not None and (run.font.strike or run.font.double_strike):
            body = f"<s>{body}</s>"
        return body

    # --- blocks ---------------------------------------------------------
    def paragraph(self, paragraph):
        tag = _block_tag(paragraph.style.name)
        inner = self.inline(paragraph._p, paragraph)
        if not inner.strip():
            return None
        self.paragraphs += 1
        if tag in ("h1", "h2", "h3"):
            self.headings += 1
        if tag == "li":
            return ("li", inner)
        if tag == "pre":
            return f"<pre>{_esc(''.join(t.text or '' for t in paragraph._p.iter(qn('w:t'))))}</pre>"
        return f"<{tag}>{inner}</{tag}>"

    def table(self, tbl):
        self.tables += 1
        rows = []
        for row in tbl.rows:
            cells = []
            for cell in row.cells:
                bits = []
                for child in cell._tc.iterchildren():
                    if child.tag == qn("w:p"):
                        inner = self.inline(child, Paragraph(child, cell))
                        if inner.strip():
                            bits.append(inner)
                cells.append("<br>".join(bits))
            rows.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        return "<table><tbody>" + "".join(rows) + "</tbody></table>"


def _blocks(document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def docx_to_html(path, save_image):
    """Convert a .docx to an HTML fragment plus import statistics."""
    doc = Document(str(path))
    ctx = _Ctx(save_image)
    out = []
    list_buf = []

    def flush_list():
        nonlocal list_buf
        if list_buf:
            out.append(_render_list(list_buf))
        list_buf = []

    for block in _blocks(doc):
        if isinstance(block, Table):
            flush_list()
            out.append(ctx.table(block))
            continue
        result = ctx.paragraph(block)
        if result is None:
            continue
        if isinstance(result, tuple):                    # a list item
            list_buf.append((_list_level(block), _list_kind(block), result[1]))
            continue
        flush_list()
        out.append(result)
    flush_list()

    title = ""
    try:
        title = (doc.core_properties.title or "").strip()
    except Exception:
        title = ""
    if not title:
        m = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", "\n".join(out))
        if m:
            title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
    stats = {"paragraphs": ctx.paragraphs, "headings": ctx.headings,
             "tables": ctx.tables, "images": ctx.images}
    return "\n".join(out), title, stats
