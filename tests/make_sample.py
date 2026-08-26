"""Generate a plain multi-page PDF for interactive testing (no annotations)."""

from __future__ import annotations

from pathlib import Path

import pymupdf

BODY = """
<h2>PDF Studio 動作確認用サンプル</h2>
<p>この文書には注釈が入っていません。ハイライトや手書き、図形を試すための素材です。</p>
<p>機密情報のサンプル: 契約金額は <b>12,340,000円</b>、担当者は 山田太郎 です。
墨消しの動作確認にお使いください。</p>
<p>English text is also included so that word search and whole-word matching can be
checked. The word <b>redaction</b> appears here, and again: redaction.</p>
<p>長い段落の折り返しをまたぐハイライトの確認用テキストです。行をまたいで文字を選択したとき、
複数の矩形として正しく領域が定義され、他のビューアで開いても同じ位置に表示されることを
確かめてください。文字の折り返しに追従することが、テキストマークアップの要件です。</p>
"""


def main() -> None:
    doc = pymupdf.open()
    for index in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_htmlbox(pymupdf.Rect(60, 60, 535, 500), BODY)
        page.insert_text((60, 780), f"- {index + 1} -", fontsize=10, fontname="helv")
    doc.set_toc([[1, "1ページ目", 1], [1, "2ページ目", 2], [1, "3ページ目", 3]])
    # The CJK fallback font embeds whole otherwise; subsetting takes 5 MB to 50 KB.
    doc.subset_fonts()
    out = Path(__file__).parent / "sample.pdf"
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    print(out)


if __name__ == "__main__":
    main()
