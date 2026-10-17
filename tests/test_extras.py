"""Everyday extras: grayscale, blank pages, personal information, booklet,
compress to a size, ruled note margins, handouts with notes, and the bridge
routes that carry them. Synthetic documents only, so it runs anywhere.

    python tests/test_extras.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webapp", "py"))

import pymupdf  # noqa: E402

from pdfstudio import bridge, extras, pages  # noqa: E402


def my_number(body: str) -> str:
    for digit in "0123456789":
        if extras._my_number_ok(body + digit):
            return body + digit
    raise AssertionError("no check digit")


def coloured_pixels(doc):
    count = 0
    for page in doc:
        pix = page.get_pixmap(dpi=20, annots=True)
        s, n = pix.samples, pix.n
        count += sum(1 for i in range(0, len(s), n) if max(s[i:i + 3]) - min(s[i:i + 3]) > 8)
    return count


def sample() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    number = my_number("12345678901")
    rows = [
        "氏名：山田太郎　電話：０３－１２３４－５６７８",
        " 携帯 090-1234-5678 メール taro.yamada@example.co.jp",
        f"〒100-0001 東京都千代田区 マイナンバー {number[:4]} {number[4:8]} {number[8:]}",
        "カード 4111 1111 1111 1111 注文番号 1234-5678-9012 合計 12,345,678円",
        "請求書番号 0312345678 日付 2026-10-17 TEL(03)1234-5678",
    ]
    y = 60
    for row in rows:
        page.insert_text((40, y), row, fontname="japan", fontsize=11, color=(0.8, 0, 0))
        y += 24
    page.draw_rect(pymupdf.Rect(40, 200, 200, 300), color=(0, 0, 1), fill=(0, 1, 0))
    doc.new_page()  # blank
    grey = doc.new_page()  # a scanned blank: grey paper
    grey.draw_rect(grey.rect, fill=(0.93, 0.93, 0.92), color=None)
    marked = doc.new_page()  # grey paper with a mark: not blank
    marked.draw_rect(marked.rect, fill=(0.93, 0.93, 0.92), color=None)
    marked.draw_line((100, 300), (500, 300), width=2)
    doc.new_page().insert_text((50, 50), "last page", fontsize=12)
    return doc.tobytes()


def call(action, payload):
    out = bridge.dispatch(action, payload)
    assert out["status"] == 200, (action, out)
    return out.get("json") if "json" in out else out


def test_personal_info():
    doc = pymupdf.open("pdf", sample())
    found = extras.find_personal_info(doc)
    labels = sorted((f["label"], f["text"]) for f in found)
    texts = [t for _, t in labels]
    assert "taro.yamada@example.co.jp" in texts
    assert "090-1234-5678" in texts and "０３－１２３４－５６７８" in texts and "(03)1234-5678" in texts
    assert "〒100-0001" in texts
    assert "4111 1111 1111 1111" in texts
    assert any(label == "マイナンバー" for label, _ in labels)
    # Look-alikes that must stay: an order number failing the check digit,
    # a run of digits with no separators, a date.
    assert not any("1234-5678-9012" in t for t in texts)
    assert not any("0312345678" == t for t in texts)
    assert not any("2026-10-17" in t for t in texts)
    assert len(found) == 7, labels


def test_blank_pages():
    doc = pymupdf.open("pdf", sample())
    assert extras.blank_pages(doc) == [1, 2]


def test_grayscale():
    doc = pymupdf.open("pdf", sample())
    assert coloured_pixels(doc) > 0
    extras.grayscale(doc)
    assert coloured_pixels(doc) == 0
    assert "山田太郎" in doc[0].get_text()  # still text, not a picture


def test_booklet():
    assert extras.booklet_order(5) == [(-1, 0), (1, -1), (-1, 2), (3, 4)]
    assert extras.booklet_order(8) == [(7, 0), (1, 6), (5, 2), (3, 4)]
    doc = pymupdf.open("pdf", sample())
    out = pymupdf.open("pdf", extras.booklet(doc, "a4"))
    assert out.page_count == 4 and out[0].rect.width > out[0].rect.height


def test_margins_with_crop_box_and_patterns():
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.insert_text((50, 50), "slide", fontsize=20)
    # PowerPoint writes a CropBox equal to the MediaBox: the margin must still show.
    doc.xref_set_key(page.xref, "CropBox", "[0 0 720 540]")
    pages.add_margins(doc, None, right=170, pattern="lines")
    assert tuple(round(v) for v in doc[0].rect) == (0, 0, 890, 540)
    pix = doc[0].get_pixmap(dpi=144, clip=pymupdf.Rect(740, 40, 880, 500))
    assert min(pix.samples) < 230, "ruled lines expected in the new margin"
    pix = doc[0].get_pixmap(dpi=36, clip=pymupdf.Rect(400, 300, 700, 500))
    assert min(pix.samples) > 250, "nothing drawn over the slide"
    # A rotated page grows on the side the reader sees.
    doc = pymupdf.open()
    doc.new_page(width=720, height=540).set_rotation(90)
    pages.add_margins(doc, None, right=100, pattern="grid")
    assert tuple(round(v) for v in doc[0].rect) == (0, 0, 640, 720)
    # A trimmed page keeps its trim and gains the margin.
    doc = pymupdf.open()
    page = doc.new_page(width=720, height=540)
    page.set_cropbox(pymupdf.Rect(50, 40, 600, 500))
    pages.add_margins(doc, None, right=100, top=10)
    assert tuple(round(v) for v in doc[0].rect) == (0, 0, 650, 470)


def test_handouts():
    doc = pymupdf.open()
    for _ in range(7):
        doc.new_page(width=720, height=540)
    four = pymupdf.open("pdf", pages.nup(doc, 4))
    assert four[0].rect.width > four[0].rect.height, "landscape slides go on landscape sheets"
    notes = pymupdf.open("pdf", pages.nup(doc, "notes3", numbers=True))
    assert notes.page_count == 3 and notes[0].rect.height > notes[0].rect.width
    assert "1 / 3" in notes[0].get_text()
    portrait = pymupdf.open()
    for _ in range(4):
        portrait.new_page()
    assert pymupdf.open("pdf", pages.nup(portrait, 4))[0].rect.height > 842 - 1


def test_bridge_routes():
    opened = call("open", {"name": "extras.pdf", "data": sample()})
    doc_id = opened["id"]
    assert opened["restrictions"] == []
    assert call("blank.find", {"docId": doc_id})["pages"] == [1, 2]
    items = call("pii.find", {"docId": doc_id})["items"]
    assert len(items) == 7
    marked = call("pii.mark", {"docId": doc_id, "items": items[:3]})
    assert marked["marked"] == 3 and isinstance(marked["pages"], list)
    applied = call("redact.apply", {"docId": doc_id, "annots": marked["annots"]})
    # The report's page count must not replace the page list the browser reloads from.
    assert isinstance(applied["pages"], list) and len(applied["pages"]) == 5
    gray = call("grayscale", {"docId": doc_id})
    assert isinstance(gray["pages"], list) and gray["backup"]
    book = bridge.dispatch("booklet", {"docId": doc_id, "paper": "a3"})
    assert book["status"] == 200 and book["filename"].endswith(".pdf")
    small = call("compress.target", {"docId": doc_id, "targetMB": 5})
    assert small["unchanged"] and small["fits"]
    order = call("pages.action", {"docId": doc_id, "action": "reorder", "order": [0, 4, 1, 3, 2]})
    assert len(order["pages"]) == 5
    call("close", {"docId": doc_id})


def test_replace_pages():
    doc = pymupdf.open()
    for n in range(4):
        doc.new_page().insert_text((50, 50), f"old {n + 1}", fontsize=12)
    doc.set_toc([[1, "one", 1], [1, "three", 3]])
    fix = pymupdf.open()
    for n in range(3):
        fix.new_page().insert_text((50, 50), f"new {n + 1}", fontsize=12)
    opened = call("open", {"name": "r.pdf", "data": doc.tobytes()})
    out = call("pages.replace", {"docId": opened["id"], "pages": "[1, 3]", "first": "1",
                                 "files": [{"filename": "fix.pdf", "data": fix.tobytes()}]})
    assert out["replaced"] == 2 and len(out["pages"]) == 4
    entry = bridge.session.get(opened["id"])
    texts = [entry.doc[i].get_text().strip() for i in range(4)]
    assert texts == ["old 1", "new 2", "old 3", "new 3"], texts
    assert [row[2] for row in entry.doc.get_toc()] == [1, 3]
    short = bridge.dispatch("pages.replace", {"docId": opened["id"], "pages": [0, 1, 2], "first": 2,
                                              "files": [{"data": fix.tobytes()}]})
    assert short["status"] == 400 and "足りません" in short["json"]["detail"]
    undone = call("undo", {"docId": opened["id"]})
    assert len(undone["pages"]) == 4
    call("close", {"docId": opened["id"]})


def test_stamps_in_sequence_keep_their_glyphs():
    """Page numbers, then a watermark, then Bates numbers: all three must be in the file.

    PyMuPDF 1.28 (the browser build) merged a face embedded by a later stamp
    into the earlier subset of the same face, dropping the later glyphs. The
    browser also writes with the built-in fallback face until the bundled
    fonts arrive, which is what is forced here.
    """
    from pdfstudio import textap

    saved = textap._font_bytes
    textap._font_bytes = lambda *a, **k: None
    try:
        doc = pymupdf.open()
        for _ in range(3):
            doc.new_page()
        opened = call("open", {"name": "s.pdf", "data": doc.tobytes()})
        i = opened["id"]
        call("stamp-pages", {"docId": i, "kind": "headerFooter", "footer": "- {page} -"})
        call("stamp-pages", {"docId": i, "kind": "watermark", "text": "回覧用", "size": 120, "opacity": 1, "colour": "#ff0000"})
        call("stamp-pages", {"docId": i, "kind": "bates", "prefix": "ABC-"})
        out = pymupdf.open("pdf", bytes(bridge.dispatch("download", {"docId": i})["data"]))
        page = out[1]
        text = page.get_text()
        assert "- 2 -" in text and "回覧用" in text and "ABC-000002" in text
        pix = page.get_pixmap(dpi=30)
        red = sum(1 for k in range(0, len(pix.samples), 3) if pix.samples[k] > 200 and pix.samples[k + 1] < 80)
        assert red > 100, "the watermark glyphs must be drawable"
        call("close", {"docId": i})
    finally:
        textap._font_bytes = saved


def test_split_spreads_and_note_pages():
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    page.insert_text((100, 100), "LEFT", fontsize=30)
    page.insert_text((520, 100), "RIGHT", fontsize=30)
    doc.new_page().insert_text((100, 100), "PORTRAIT", fontsize=30)
    opened = call("open", {"name": "book.pdf", "data": doc.tobytes()})
    i = opened["id"]
    out = call("pages.action", {"docId": i, "action": "split-spreads", "pages": []})
    assert len(out["pages"]) == 3
    entry = bridge.session.get(i)
    # get_text keeps to the visible area (the crop), as a reader sees each half.
    words = [entry.doc[n].get_text().split() for n in range(3)]
    assert "LEFT" in words[0] and "RIGHT" not in words[0], words
    assert "RIGHT" in words[1] and "LEFT" not in words[1], words
    # Nothing landscape left: refused, and no empty undo step is left behind.
    depth = len(entry.snapshots)
    refused = bridge.dispatch("pages.action", {"docId": i, "action": "split-spreads", "pages": [2]})
    assert refused["status"] == 400 and len(entry.snapshots) == depth
    # Duplicating the last page used to fail ("bad page number").
    out = call("pages.action", {"docId": i, "action": "duplicate", "pages": [2]})
    assert len(out["pages"]) == 4
    out = call("pages.action", {"docId": i, "action": "note-page", "at": 4, "width": 595, "height": 842, "pattern": "grid"})
    assert len(out["pages"]) == 5
    pix = entry.doc[4].get_pixmap(dpi=72)
    assert min(pix.samples) < 220, "grid lines on the note page"
    call("close", {"docId": i})


def test_restrictions_reported():
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), "locked", fontsize=12)
    data = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
                       permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_COPY)
    opened = call("open", {"name": "locked.pdf", "data": data})
    assert "注釈・書き込み" in opened["restrictions"] and "印刷" not in opened["restrictions"]
    call("close", {"docId": opened["id"]})


if __name__ == "__main__":
    test_personal_info()
    test_blank_pages()
    test_grayscale()
    test_booklet()
    test_margins_with_crop_box_and_patterns()
    test_handouts()
    test_bridge_routes()
    test_replace_pages()
    test_stamps_in_sequence_keep_their_glyphs()
    test_split_spreads_and_note_pages()
    test_restrictions_reported()
    print("extras tests passed")
