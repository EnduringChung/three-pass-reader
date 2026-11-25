"""Reference extraction tests. Real PDFs are used for recall; network is never touched here."""
import json
import pathlib
import sqlite3

import pytest

import db as dbm
import refs

PROJECT = pathlib.Path(__file__).resolve().parents[1]


def make_db(tmp_path):
    return dbm.connect(str(tmp_path / "refs.db"))


def real_paths():
    """work_id -> pdf path, from the live DB; skip the whole module if it is unavailable."""
    dbfile = PROJECT / "data" / "reader.db"
    if not dbfile.exists():
        return {}
    conn = sqlite3.connect(str(dbfile))
    try:
        return {wid: p for wid, p in conn.execute("SELECT work_id, path FROM files")}
    finally:
        conn.close()


PATHS = real_paths()


def need(wid):
    if wid not in PATHS or not pathlib.Path(PATHS[wid]).exists():
        pytest.skip(f"work {wid} not available")
    return PATHS[wid]


# ---------- pure helpers ----------

def test_norm_title_strips_accents_and_punctuation():
    assert refs.norm_title("Fine-tuning the extracellular environment!") == \
        "fine tuning the extracellular environment"
    assert refs.norm_title("Der Müller-Effekt") == "der muller effekt"


def test_ref_key_prefers_doi_then_title_then_author_year():
    assert refs.ref_key("10.1000/ABC.", "x" * 40, "A", "2020") == "doi:10.1000/abc"
    assert refs.ref_key("", "a title that is definitely long enough to key on", "A", "2020") \
        .startswith("ta:")
    assert refs.ref_key("", "", "Chen", "2024") == "ay:chen|2024"
    assert refs.ref_key("", "", "", "") == ""


def test_clean_doi_strips_trailing_punctuation():
    assert refs.clean_doi("https://doi.org/10.1016/J.JHEP.2025.11.018.") == \
        "10.1016/j.jhep.2025.11.018"
    assert refs.clean_doi("doi: 10.1002/ADVS.202511302") == "10.1002/advs.202511302"
    assert refs.clean_doi("10.1016/j.jhep.2025.11.018.") == "10.1016/j.jhep.2025.11.018"


# ---------- section location / cleaning ----------

def test_find_refs_start_takes_the_last_heading_line():
    txt = "Intro mentions\nreferences\nlike this inline.\n\nREFERENCES\n1. A.\n2. B.\n"
    i = refs.find_refs_start(txt)
    assert txt[i:].lstrip().startswith("1. A.")


def test_dense_start_fires_when_heading_missing():
    body = "Body text. " * 400
    refs_block = "\n".join(f"{n}. Author, A. ({2000 + n % 20}). A title here. J. Test {n}, 1."
                           for n in range(1, 40))
    txt = body + "\n" + refs_block
    assert refs.dense_start(txt) > len(body) * 0.5


def test_doi_cut_at_a_separator_is_joined_but_entries_are_not():
    sec = ("1. A. Kengelbach-Weigand, T. Thielen, Bone regeneration. npj Regen. Med. 6, 1. "
           "https://doi.org/10.1038/s41536-021- \n00158-8.\n"
           "2. Kim, S.J. (2020). A title that is long enough. J. Test 3, 1. "
           "https://doi.org/10.1002/SMMD.20230038.\n"
           "3. Third entry, A. (2021). Another title entirely. J. Test 4, 9.")
    style, ents = refs.split_entries(sec)
    assert style in ("numbered", "numbered-weak")
    assert len(ents) == 3, f"entry numbers must survive the DOI join (got {len(ents)})"
    joined = ents[0][1]
    assert "10.1038/s41536-021-00158-8" in joined, joined[:160]
    assert "10.1002/smmd.20230038" in refs.parse_entry(ents[1][1])["doi"].lower()


def test_doi_tails_starting_uppercase_or_digits_only_are_joined():
    # both shapes appear in work 11 and both truncated a DOI to "10.1002/advs" / "10.1089/ten"
    sec = ("1. Choi, D., Gonzalez-Suarez, A.M. (2021). A title long enough to key. "
           "Adv. Sci. 8, 1. https://doi.org/10.1002/advs.\n20200256.\n"
           "2. Rambøl, M.H. (2020). Another title long enough. Tissue Eng. 26, 5. "
           "https://doi.org/10.1089/ten. \nTEA.2019.0186.\n"
           "3. Aleman, J. (2019). Third title here for keying. Biotechnol. 116, 9. "
           "https://doi.org/10.1002/bit. \n26871.\n"
           "4. Fourth entry, A. (2018). A fourth title that is long. J. Test 1, 1.")
    style, ents = refs.split_entries(sec)
    dois = [refs.parse_entry(b)["doi"] for _, b in ents]
    assert "10.1002/advs.20200256" in dois, dois
    assert "10.1089/ten.tea.2019.0186" in dois, dois
    assert "10.1002/bit.26871" in dois, dois
    assert len(ents) == 4


def test_wrapped_doi_is_joined_before_matching():
    sec = "12. Kim, S. (2020). Title of something. J. Hepatol. 84, 946. https://doi.org/10.1016/j.jhep.\n2025.11.018."
    out = refs.split_entries(sec)[1][0][1]
    assert "10.1016/j.jhep.2025.11.018" in out


# ---------- splitting ----------

def test_split_bracketed():
    sec = "\n".join(f"[{n}] Author {n}, Title {n}, J. Test {n} (20{10+n}) 1." for n in range(1, 9))
    style, ents = refs.split_entries(sec)
    assert style == "bracketed" and len(ents) == 8
    assert ents[0][0] == 1 and "Author 1" in ents[0][1]


def test_split_bare_keeps_lowercase_surnames():
    # the bug that ate entry 81: "81. van den Berg" has no capital after the number
    sec = "\n".join(f"{n}. Author, A. (2000). Title {n}. J. Test {n}, 1." for n in range(1, 80))
    sec += "\n80. Koning, M. (2022). T. J. x 7, 40.\n81. van den Berg, C.W. (2018). T. J. y 10, 751.\n82. X, Y. (2019). T. J. z 3, 1."
    style, ents = refs.split_entries(sec)
    nums = [n for n, _ in ents]
    assert style == "numbered"
    assert 81 in nums, "lowercase surname particle must not delete the entry"
    assert any("van den Berg" in t for n, t in ents if n == 81)


def test_split_falls_back_to_author_year():
    sec = "\n".join(
        f"Author{n}, A., Smith, B. (20{10 + n}). A reasonably long title number {n}. "
        f"Journal of Testing {n}, 1-10." for n in range(1, 9))
    style, ents = refs.split_entries(sec)
    assert style == "author-year" and len(ents) == 8


def test_sequence_guard_rejects_fake_numbering():
    # page numbers masquerading as entries: numbers are wild, so the detector must not trust them
    sec = "\n".join(f"{n} Some line of body text that is not a reference at all" for n in (3, 400, 12, 77, 5, 900))
    style, _ = refs.split_entries(sec)
    assert style != "bracketed"


# ---------- single entry parsing ----------

def test_parse_elsevier_entry_without_title():
    f = refs.parse_entry("S.J. Lee, et al., Adv. Mater. 36 (49) (2024) e2408032.")
    assert f["year"] == "2024"
    assert f["title"] == "", "no title is printed — empty is the honest answer"
    assert "Lee" in f["authors"]


def test_parse_elsevier_entry_with_title():
    f = refs.parse_entry("Zixuan Zhao, Xinyi Chen, et al., Organoids, Nat. Rev. Methods Primers 2 (1) (2022) 94.")
    assert f["title"] == "Organoids"
    assert f["year"] == "2022"


def test_parse_author_year_entry():
    f = refs.parse_entry("van den Berg, C.W., Ritsma, L. (2018). Renal subcapsular transplantation of "
                         "PSC-derived kidney organoids. Stem Cell Rep. 10, 751-765. "
                         "https://doi.org/10.1016/j.stemcr.2018.01.041.")
    assert f["year"] == "2018"
    assert f["doi"] == "10.1016/j.stemcr.2018.01.041"
    assert "Renal subcapsular" in f["title"]


def test_parse_quoted_ieee_entry_takes_title_from_quotes_and_journal_from_between():
    # this is the style of work 2; the old parser swallowed the journal into the title
    f = refs.parse_entry('Abbott NJ, Patabendige AAK, Dolman DEM, and Begley DJ, '
                         '\u201cStructure and function of the blood\u2013brain barrier,\u201d '
                         'Neurobiol. Dis, vol. 37, no. 1, pp. 13\u201325, 2010. [PubMed: 19664713]')
    assert f["title"] == "Structure and function of the blood\u2013brain barrier", f["title"]
    assert f["journal"] == "Neurobiol. Dis", f["journal"]
    assert f["year"] == "2010"
    assert "Neurobiol" not in f["title"]


def test_journal_match_accepts_abbreviations_and_rejects_other_journals():
    assert refs._journal_match("Neurobiol. Dis", "Neurobiology of Disease")
    assert refs._journal_match("Nat. Rev. Methods Primers", "Nature Reviews Methods Primers")
    assert not refs._journal_match("Neurobiol. Dis", "Frontiers in Pharmacology")
    assert not refs._journal_match("Cell Rep", "Stem Cell Reports")


def test_query_doi_rejects_same_title_wrong_journal_and_picks_the_right_one(monkeypatch):
    nbd = {"DOI": "10.1016/j.nbd.2009.07.030", "title": ["Structure and function of the blood-brain barrier"],
           "issued": {"date-parts": [[2010]]}, "container-title": ["Neurobiology of Disease"],
           "author": [{"given": "N.", "family": "Abbott"}], "type": "journal-article"}
    fphar = {"DOI": "10.3389/conf.fphar.2010.02.00002", "title": ["Structure and function of the blood-brain barrier"],
             "issued": {"date-parts": [[2010]]}, "container-title": ["Frontiers in Pharmacology"],
             "author": [{"given": "X", "family": "Y"}], "type": "journal-article"}
    monkeypatch.setattr(refs, "_http_json", lambda url, timeout=20: {"message": {"items": [fphar, nbd]}})
    got, status, alts = refs.query_doi(None, "Structure and function of the blood-brain barrier",
                                       "Abbott NJ", "2010", "Neurobiol. Dis")
    assert got == "10.1016/j.nbd.2009.07.030" and status == "exact", (got, status, alts)

    # same title, only the wrong journal available → refuse rather than attach a wrong DOI
    monkeypatch.setattr(refs, "_http_json", lambda url, timeout=20: {"message": {"items": [fphar]}})
    got, status, _ = refs.query_doi(None, "Structure and function of the blood-brain barrier",
                                    "Abbott NJ", "2010", "Neurobiol. Dis")
    assert got is None and status == "unmatched"


def test_parse_never_takes_a_page_number_as_year():
    f = refs.parse_entry("Koning, M. (2022). Vasculogenesis in kidney organoids. npj Regen. Med. 7, 40.")
    assert f["year"] == "2022"


# ---------- real PDFs ----------

def test_real_work1_bracketed_31_entries():
    path = need(1)
    f = refs.read_pdf_text(path)
    style, ents = refs.split_entries(refs.refs_section(f))
    assert style == "bracketed"
    assert len(ents) == 31


def test_real_work11_recovers_all_220_including_lowercase_entry_81():
    path = need(11)
    txt = refs.read_pdf_text(path)
    style, ents = refs.split_entries(refs.refs_section(txt))
    nums = [n for n, _ in ents]
    assert style == "numbered"
    assert len(ents) == 220, f"got {len(ents)}"
    assert sorted(nums) == list(range(1, 221)), "every number 1..220 must be present"
    assert any("van den Berg" in t for n, t in ents if n == 81)


def test_real_work121_numbered_without_brackets():
    path = need(121)
    style, ents = refs.split_entries(refs.refs_section(refs.read_pdf_text(path)))
    assert style in ("numbered", "numbered-weak")
    assert len(ents) >= 90


def test_real_work3_has_titles_and_74_entries():
    path = need(3)
    style, ents = refs.split_entries(refs.refs_section(refs.read_pdf_text(path)))
    assert len(ents) == 74
    parsed = [refs.parse_entry(t) for _, t in ents]
    with_title = sum(1 for p in parsed if p["title"])
    assert with_title >= 40, f"only {with_title} entries got a title"


def test_text_layer_status_flags_scans_and_digital_papers():
    assert refs.text_layer_status(need(97)) == "image-only"
    assert refs.text_layer_status(need(103)) == "image-only"
    assert refs.text_layer_status(need(1)) == "text"


def test_text_layer_status_is_unreadable_for_garbage(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert refs.text_layer_status(str(bad)) == "unreadable"


def test_extract_refs_summary_on_real_paper(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "w1", "a paper from the library")
    dbm.add_file(conn, w, need(1), "abc")
    out = refs.extract_refs(conn, w, need(1), resolve=False, query=False)
    assert out["status"] == "ok" and out["entries"] == 31
    assert (out["own_doi"] or "").startswith("10."), "the paper's own DOI should be found"
    rows = dbm.get_refs(conn, w)
    assert len(rows) == 31
    assert all(r["source"] == "auto" for r in rows)


def test_extract_on_scan_reports_no_text_layer(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "scan", "Tumor Angiogenesis ch11")
    out = refs.extract_refs(conn, w, need(97), resolve=False, query=False)
    assert out["status"] == "no-text-layer" and out["entries"] == 0


def test_resolve_doi_uses_cache_and_makes_no_second_request(tmp_path, monkeypatch):
    conn = make_db(tmp_path)
    calls = {"n": 0}

    def fake_http(url, timeout=20):
        calls["n"] += 1
        return {"message": {"DOI": "10.1/x", "title": ["A cached title"],
                            "issued": {"date-parts": [[2020]]},
                            "container-title": ["J. Test"], "author": [], "type": "journal-article"}}

    monkeypatch.setattr(refs, "_http_json", fake_http)
    first = refs.resolve_doi(conn, "10.1/x", sleep=0)
    second = refs.resolve_doi(conn, "10.1/x", sleep=0)
    assert first == second and calls["n"] == 1, f"cache miss: {calls['n']} requests"
    # a plain connection without row_factory must still hit the cache
    import sqlite3 as _s
    raw = _s.connect(str(tmp_path / "refs.db"))
    assert refs.resolve_doi(raw, "10.1/x", sleep=0) == first and calls["n"] == 1


# ---------- upsert rules ----------

def test_upsert_preserves_star_why_fix_and_protects_manual_rows(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "u1", "U1")
    key = "ta:a title long enough to be keyed on for the test"
    dbm.upsert_refs(conn, w, [{"raw": "v1", "title_norm": "a title", "key": key,
                               "style": "bracketed", "match_status": "unmatched"}])
    rid = dbm.get_refs(conn, w)[0]["id"]
    dbm.update_ref(conn, rid, {"star": 1, "why": "core to my question", "fix": "checked page 9"})

    dbm.upsert_refs(conn, w, [{"raw": "v2", "title_norm": "a title", "key": key,
                               "style": "bracketed", "match_status": "tentative"}])
    r = dbm.get_refs(conn, w)[0]
    assert r["raw"] == "v2" and r["match_status"] == "tentative"      # refreshed
    assert r["star"] == 1 and r["why"] == "core to my question"      # curation untouched

    # manual row with the same key must survive and win
    dbm.delete_ref(conn, rid)
    dbm.upsert_refs(conn, w, [{"raw": "typed by hand", "key": key, "style": "manual",
                               "source": "manual", "match_status": "unmatched"}])
    stats = dbm.upsert_refs(conn, w, [{"raw": "parser again", "key": key, "style": "bracketed"}])
    rows = dbm.get_refs(conn, w)
    assert len(rows) == 1 and rows[0]["raw"] == "typed by hand"
    assert stats["collisions"] == 1 and stats["skipped_manual"] == 1


def test_upsert_dedupes_within_a_work(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "u2", "U2")
    row = {"raw": "x", "key": "doi:10.1/abc", "style": "bracketed"}
    s1 = dbm.upsert_refs(conn, w, [row])
    s2 = dbm.upsert_refs(conn, w, [row])
    assert s1["added"] == 1 and s2["added"] == 0 and s2["updated"] == 1
    assert len(dbm.get_refs(conn, w)) == 1


# ---------- manual entry ----------

def test_manual_lines_parse_and_are_marked(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "m1", "M1")
    text = ("Koning, M. (2022). Vasculogenesis in kidney organoids. npj Regen. Med. 7, 40.\n"
            "Chen, W. (2024). Tailoring cell sheets. Smart Medicine 3, e20230038.\n")
    stats = refs.manual_add_lines(conn, w, text)
    rows = dbm.get_refs(conn, w)
    assert stats["added"] == 2 and len(rows) == 2
    assert all(r["source"] == "manual" and r["style"] == "manual" for r in rows)
    again = refs.manual_add_lines(conn, w, text)
    assert again["added"] == 0 and len(dbm.get_refs(conn, w)) == 2


def test_manual_dois_reject_junk_and_keep_valid(tmp_path):
    conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "m2", "M2")
    stats = refs.manual_add_dois(conn, w, "10.1038/s41586-020-1234-5\nnot a doi\n", resolve=False)
    rows = dbm.get_refs(conn, w)
    assert stats["added"] == 1 and len(stats["rejected"]) == 1
    assert rows[0]["doi"] == "10.1038/s41586-020-1234-5"


def test_common_counts_distinct_works(tmp_path):
    conn = make_db(tmp_path)
    w1 = dbm.upsert_work(conn, "c1", "C1")
    w2 = dbm.upsert_work(conn, "c2", "C2")
    key = "doi:10.1/shared"
    dbm.upsert_refs(conn, w1, [{"raw": "a", "key": key, "style": "bracketed"},
                               {"raw": "a again", "key": key + "-dup", "style": "bracketed"}])
    dbm.upsert_refs(conn, w2, [{"raw": "b", "key": key, "style": "bracketed"}])
    common = dbm.refs_common(conn, min_works=2)
    assert len(common) == 1
    assert common[0]["n_works"] == 2 and common[0]["n_rows"] == 2
