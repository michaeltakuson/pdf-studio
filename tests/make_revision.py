"""Generate a revised copy of sample.pdf, for testing document comparison."""

from __future__ import annotations

from pathlib import Path

import pymupdf

HERE = Path(__file__).parent


def main() -> None:
    doc = pymupdf.open(HERE / "sample.pdf")
    doc[0].insert_htmlbox(
        pymupdf.Rect(60, 560, 520, 640),
        "<p style='font-size:14pt'>【改訂】この段落は第2版で追加されました。</p>",
    )
    doc[1].insert_htmlbox(
        pymupdf.Rect(60, 300, 520, 340),
        "<p style='font-size:12pt'>2ページ目の変更点</p>",
    )
    doc.subset_fonts()
    out = HERE / "sample_rev2.pdf"
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    print(out)


if __name__ == "__main__":
    main()
