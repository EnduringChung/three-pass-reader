import os

import db as dbm


def make_db(tmp_path):
    p = str(tmp_path / "test.db")
    return p, dbm.connect(p)


def test_work_file_upsert_idempotent(tmp_path):
    path, conn = make_db(tmp_path)
    w1 = dbm.upsert_work(conn, "ataie-2026", "Ataie et al. 2026")
    w1b = dbm.upsert_work(conn, "ataie-2026", "Ataie et al. 2026")
    assert w1 == w1b
    f1 = dbm.add_file(conn, w1, "D:/x/ataie.pdf", "aaa")
    f1b = dbm.add_file(conn, w1, "D:/x/ataie.pdf", "aaa")
    assert f1 == f1b
    assert dbm.get_work(conn, w1)["active_file_id"] == f1


def test_answer_upsert(tmp_path):
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "s1", "S1")
    dbm.upsert_answer(conn, w, "pass1", "category", "experimental + ABM")
    dbm.upsert_answer(conn, w, "pass1", "category", "updated")
    got = dbm.get_answers(conn, w, "pass1")
    assert got["category"]["value"] == "updated"
    assert got["category"]["updated_at"]


def test_card_fts_roundtrip(tmp_path):
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "s1", "S1")
    cid = dbm.create_card(conn, w, "pass2", question="why does simulated size differ from experiment?")
    hits = dbm.search_cards(conn, "simulated size")
    assert len(hits) == 1 and hits[0]["id"] == cid
    dbm.update_card(conn, cid, {"question": "why is ABM diameter half of Req?"})
    hits = dbm.search_cards(conn, "ABM diameter")
    assert len(hits) == 1
    hits = dbm.search_cards(conn, "simulated size")
    assert len(hits) == 0
    dbm.delete_card(conn, cid)
    assert dbm.search_cards(conn, "ABM diameter") == []


def test_card_multi_anchor(tmp_path):
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "s1", "S1")
    f = dbm.add_file(conn, w, "D:/x/a.pdf", "h1")
    a1 = dbm.add_anchor(conn, f, 5, "[0.1,0.1,0.9,0.5]", "captures/a/fig2e.png")
    a2 = dbm.add_anchor(conn, f, 7, "[0.1,0.2,0.9,0.6]", "captures/a/panelC.png")
    cid = dbm.create_card(conn, w, "pass2", question="size discrepancy")
    dbm.link_anchor(conn, cid, a1, 0)
    dbm.link_anchor(conn, cid, a2, 1)
    cards = dbm.get_cards(conn, w, "pass2")
    assert len(cards) == 1 and len(cards[0]["anchors"]) == 2
    assert cards[0]["anchors"][0]["page"] == 5


def test_listitems(tmp_path):
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "s1", "S1")
    i1 = dbm.add_listitem(conn, w, "pass2", "parking_lot", "JKR contact mechanics")
    i2 = dbm.add_listitem(conn, w, "pass2", "parking_lot", "Box-Behnken design")
    items = dbm.get_listitems(conn, w, "pass2", "parking_lot")
    assert [i["id"] for i in items] == [i1, i2]
    dbm.delete_listitem(conn, i1)
    assert len(dbm.get_listitems(conn, w, "pass2", "parking_lot")) == 1


def test_card_links_bidirectional(tmp_path):
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "s1", "S1")
    w2 = dbm.upsert_work(conn, "s2", "S2")
    c1 = dbm.create_card(conn, w, "pass2", question="size discrepancy exp vs sim")
    c2 = dbm.create_card(conn, w2, "pass3", observation="JKR contact mechanics governs cohesion")
    dbm.link_cards(conn, c1, c2)
    got = dbm.get_card_links(conn, c1)
    assert [c["id"] for c in got["links"]] == [c2]
    # backlink visible from the other side, across papers
    got2 = dbm.get_card_links(conn, c2)
    assert [c["id"] for c in got2["backlinks"]] == [c1] and got2["backlinks"][0]["title"] == "S1"
    # no self-links, no duplicates
    try:
        dbm.link_cards(conn, c1, c1)
        raise AssertionError("self link allowed")
    except ValueError:
        pass
    dbm.link_cards(conn, c1, c2)
    assert len(dbm.get_card_links(conn, c1)["links"]) == 1
    dbm.unlink_cards(conn, c1, c2)
    assert dbm.get_card_links(conn, c1)["links"] == []


def test_highlight_roundtrip(tmp_path):
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "hl-1", "HL 1")
    f = dbm.add_file(conn, w, "D:/x/hl1.pdf", "abc")
    hid = dbm.add_highlight(conn, f, 3, "doubt", [[0.1, 0.2, 0.5, 0.25]], "text…", "n unclear")
    rows = dbm.get_highlights(conn, f)
    assert len(rows) == 1
    assert rows[0]["color"] == "doubt" and rows[0]["page"] == 3
    assert rows[0]["rects"] == [[0.1, 0.2, 0.5, 0.25]]
    assert rows[0]["note"] == "n unclear"
    dbm.update_highlight(conn, hid, color="claim", note="")
    got = dbm.get_highlights(conn, f)[0]
    assert got["color"] == "claim" and got["note"] == ""
    dbm.delete_highlight(conn, hid)
    assert dbm.get_highlights(conn, f) == []


def test_highlight_rejects_bad_colour(tmp_path):
    import pytest
    _, conn = make_db(tmp_path)
    w = dbm.upsert_work(conn, "hl-2", "HL 2")
    f = dbm.add_file(conn, w, "D:/x/hl2.pdf", "abc")
    with pytest.raises(ValueError):
        dbm.add_highlight(conn, f, 1, "chartreuse", [[0, 0, 1, 1]], "x")
    hid = dbm.add_highlight(conn, f, 1, "claim", [[0, 0, 1, 1]], "x")
    with pytest.raises(ValueError):
        dbm.update_highlight(conn, hid, color="chartreuse")
