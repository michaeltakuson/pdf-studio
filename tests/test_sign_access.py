"""Phase 5 checks: signatures and accessibility.

The important property tested here is honesty: a drawn signature must not be
presented as a digital one, and the accessibility audit must report what is
actually missing.

Run with: python -m tests.test_sign_access   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from backend import accessibility, signing

HERE = Path(__file__).parent


def check(condition, label):
    print(("  OK   " if condition else "  FAIL ") + label)
    return bool(condition)


def fresh() -> pymupdf.Document:
    return pymupdf.open(HERE / "sample.pdf")


def main() -> int:
    failures = 0

    # ---------------------------------------------------------------- signing
    doc = fresh()
    page = doc[0]

    strokes = [{"pts": [[60 + i * 4, 600 + (i % 5) * 6] for i in range(30)]}]
    result = signing.place_drawn(page, [60, 590, 200, 630], strokes)
    failures += not check(result["type"] == "drawn", "手書き署名を置ける")

    signing.place_typed(page, [220, 590, 400, 620], "山田太郎", size=18)
    saved = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    failures += not check("山田太郎" in saved[0].get_text(),
                          "タイプ署名の日本語がページに入る")
    ink = [a for a in saved[0].annots() if a.type[1] == "Ink"]
    failures += not check(len(ink) == 1, "手書き署名がInk注釈として保存される")
    saved.close()

    state = signing.digital_signature_state(doc)
    failures += not check(state["canSign"] is False,
                          "電子証明書による署名は作成できないと明示している")
    failures += not check("改ざん検知の効力はありません" in state["note"],
                          "見た目だけの署名であることを説明している")

    signing.add_signature_field(doc[1], [60, 600, 260, 640], "承認者")
    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    state = signing.digital_signature_state(doc)
    failures += not check(len(state["fields"]) == 1,
                          f"署名欄を作れる ({len(state['fields'])} 個)")
    failures += not check(state["fields"][0]["name"] == "承認者",
                          "署名欄の日本語名が保持される")
    failures += not check(state["signed"] == 0, "未署名の欄は未署名と報告される")

    block = signing.signature_block("山田太郎", reason="内容確認のため")
    failures += not check("山田太郎" in block and "理由: 内容確認のため" in block,
                          "署名ブロックに氏名・日時・理由が入る")

    certified = signing.certify(doc, allow="fill")
    failures += not check(certified["enforced"] is False,
                          "証明はあくまで宣言であると報告する")
    doc.close()

    # ---------------------------------------------------------------- audit
    doc = fresh()
    report = accessibility.audit(doc)
    failures += not check(not report["tagged"], "タグの無い文書をタグ無しと判定する")
    ids = {issue["id"] for issue in report["issues"]}
    failures += not check("tags" in ids, "タグが無いことを問題として挙げる")
    failures += not check("lang" in ids, "言語未指定を問題として挙げる")
    failures += not check(not report["passed"], "問題があるとき passed は False")
    tag_issue = next(i for i in report["issues"] if i["id"] == "tags")
    failures += not check("タグに依存" in tag_issue["detail"],
                          "なぜ問題なのかを説明している")
    failures += not check(bool(tag_issue["fix"]), "どうすれば直るかを示している")

    # ---------------------------------------------------------------- autotag
    tagged = accessibility.autotag(doc, language="ja-JP")
    failures += not check(tagged["tagged"], "自動タグ付けが実行できる")
    failures += not check(tagged["elements"] > 3,
                          f"本文がタグ要素になる ({tagged['elements']} 個)")
    failures += not check(tagged["headings"] >= 1,
                          f"文字サイズから見出しを推定する ({tagged['headings']} 個)")

    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    failures += not check(accessibility.is_tagged(doc), "保存後もタグ構造が残る")
    failures += not check(accessibility.has_language(doc) == "ja-JP",
                          f"言語が保存される ({accessibility.has_language(doc)})")

    after = accessibility.audit(doc)
    ids_after = {issue["id"] for issue in after["issues"]}
    failures += not check("tags" not in ids_after, "タグ付け後はタグの指摘が消える")
    failures += not check("lang" not in ids_after, "言語設定後は言語の指摘が消える")
    doc.close()

    # ---------------------------------------------------------------- alt text
    doc = fresh()
    page = doc[0]
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
    pixmap.set_rect(pixmap.irect, (200, 120, 60))
    page.insert_image(pymupdf.Rect(400, 600, 460, 660), pixmap=pixmap)
    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))

    report = accessibility.audit(doc)
    alt_issue = next((i for i in report["issues"] if i["id"] == "alt"), None)
    failures += not check(alt_issue is not None, "代替テキストの無い画像を指摘する")

    images = accessibility.image_alt_texts(doc)
    failures += not check(len(images) >= 1, f"画像を検出できる ({len(images)} 個)")
    accessibility.set_alt_text(doc, images[0]["xref"], "会社のロゴ")
    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    restored = accessibility.image_alt_texts(doc)
    failures += not check(any(i["alt"] == "会社のロゴ" for i in restored),
                          f"代替テキスト(日本語)が保存される ({[i['alt'] for i in restored]})")
    failures += not check(
        not any(i["id"] == "alt" for i in accessibility.audit(doc)["issues"]),
        "代替テキストを入れると指摘が消える",
    )
    doc.close()

    # ---------------------------------------------------------------- order
    doc = fresh()
    blocks = accessibility.reading_order(doc, 0)
    failures += not check(len(blocks) >= 3, f"読み上げ順序を出せる ({len(blocks)} ブロック)")
    failures += not check(blocks[0]["order"] == 1, "順序が1始まり")
    failures += not check("PDF Studio" in blocks[0]["text"],
                          f"最初に読まれるのは見出し ({blocks[0]['text'][:24]!r})")
    doc.close()

    # ---------------------------------------------------------------- scanned
    scan = pymupdf.open()
    scan_page = scan.new_page(width=300, height=200)
    scan_page.insert_image(scan_page.rect, pixmap=pixmap)
    scan = pymupdf.open("pdf", scan.tobytes(garbage=3, deflate=True))
    scan_report = accessibility.audit(scan)
    failures += not check(any(i["id"] == "scanned" for i in scan_report["issues"]),
                          "画像だけのページをOCR必要として指摘する")
    scan.close()

    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
