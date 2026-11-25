"""Sanitiser tests: what the browser produces is never what gets stored."""
import writerdoc as wd


def test_script_tags_and_event_handlers_are_removed():
    out = wd.sanitize('<p onclick="steal()">hi</p><script>alert(1)</script>')
    assert "<script" not in out and "onclick" not in out
    assert "hi" in out


def test_javascript_urls_are_dropped():
    assert "javascript:" not in wd.sanitize('<img src="javascript:alert(1)">')
    assert "javascript:" not in wd.sanitize('<a href="javascript:alert(1)">x</a>')
    assert "<a" not in wd.sanitize('<a href="javascript:alert(1)">x</a>') or "href" not in \
        wd.sanitize('<a href="javascript:alert(1)">x</a>')


def test_unknown_tags_are_unwrapped_but_text_survives():
    # tables ARE whitelisted now (Word import) — iframe/object are not, and their text must survive
    out = wd.sanitize('<iframe src="https://x.test">frame text</iframe>'
                      '<object data="x.swf">object text</object>')
    assert "frame text" in out and "object text" in out
    assert "<iframe" not in out and "<object" not in out


def test_word_blob_loses_styling_but_keeps_text():
    blob = '<p class="MsoNormal"><span style="mso-bidi-font-size:11.0pt">Vascularisation</span> matters.</p>'
    out = wd.sanitize(blob)
    assert "Vascularisation" in out and "mso-" not in out and "style=" not in out


def test_figure_caption_and_cite_markers_survive():
    src = ('<figure><img src="/drafts/3/img-001.png" alt=""><figcaption>A caption</figcaption></figure>'
           '<p><cite data-ref="12" data-work="7" data-style="author-year">(Abbott et al., 2010)</cite></p>')
    out = wd.sanitize(src)
    assert '<img src="/drafts/3/img-001.png"' in out
    assert "<figcaption>A caption</figcaption>" in out
    assert 'data-ref="12"' in out and 'data-work="7"' in out


def test_inline_style_formatting_is_kept_but_scrubbed():
    # Chrome emits bold as <span style="font-weight: bold"> — dropping `style` loses the user's work
    out = wd.sanitize('<span style="font-weight: bold; background: url(javascript:x)">hi</span>')
    assert "font-weight: bold" in out
    assert "javascript" not in out and "background" not in out
    out2 = wd.sanitize('<div style="text-align: center; position: fixed">x</div>')
    assert "text-align: center" in out2 and "position" not in out2


def test_b_and_i_are_folded_to_strong_and_em():
    out = wd.sanitize("<b>bold</b> <i>ital</i>")
    assert "<strong>bold</strong>" in out and "<em>ital</em>" in out


def test_plain_text_has_block_breaks_and_word_count():
    plain = wd.to_plain(wd.sanitize("<h1>Title</h1><p>Two words</p><ul><li>item</li></ul>"))
    assert "Title" in plain and "Two words" in plain and "item" in plain
    assert plain.count("\n") >= 2
    assert wd.word_count(plain) == 4


def test_markdown_heading_does_not_break_on_inner_whitespace_or_spans():
    md = wd.to_markdown("<h2>\n<span>Vascularisation in organoids</span>\n</h2><p>Body</p>")
    assert "## Vascularisation in organoids" in md
    assert "## \n" not in md


def test_markdown_export_keeps_structure():
    html = ('<h1>Paper</h1><p>A <strong>bold</strong> claim <cite data-ref="1">(A, 2020)</cite>.</p>'
            '<ul><li>one</li><li>two</li></ul>'
            '<figure><img src="/drafts/1/img-001.png" alt=""><figcaption>Fig 1</figcaption></figure>')
    md = wd.to_markdown(html)
    assert md.startswith("# Paper")
    assert "**bold**" in md and "(A, 2020)" in md
    assert "- one" in md and "- two" in md
    assert "![Fig 1](/drafts/1/img-001.png)" in md or "![](" in md
