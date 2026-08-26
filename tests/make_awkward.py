"""Build the awkward files a PDF tool actually meets in the wild.

Opening a file is the one operation every user performs, so it has to cope with
files that are damaged, locked, empty, huge, or oddly shaped rather than failing
in a way that looks like the app is broken.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
OUT = HERE / "awkward"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    made = []

    # 1. password-protected
    doc = pymupdf.open(HERE / "sample.pdf")
    doc.save(
        OUT / "locked.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret", owner_pw="ownersecret",
        permissions=pymupdf.PDF_PERM_PRINT,
    )
    doc.close()
    made.append("locked.pdf (開くのに secret が必要)")

    # 2. truncated / damaged
    data = (HERE / "sample.pdf").read_bytes()
    (OUT / "damaged.pdf").write_bytes(data[: int(len(data) * 0.75)])
    made.append("damaged.pdf (末尾を切り落とした)")

    # 3. not a PDF at all
    (OUT / "notapdf.pdf").write_text("これはPDFではありません\n", encoding="utf-8")
    made.append("notapdf.pdf (中身がテキスト)")

    # 4. a single blank page, no text
    blank = pymupdf.open()
    blank.new_page(width=595, height=842)
    blank.save(OUT / "blank.pdf")
    blank.close()
    made.append("blank.pdf (白紙1ページ)")

    # 5. mixed page sizes and rotations in one file
    mixed = pymupdf.open()
    for width, height, rotation in (
        (595, 842, 0), (842, 595, 0), (595, 842, 90), (1191, 842, 270), (300, 300, 180),
    ):
        page = mixed.new_page(width=width, height=height)
        page.insert_htmlbox(
            pymupdf.Rect(20, 20, width - 20, min(height - 20, 200)),
            f"<p style='font-size:18pt'>{width}x{height} rot{rotation} この行を目印にします</p>",
        )
        page.set_rotation(rotation)
    mixed.subset_fonts()
    mixed.save(OUT / "mixed.pdf", garbage=4, deflate=True)
    mixed.close()
    made.append("mixed.pdf (ページ寸法と回転がばらばら)")

    # 6. many pages, to check responsiveness
    many = pymupdf.open()
    for index in range(120):
        page = many.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {index + 1}", fontsize=24)
    many.subset_fonts()
    many.save(OUT / "many.pdf", garbage=4, deflate=True)
    many.close()
    made.append("many.pdf (120ページ)")

    for name in made:
        print(" ", name)
    print(OUT)


if __name__ == "__main__":
    main()
