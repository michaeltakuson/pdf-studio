"""Text boxes, Japanese fonts, pictures, and saving only what changed.

    python tests/test_text_fonts.py

These are the guarantees behind "what you typed is what every viewer shows"
and "saving does not disturb what you did not touch".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp" / "py"))

import pymupdf

from pdfstudio import annots, bridge

HERE = Path(__file__).parent


def check(condition: bool, label: str) -> bool:
    print(f"  {'OK  ' if condition else 'FAIL'} {label}")
    return bool(condition)


def text_box(ident, rect, text, page=0, **font):
    return {
        "id": ident, "type": "freetext", "page": page, "rect": rect, "text": text, "contents": text,
        "style": {"fill": None, "width": 0, "opacity": 1,
                  "font": {"family": "gothic", "size": 12, "color": "#1c1f26", "align": "left", **font}},
    }


def fonts_of(doc: pymupdf.Document) -> str:
    return " ".join(doc.xref_object(x) for x in range(1, doc.xref_length()) if "/BaseFont" in doc.xref_object(x))


def main() -> int:
    failures = 0

    # ------------------------------------------------------------ Japanese text
    doc = pymupdf.open()
    doc.new_page()
    items = [
        text_box("t1", [50, 50, 300, 75], "直す・骨・海 請求書 ABC 123"),
        text_box("t2", [50, 90, 300, 115], "明朝体のテスト", family="mincho"),
        text_box("t3", [50, 130, 300, 155], "太字のテスト", bold=True),
        text_box("t4", [50, 170, 300, 195], "教科書体", family="klee"),
    ]
    baseline: dict = {}
    annots.write_document(doc, items, baseline)
    data = doc.tobytes(garbage=3, deflate=True)
    saved = pymupdf.open("pdf", data)
    names = fonts_of(saved)
    failures += not check("BIZUDPGothic" in names and "BIZUDPMincho" in names and "KleeOne" in names,
                          "選んだ日本語フォントがPDFに埋め込まれる")
    failures += not check("Bold" in names, "太字は太字のフォントで入る")
    failures += not check("/Song" not in names and "Droid" not in names,
                          "中国語フォント（Song / Droid Fallback）が使われない")
    failures += not check(len(data) < 200_000, f"フォントはサブセット化される（{len(data) // 1024} KB）")
    failures += not check("請求書" in saved[0].get_text() and "ABC 123" in saved[0].get_text(),
                          "書いた文字を他のソフトで検索・コピーできる")
    pix = saved[0].get_pixmap(clip=pymupdf.Rect(50, 50, 300, 75), dpi=96)
    dark = sum(1 for i in range(0, len(pix.samples), pix.n) if pix.samples[i] < 128)
    failures += not check(dark > 150, f"文字が実際に描かれている（暗い画素 {dark}）")

    again = annots.read_document(saved)
    by_id = {a["id"]: a for a in again}
    failures += not check(by_id["t2"]["style"]["font"]["family"] == "mincho"
                          and by_id["t3"]["style"]["font"]["bold"] is True,
                          "開き直してもフォントと太字の指定が残る")
    failures += not check(by_id["t1"]["text"] == items[0]["text"], "開き直しても本文が同じ")

    # ------------------------------------------------------------ browser line breaks are honoured
    doc = pymupdf.open()
    doc.new_page()
    laid = text_box("w1", [50, 50, 200, 120], "一行目と二行目")
    laid["layout"] = {"lines": [{"x": 3, "y": 14, "t": "一行目と"}, {"x": 3, "y": 60, "t": "二行目"}]}
    annots.write_document(doc, [laid], {})
    saved = pymupdf.open("pdf", doc.tobytes())
    pix = saved[0].get_pixmap()
    words = saved[0].get_text("words")
    rows = sorted({round(w[1]) for w in words})
    failures += not check(len(rows) == 2 and rows[1] - rows[0] > 30,
                          f"ブラウザが決めた改行位置のまま描かれる（行の位置 {rows}）")
    del pix

    # ------------------------------------------------------------ rotated pages
    doc = pymupdf.open()
    page = doc.new_page()
    page.set_rotation(90)
    annots.write_document(doc, [text_box("r1", [100, 100, 300, 125], "回転ページ")], {})
    saved = pymupdf.open("pdf", doc.tobytes())
    view = saved[0].get_pixmap(clip=pymupdf.Rect(100, 100, 300, 125) * saved[0].derotation_matrix)
    box = [w for w in saved[0].get_text("words")]
    failures += not check(bool(box), "回転したページにも文字が入る")
    del view

    # ------------------------------------------------------------ only what changed is rewritten
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40))
    pix.clear_with(200)
    page.add_stamp_annot(pymupdf.Rect(400, 600, 460, 660), stamp=pix.tobytes("png"))
    page.add_file_annot((300, 300), b"attached", "note.txt")
    doc = pymupdf.open("pdf", doc.tobytes())
    items = annots.read_document(doc)
    baseline = annots.baseline_of(items)
    kinds = [a["type"] for a in items]
    failures += not check(kinds == ["image"], f"他ソフトの画像スタンプは画像として読める（{kinds}）")
    failures += not check(str(items[0].get("image", "")).startswith("data:image/png"), "画像スタンプの見た目を取り出せる")

    def snapshot(d):
        # Keep the Page alive while its annotations are read: PyMuPDF frees
        # them with the page, and touching one afterwards crashes.
        first = d[0]
        return {a.xref: d.xref_object(a.xref) for a in first.annots()}

    before = snapshot(doc)
    annots.write_document(doc, items + [text_box("n1", [50, 50, 200, 75], "追加")], baseline)
    after = snapshot(doc)
    failures += not check(all(after.get(x) == before[x] for x in before),
                          "保存しても、触っていない注釈は1バイトも変わらない")
    first = doc[0]
    types = sorted(a.type[1] for a in first.annots())
    failures += not check("FileAttachment" in types, f"扱わない種類の注釈（添付ファイル）を消さない（{types}）")

    moved = [dict(a) for a in annots.read_document(doc)]
    for item in moved:
        if item["type"] == "image":
            item["rect"] = [100, 400, 220, 520]
    annots.write_document(doc, moved, annots.baseline_of(annots.read_document(doc)))
    first = doc[0]
    stamp = next(a for a in first.annots() if a.type[1] == "Stamp")
    failures += not check([round(v) for v in stamp.rect] == [100, 400, 220, 520], "画像は動かしても画像のまま")
    failures += not check(stamp.get_pixmap().width > 10, "動かした画像の見た目が保たれる")

    removed = [a for a in annots.read_document(doc) if a["type"] != "freetext"]
    annots.write_document(doc, removed, annots.baseline_of(annots.read_document(doc)))
    first = doc[0]
    failures += not check(not any(a.type[1] == "FreeText" for a in first.annots()), "消した注釈だけが消える")

    # standard stamp index 0 ("Approved") must survive: 0 is a value, not "unset"
    doc = pymupdf.open()
    doc.new_page()
    annots.write_document(doc, [{"id": "s1", "type": "stamp", "page": 0, "rect": [50, 50, 160, 86], "stampIndex": 0,
                                 "style": {"stroke": "#1b7f3b"}}], {})
    back = annots.read_document(pymupdf.open("pdf", doc.tobytes()))
    failures += not check(back[0]["type"] == "stamp" and back[0].get("stampIndex") == 0,
                          "標準スタンプ（APPROVED）が開き直してもスタンプのまま")

    # ------------------------------------------------------------ undo of document operations
    sample = HERE / "sample.pdf"
    if sample.exists():
        opened = bridge.dispatch("open", {"name": "s.pdf", "data": sample.read_bytes()})["json"]
        ident = opened["id"]
        note = text_box("u1", [60, 520, 300, 545], "元に戻すテスト")
        rotated = bridge.dispatch("pages.action", {"docId": ident, "action": "rotate", "pages": [0],
                                                   "degrees": 90, "annots": [note]})["json"]
        failures += not check(rotated["pages"][0]["rotation"] == 90 and rotated["undoDepth"] == 1, "ページ操作のたびに戻り先が1つ増える")
        undone = bridge.dispatch("undo", {"docId": ident})["json"]
        failures += not check(undone["pages"][0]["rotation"] == 0, "ページ操作を元に戻せる")
        failures += not check(any(a["id"] == "u1" for a in undone["annots"]), "元に戻しても、直前までの書き込みは残る")
        refused = bridge.dispatch("undo", {"docId": ident})
        failures += not check(refused["status"] == 400, "戻り先が無いときは、無いと答える")

    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
