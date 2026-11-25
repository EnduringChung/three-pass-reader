"""Writer documents: HTML sanitising, plain-text extraction, markdown export.

The editor is a `contenteditable`, and a contenteditable happily produces whatever the browser
or a pasted Word document felt like producing. Everything that reaches the database goes through
`sanitize()` first: a tag/attribute whitelist, `javascript:` dropped, unknown tags unwrapped so
their text survives. Nothing here generates prose — it only cleans and converts what the author wrote.
"""
import html
import re
from html.parser import HTMLParser

ALLOWED_TAGS = {
    "p", "br", "h1", "h2", "h3", "strong", "b", "em", "i", "u", "s", "ul", "ol", "li",
    "blockquote", "hr", "figure", "figcaption", "img", "cite", "a", "code", "pre", "div", "span",
    # Word documents are full of tables; dropping them would silently lose content on import
    "table", "thead", "tbody", "tr", "th", "td", "caption",
}
# b/i are folded onto strong/em on the way out, but accepted on the way in (execCommand emits them)
FOLD = {"b": "strong", "i": "em"}
ALLOWED_ATTRS = {
    "img": {"src", "alt"},
    "cite": {"data-ref", "data-work", "data-style"},
    "a": {"href"},
    # execCommand expresses bold/italic/alignment as inline styles on span/div, so a whitelist
    # that drops `style` silently loses the user's formatting. Only these declarations survive.
    "span": {"style"},
    "div": {"style"},
    "p": {"style"},
    "th": {"colspan", "rowspan"},
    "td": {"colspan", "rowspan"},
}
STYLE_PROPS = {
    "font-weight": {"bold", "bolder", "normal", "600", "700", "800", "900"},
    "font-style": {"italic", "normal", "oblique"},
    "text-decoration": {"underline", "line-through", "none"},
    "text-decoration-line": {"underline", "line-through"},
    "text-align": {"left", "right", "center", "justify"},
}


def scrub_style(value):
    """Keep only whitelisted CSS declarations. Everything else is discarded."""
    keep = []
    for decl in (value or "").split(";"):
        if ":" not in decl:
            continue
        prop, val = decl.split(":", 1)
        prop = prop.strip().lower()
        val = val.strip().lower()
        if prop in STYLE_PROPS and val in STYLE_PROPS[prop]:
            keep.append(f"{prop}: {val}")
    return "; ".join(keep)
BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "li", "blockquote", "pre", "figcaption", "figure", "tr"}
VOID_TAGS = {"br", "hr", "img"}
SAFE_URL = re.compile(r"^(?:https?://|/|#|mailto:)", re.I)
BAD_URL = re.compile(r"^\s*(?:javascript|data|vbscript)\s*:", re.I)


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []

    # --- helpers ---------------------------------------------------------
    def _attrs(self, tag, attrs):
        allowed = ALLOWED_ATTRS.get(tag, set())
        kept = []
        for k, v in attrs:
            if k and k.lower() == "style":
                scrubbed = scrub_style(v)
                if scrubbed and "style" in allowed:
                    kept.append(f'style="{html.escape(scrubbed, quote=True)}"')
                continue
            k = (k or "").lower()
            if k not in allowed or v is None:
                continue
            if k in ("src", "href"):
                if BAD_URL.match(v) or not SAFE_URL.match(v.strip()):
                    continue
            kept.append(f'{k}="{html.escape(v, quote=True)}"')
        return (" " + " ".join(kept)) if kept else ""

    # --- parser hooks ----------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in ALLOWED_TAGS:
            return                                   # unwrap: children/text still flow through
        tag = FOLD.get(tag, tag)
        if tag in VOID_TAGS:
            self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
            return
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = FOLD.get(tag.lower(), tag.lower())
        if tag in VOID_TAGS or tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def close(self):
        super().close()
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")


def sanitize(raw_html):
    """Whitelist-filter an HTML fragment. Returns a safe fragment (never None)."""
    if not raw_html:
        return ""
    p = _Sanitizer()
    p.feed(raw_html)
    p.close()
    out = "".join(p.out)
    # collapse the empty shells contenteditable leaves behind
    out = re.sub(r"<(p|div|span)>\s*</\1>", "", out)
    return out.strip()


def to_plain(safe_html):
    """Plain text for search/word count: block tags become newlines."""
    txt = re.sub(r"(?i)<(br|/p|/div|/h[1-3]|/li|/blockquote|/figcaption|/figure|hr|/tr)[^>]*>", "\n", safe_html or "")
    txt = re.sub(r"(?i)<t[hd][^>]*>", " ", txt)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"[ \t\u00a0]+", " ", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def word_count(plain):
    return len([w for w in re.split(r"\s+", plain or "") if w.strip()])


# ---------- markdown (export) ----------

def to_markdown(safe_html, base_url=""):
    """Small, predictable HTML→markdown for export. Not a general converter."""
    md = safe_html or ""

    def remap_cites(m):
        body = m.group(2)
        return body.strip()

    # .strip() matters: contenteditable leaves whitespace inside blocks, which produced "## \nTitle"
    md = re.sub(r"(?is)<h1[^>]*>(.*?)</h1>", lambda m: "\n# " + _strip(m.group(1)) + "\n", md)
    md = re.sub(r"(?is)<h2[^>]*>(.*?)</h2>", lambda m: "\n## " + _strip(m.group(1)) + "\n", md)
    md = re.sub(r"(?is)<h3[^>]*>(.*?)</h3>", lambda m: "\n### " + _strip(m.group(1)) + "\n", md)
    md = re.sub(r"(?is)<blockquote[^>]*>(.*?)</blockquote>",
                lambda m: "\n> " + _strip(m.group(1)) + "\n", md)
    md = re.sub(r"(?is)<figure[^>]*>\s*<img[^>]*src=\"([^\"]+)\"[^>]*>(.*?)</figure>",
                lambda m: f"\n![{_strip(m.group(2)) or 'figure'}]({m.group(1)})\n\n{_strip(m.group(2))}\n", md)
    md = re.sub(r"(?is)<img[^>]*src=\"([^\"]+)\"[^>]*/?>", r"\n![](\1)\n", md)
    md = re.sub(r"(?is)<figcaption[^>]*>(.*?)</figcaption>", r"\n*\1*\n", md)
    md = re.sub(r"(?is)<table[^>]*>(.*?)</table>", _table_md, md)
    md = re.sub(r"(?is)<li[^>]*>(.*?)</li>", lambda m: "- " + _strip(m.group(1)) + "\n", md)
    md = re.sub(r"(?is)</?(ul|ol)[^>]*>", "\n", md)
    md = re.sub(r"(?is)<blockquote[^>]*>(.*?)</blockquote>", r"\n> \1\n", md)
    md = re.sub(r"(?is)<hr[^>]*>", "\n---\n", md)
    md = re.sub(r"(?is)<strong[^>]*>(.*?)</strong>", r"**\1**", md)
    md = re.sub(r"(?is)<em[^>]*>(.*?)</em>", r"*\1*", md)
    md = re.sub(r"(?is)<u[^>]*>(.*?)</u>", r"_\1_", md)
    md = re.sub(r"(?is)<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"[\2](\1)", md)
    md = re.sub(r"(?is)<cite[^>]*>(.*?)</cite>", r"\1", md)
    md = re.sub(r"(?is)</?(p|div|span|code|pre)[^>]*>", "\n", md)
    md = re.sub(r"(?is)<br[^>]*/?>", "\n", md)
    md = _strip_tags_keep_newlines(md)
    md = re.sub(r"[ \t]+", " ", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"


def _table_md(m):
    """GFM pipe table: first row is the header, so it needs a separator row."""
    rows = re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", m.group(1) or "")
    lines = []
    for i, row in enumerate(rows):
        cells = re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", row)
        lines.append("| " + " | ".join(_strip(c) for c in cells) + " |")
        if i == 0:
            lines.append("|" + "|".join(" --- " for _ in cells) + "|")
    return "\n" + "\n".join(lines) + "\n" if lines else ""


def _strip_tags_keep_newlines(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _strip(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()
