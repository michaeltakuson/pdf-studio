"""Layer ②/③ checks: page operations, redaction, text editing, optimisation.

The point of most of these is the source material's core warning — that hiding
is not deleting. Where an operation claims to remove something, the test proves
the content is really gone.

Run with: python -m tests.test_pages   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp" / "py"))

import pymupdf

from pdfstudio import content, pages

HERE = Path(__file__).parent

SECRET = "契約金額は 12,340,000円"


def check(condition, label):
    print(("  OK   " if condition else "  FAIL ") + label)
    return bool(condition)


def fresh() -> pymupdf.Document:
    doc = pymupdf.open(HERE / "sample.pdf")
    return doc


def _scanned_pdf() -> pymupdf.Document:
    """A real page turned into pure pixels — the situation OCR exists for.

    Rasterising the project's own sample keeps this close to a genuine scan;
    a two-line synthetic image is not representative and gives erratic results.
    """
    source = pymupdf.open(HERE / "sample.pdf")
    pixmap = source[0].get_pixmap(dpi=200)
    rect = source[0].rect
    source.close()

    scanned = pymupdf.open()
    page = scanned.new_page(width=rect.width, height=rect.height)
    page.insert_image(page.rect, pixmap=pixmap)
    # Round-trip through bytes so no text sneaks through from the generator.
    data = scanned.tobytes(garbage=3, deflate=True)
    scanned.close()
    return pymupdf.open("pdf", data)


def main() -> int:
    if not (HERE / "sample.pdf").exists():
        print("先に python tests\\make_sample.py を実行してください")
        return 1

    failures = 0

    # ---------------------------------------------------------------- pages
    doc = fresh()
    original = doc.page_count
    pages.rotate(doc, [0], 90)
    failures += not check(doc[0].rotation == 90, "ページを回転できる")

    pages.duplicate(doc, [0])
    failures += not check(doc.page_count == original + 1, "ページを複製できる")

    pages.delete(doc, [1])
    failures += not check(doc.page_count == original, "ページを削除できる")

    first_text = doc[0].get_text()[:20]
    pages.move(doc, 0, doc.page_count - 1)
    failures += not check(doc[doc.page_count - 1].get_text()[:20] == first_text,
                          "ページを並べ替えられる")

    pages.insert_blank(doc, 0, 595, 842)
    failures += not check(doc.page_count == original + 1 and not doc[0].get_text().strip(),
                          "白紙ページを挿入できる")

    try:
        pages.delete(doc, list(range(doc.page_count)))
        failures += not check(False, "全ページ削除は拒否される")
    except ValueError:
        failures += not check(True, "全ページ削除は拒否される")

    extracted = pages.extract(doc, [1])
    single = pymupdf.open("pdf", extracted)
    failures += not check(single.page_count == 1, "ページを抽出できる")
    single.close()
    doc.close()

    # ---------------------------------------------------------------- crop
    doc = fresh()
    full_area = doc[0].rect.get_area()
    pages.crop(doc, [0], [100, 100, 400, 500])
    failures += not check(doc[0].rect.get_area() < full_area, "トリミングで表示範囲が縮む")
    # The warning that matters: cropped-away text is still in the file.
    data = doc.tobytes(garbage=3, deflate=True)
    cropped = pymupdf.open("pdf", data)
    pages.reset_crop(cropped, [0])
    failures += not check("PDF Studio" in cropped[0].get_text(),
                          "トリミングは消去ではない（枠を戻すと本文が復活する）")
    cropped.close()
    doc.close()

    # ---------------------------------------------------------------- overlays
    doc = fresh()
    pages.watermark(doc, "社外秘", colour="#c8c8c8", size=54)
    failures += not check("社外秘" in doc[0].get_text(), "透かしがページに入る")

    pages.header_footer(doc, header="レビュー用", footer="- {page} / {pages} -")
    text = doc[1].get_text()
    failures += not check("レビュー用" in text, "ヘッダーが入る")
    failures += not check("- 2 / 3 -" in text, "フッターのページ番号が展開される")

    pages.bates(doc, prefix="ABC-", start=1, digits=5)
    failures += not check("ABC-00003" in doc[2].get_text(), "ベイツ番号が通し番号になる")
    doc.close()

    # ---------------------------------------------------------------- redaction
    doc = fresh()
    failures += not check(SECRET in doc[0].get_text(), "墨消し前は機密文字列が抽出できる")

    marked = pages.search_and_mark_redactions(doc, "12,340,000", overlay="［非開示］")
    failures += not check(marked >= 1, f"検索して墨消しを指定できる ({marked} 箇所)")

    # Before applying, the mark alone must not have removed anything.
    failures += not check("12,340,000" in doc[0].get_text(),
                          "指定しただけでは本文はまだ残っている")

    report = pages.apply_redactions(doc)
    failures += not check(report["applied"] >= 1, "墨消しを適用できる")
    failures += not check("12,340,000" not in doc[0].get_text(),
                          "適用後は本文から実際に消えている")

    # And it must stay gone after a full save/reload.
    saved = pymupdf.open("pdf", doc.tobytes(garbage=4, deflate=True, clean=True))
    failures += not check("12,340,000" not in saved[0].get_text(),
                          "保存し直しても復活しない")
    # The redaction code drawn on top must survive too. Japanese needs the CJK
    # face: with the default Helvetica it silently vanishes.
    failures += not check("非開示" in saved[0].get_text(),
                          f"墨消しコード（日本語）が黒塗りの上に描かれる"
                          f" ({saved[0].get_text()[:0]!r})")
    saved.close()
    doc.close()

    # ---------------------------------------------------------------- scrub
    doc = fresh()
    doc.set_metadata({"author": "秘密の作成者", "title": "内部資料"})
    pages.scrub(doc)
    meta = doc.metadata or {}
    failures += not check(not (meta.get("author") or ""), "scrubでメタデータが消える")
    doc.close()

    # ---------------------------------------------------------------- text edit
    doc = fresh()
    blocks = content.find_text_blocks(doc[0])
    failures += not check(len(blocks) > 3, f"編集可能なテキストを検出できる ({len(blocks)} 箇所)")

    target = next((b for b in blocks if "12,340,000" in b["text"]), None)
    if target:
        content.replace_text(doc[0], target["rect"], "999,999,999",
                             size=target["size"], colour=target["colour"])
        after = doc[0].get_text()
        failures += not check("999,999,999" in after, "本文テキストを差し替えられる")
        failures += not check("12,340,000" not in after, "差し替え後、元の文字は残らない")
    else:
        failures += not check(False, "差し替え対象のテキストが見つかった")
    doc.close()

    doc = fresh()
    replaced = content.search_replace(doc, "redaction", "REDACTION")
    failures += not check(replaced >= 2, f"検索と置換が全ページに効く ({replaced} 箇所)")
    failures += not check("REDACTION" in doc[0].get_text(), "置換後の文字が入っている")
    doc.close()

    # ---------------------------------------------------------------- optimise
    # sample.pdf is already subsetted, so build a deliberately wasteful file:
    # a fully embedded CJK face is the classic multi-megabyte culprit.
    bloated = pymupdf.open()
    page = bloated.new_page(width=595, height=842)
    page.insert_htmlbox(pymupdf.Rect(50, 50, 545, 400), "<p>最適化の確認用テキストです。</p>")
    report = pages.optimise(bloated)
    failures += not check(report["saved"] > 1_000_000,
                          f"最適化で大幅に小さくなる ({report['before']} -> {report['after']})")
    bloated.close()

    # ---------------------------------------------------------------- security
    doc = fresh()
    options = pages.save_options(user_password="pw1234", permissions={"copy": False})
    data = doc.tobytes(garbage=3, deflate=True, **options)
    doc.close()
    locked = pymupdf.open("pdf", data)
    failures += not check(locked.needs_pass, "パスワードで保護できる")
    failures += not check(locked.authenticate("pw1234") > 0, "正しいパスワードで開ける")
    failures += not check(not locked.permissions & pymupdf.PDF_PERM_COPY,
                          "コピー禁止の権限が効いている")
    locked.close()

    # ---------------------------------------------------------------- OCR text layer
    # Recognition itself runs in the browser (tesseract.js). What the engine
    # does is take the recognised words and lay them under the page image, so
    # that is what is checked here.
    from pdfstudio import bridge

    scan = _scanned_pdf()
    opened = bridge.dispatch("open", {"name": "scan.pdf", "data": scan.tobytes()})["json"]
    scan.close()
    result = bridge.dispatch("ocr.apply", {"docId": opened["id"], "pages": [{"page": 0, "words": [
        {"text": "契", "rect": [100, 100, 112, 114]}, {"text": "約", "rect": [112, 100, 124, 114]},
        {"text": "redaction", "rect": [100, 130, 160, 144]},
    ]}]})
    failures += not check(result["status"] == 200 and result["json"]["characters"] == 11,
                          f"認識結果を埋め込める ({result['json'].get('characters')} 文字)")
    saved = pymupdf.open("pdf", bridge.dispatch("download", {"docId": opened["id"]})["data"])
    failures += not check("redaction" in saved[0].get_text(), "埋め込んだ文字が抽出できる")
    failures += not check(bool(content.search_relaxed(saved[0], "契約")), "埋め込んだ日本語を検索できる")
    hit = saved[0].search_for("redaction")
    failures += not check(bool(hit) and abs(hit[0].x0 - 100) < 4 and abs(hit[0].x1 - 160) < 6,
                          f"検索結果が画像上の位置に重なる ({tuple(round(v) for v in hit[0]) if hit else None})")
    saved.close()

    # ---------------------------------------------------------------- page layout
    doc = fresh()
    before = doc[0].rect
    pages.add_margins(doc, None, right=170)
    failures += not check(abs(doc[0].rect.width - before.width - 170) < 0.5 and doc[0].rect.height == before.height,
                          f"余白を足すと用紙だけが広がる ({before.width:.0f} -> {doc[0].rect.width:.0f})")
    failures += not check("PDF Studio" in doc[0].get_text(), "余白を足しても本文はそのまま")
    count = doc.page_count
    sheets = pymupdf.open("pdf", pages.nup(doc, 4))
    failures += not check(sheets.page_count == -(-count // 4), f"4面付けで {count} ページが {sheets.page_count} 枚になる")
    sheets.close()
    failures += not check(pages.parse_ranges("1-2, 3-", 3) == [[0, 1], [2]], "ページ範囲を読める")
    failures += not check(pages.parse_ranges("１〜２、３", 3) == [[0, 1], [2]], "全角の数字・記号でも読める")
    try:
        pages.parse_ranges("9", 3)
        failures += not check(False, "範囲外のページを拒否する")
    except ValueError:
        failures += not check(True, "範囲外のページを拒否する")
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(pages.split(doc, "doc", every=2))).namelist()
    failures += not check(len(names) == -(-count // 2), f"2ページごとに分割できる ({names})")
    name, media, data = pages.to_images(doc, "doc", [0], dpi=72)
    failures += not check(media == "image/png" and data[:4] == b"\x89PNG", "ページを画像にできる")
    doc.close()

    # ---------------------------------------------------------------- editing body text
    doc = fresh()
    page = doc[0]
    line = next(l for l in content.find_text_lines(page) if "12,340,000" in l["text"])
    content.replace_text(page, line["rect"], "契約金額は 99,000円、担当者は 直井 です。",
                         size=line["size"], origin=line["origin"], serif=line["serif"])
    text = page.get_text()
    failures += not check("99,000" in text and "12,340,000" not in text, "本文の行を書き換えられる（元の文字は残らない）")
    failures += not check("動作確認用サンプル" in text, "書き換えた行以外はそのまま")
    fonts = {f[3] for f in page.get_fonts(full=True)}
    failures += not check(any("BIZ" in f for f in fonts), f"書き換えた文字は同梱の日本語フォントで入る ({sorted(fonts)})")
    doc.close()

    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
