"""Rotation checks: markup must stay on the content it was placed on.

PyMuPDF works in the page as authored and ignores /Rotate; the viewer draws the
page rotated. Those are two frames, and a page can be rotated either because
the user rotated it or because the file arrived that way. The test that matters
is behavioural: put a mark on a phrase, rotate, and the mark is still on it.

Run with: python -m tests.test_rotation   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from backend import annots, common, pages

HERE = Path(__file__).parent
NEEDLE = "12,340,000"


def check(condition, label, detail=""):
    print(("  OK   " if condition else "  FAIL ") + label + (f" : {detail}" if detail else ""))
    return bool(condition)


def text_under(page: pymupdf.Page, view_rect) -> str:
    """What the page says beneath a view-space rectangle."""
    return page.get_textbox(pymupdf.Rect(common.rect_to_page(page, view_rect))).strip()


def rotated_copy(source: Path, degrees: int) -> pymupdf.Document:
    doc = pymupdf.open(source)
    page = doc[0]
    page.set_rotation(degrees)
    return pymupdf.open("pdf", doc.tobytes())


def main() -> int:
    failures = 0
    sample = HERE / "sample.pdf"

    # ---------------------------------------------------------------- frames
    for degrees in (0, 90, 180, 270):
        doc = rotated_copy(sample, degrees)
        page = doc[0]
        hit = page.search_for(NEEDLE)[0]
        view = common.rect_to_view(page, hit)
        inside = (
            -1 <= view[0] <= page.rect.width + 1
            and -1 <= view[1] <= page.rect.height + 1
            and view[2] <= page.rect.width + 1
            and view[3] <= page.rect.height + 1
        )
        failures += not check(inside, f"{degrees}°: 表示座標がページ内に収まる",
                              f"rect={[round(v) for v in view]} page={round(page.rect.width)}x{round(page.rect.height)}")
        back = common.rect_to_page(page, view)
        failures += not check(all(abs(a - b) < 0.01 for a, b in zip(back, list(hit))),
                              f"{degrees}°: 変換が往復して元に戻る",
                              f"{[round(v) for v in hit]} -> {[round(v) for v in back]}")
        doc.close()

    # ---------------------------------------------------------------- markup
    for degrees in (0, 90, 180, 270):
        doc = rotated_copy(sample, degrees)
        page = doc[0]
        # What the browser would receive from /search for this phrase.
        hit = page.search_for(NEEDLE, quads=True)[0]
        found = common.to_view(page, {
            "rect": list(hit.rect),
            "quads": [[c for p in (hit.ul, hit.ur, hit.ll, hit.lr) for c in p]],
        })

        # What the browser would send back to save a highlight there.
        annots.write_document(doc, [{
            "id": "h1", "page": 0, "type": "highlight",
            "rect": found["rect"], "quads": found["quads"],
            "style": {"stroke": "#ffe14d", "opacity": 0.45},
            "author": "回転テスト",
        }])
        doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
        page = doc[0]

        marks = list(page.annots())
        failures += not check(len(marks) == 1, f"{degrees}°: 注釈が1件保存される")
        covered = page.get_textbox(marks[0].rect).strip() if marks else ""
        failures += not check(NEEDLE in covered,
                              f"{degrees}°: 保存後もマークが同じ文字を覆っている",
                              repr(covered[:24]))

        # And reading it back must land on the same text again.
        restored = annots.read_page(page)[0]
        failures += not check(NEEDLE in text_under(page, restored["rect"]),
                              f"{degrees}°: 読み戻した座標も同じ文字を指す",
                              f"rect={[round(v) for v in restored['rect']]}")
        doc.close()

    # ---------------------------------------------------------------- stable
    # Rotating a page must not move markup relative to the content.
    doc = pymupdf.open(sample)
    page = doc[0]
    hit = page.search_for(NEEDLE)[0]
    annots.write_document(doc, [{
        "id": "s1", "page": 0, "type": "square", "rect": list(hit),
        "style": {"stroke": "#e0403a", "fill": None, "width": 1},
    }])
    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))

    for turn in range(4):
        pages.rotate(doc, [0], 90)
        doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
        page = doc[0]
        item = annots.read_page(page)[0]
        failures += not check(NEEDLE in text_under(page, item["rect"]),
                              f"回転{(turn + 1) * 90}°後もマークが同じ文字の上にある",
                              f"覆っている文字={text_under(page, item['rect'])[:20]!r}")
    doc.close()

    # ---------------------------------------------------------------- drawing
    # What the browser sends when the user draws on a rotated page.
    for degrees in (90, 270):
        doc = rotated_copy(sample, degrees)
        page = doc[0]
        drawn = [40, 60, 240, 160]  # as dragged on screen
        annots.write_document(doc, [{
            "id": "d1", "page": 0, "type": "square", "rect": drawn,
            "style": {"stroke": "#e0403a", "fill": None, "width": 1.5},
        }])
        doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
        page = doc[0]
        item = annots.read_page(page)[0]
        failures += not check(
            all(abs(a - b) < 0.6 for a, b in zip(item["rect"], drawn)),
            f"{degrees}°: 描いた位置がそのまま読み戻せる",
            f"{drawn} -> {[round(v) for v in item['rect']]}",
        )
        # What PyMuPDF stores is in page space, so it has to be checked against
        # the page's authored box — page.rect is the rotated one and comparing
        # against it would be the very frame mix-up this file exists to catch.
        stored = list(page.annots())[0].rect
        authored = page.cropbox
        failures += not check(
            authored.contains(stored),
            f"{degrees}°: 保存された矩形がページ内にある",
            f"{[round(v) for v in stored]} in {round(authored.width)}x{round(authored.height)}",
        )
        doc.close()

    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
