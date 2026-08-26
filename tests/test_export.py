"""Export checks: XFDF, CSV, Markdown and the summary PDF.

Run with: python -m tests.test_export   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from backend import annots, export

HERE = Path(__file__).parent


def check(condition, label):
    print(("  OK   " if condition else "  FAIL ") + label)
    return bool(condition)


def main() -> int:
    source = HERE / "roundtrip.pdf"
    if not source.exists():
        print("先に python -m tests.test_roundtrip を実行してください")
        return 1

    failures = 0
    doc = pymupdf.open(source)
    items = annots.read_document(doc)
    failures += not check(len(items) >= 9, f"元データを読み込めた ({len(items)} 件)")

    # ---------------------------------------------------------------- CSV
    csv_text = export.to_csv(doc, items)
    rows = csv_text.strip().splitlines()
    failures += not check(len(rows) == len(items) + 1, f"CSVの行数が注釈数+見出し ({len(rows)})")
    failures += not check("ハイライト" in csv_text, "CSVに種類の日本語ラベルが入る")
    failures += not check("Redact me please" in csv_text, "CSVにマークアップ対象の本文が入る")
    failures += not check("承諾" in csv_text, "CSVにレビュー状態が入る")

    # ---------------------------------------------------------------- Markdown
    md = export.to_markdown(doc, items, "roundtrip.pdf", {"#ffe14d": "重要"})
    failures += not check("# roundtrip.pdf" in md, "Markdownに見出しが付く")
    failures += not check("## 1 ページ" in md, "Markdownがページ単位に分かれる")
    failures += not check("> Redact me please" in md, "Markdownに引用として対象テキストが入る")
    failures += not check("#重要" in md, "色が意味タグとして保持される")
    failures += not check("**佐藤**: ここは修正済みです" in md, "Markdownに返信が入る")

    # ---------------------------------------------------------------- XFDF
    xfdf = export.to_xfdf(doc, items, "roundtrip.pdf")
    (HERE / "_annots.xfdf").write_text(xfdf, encoding="utf-8")
    size_kb = len(xfdf.encode()) / 1024
    pdf_kb = source.stat().st_size / 1024
    failures += not check(size_kb < pdf_kb, f"XFDFはPDFより小さい ({size_kb:.1f}KB < {pdf_kb:.0f}KB)")
    failures += not check("<highlight" in xfdf, "XFDFにhighlight要素がある")
    failures += not check('state="Accepted"' in xfdf, "XFDFにレビュー状態が入る")
    failures += not check("inklist=" in xfdf, "XFDFに手書きの座標が入る")

    restored = export.from_xfdf(doc, xfdf)
    kinds = {item["type"] for item in restored}
    failures += not check("highlight" in kinds, "XFDFからハイライトを読み戻せる")
    failures += not check("ink" in kinds, "XFDFから手書きを読み戻せる")
    failures += not check("square" in kinds, "XFDFから図形を読み戻せる")

    hl = next((i for i in restored if i["type"] == "highlight"), {})
    original = next((i for i in items if i["type"] == "highlight"), {})
    same_place = (
        hl.get("rect")
        and original.get("rect")
        and all(abs(a - b) < 1 for a, b in zip(hl["rect"], original["rect"]))
    )
    failures += not check(same_place, f"XFDF往復で座標が保たれる ({hl.get('rect')})")
    failures += not check(hl.get("state") == "accepted", "XFDF往復でレビュー状態が保たれる")

    # Re-importing into a fresh copy must produce real annotations again.
    fresh = pymupdf.open(source)
    written = annots.write_document(fresh, restored)
    failures += not check(written == len(restored), f"XFDF由来の注釈を書き戻せる ({written} 件)")
    fresh.close()

    # ---------------------------------------------------------------- summary
    data = export.summary_pdf(doc, items, "roundtrip.pdf")
    out = HERE / "_summary.pdf"
    out.write_bytes(data)
    summary = pymupdf.open(out)
    text = "\n".join(page.get_text() for page in summary)
    failures += not check(summary.page_count >= 1, "サマリーPDFが生成された")
    failures += not check(len(data) < 500 * 1024,
                          f"サマリーPDFのフォントがサブセット化されている ({len(data) / 1024:.0f}KB)")
    failures += not check("注釈一覧" in text, "サマリーに見出しがある")
    failures += not check("ここは修正済みです" in text, "サマリーに返信が載る")
    failures += not check("承諾" in text, "サマリーにレビュー状態が載る")
    summary.close()

    doc.close()
    for path in (HERE / "_annots.xfdf", HERE / "_summary.pdf"):
        path.unlink(missing_ok=True)
    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
