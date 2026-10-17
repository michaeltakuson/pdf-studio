"""Everyday jobs for office work and study that sit on top of the page tools.

- grayscale: one call to MuPDF's recolor, which converts text, drawings,
  pictures and markup alike, so the file stays text (searchable, sharp).
- blank pages: a scan of a double-sided stack is half blank backs. A page
  counts as blank when, rendered small, almost nothing on it is darker than
  paper; the edges are ignored because scanners shade them.
- personal information: telephone numbers, e-mail addresses, postcodes,
  My Number and card numbers found in the page text, with their boxes, so
  they can be marked for redaction. My Number and card numbers are checked
  by their check digits, which keeps invoice and order numbers out.
- booklet: pages laid two to a sheet in saddle-stitch order, to print on
  both sides, fold and staple.
- compress to a size: the gentlest of a ladder of settings that gets under
  a size limit (an upload form, a mail server).
"""

from __future__ import annotations

import re

import pymupdf

from .common import RAW_TEXT_ONLY, TEXT_ONLY

# ------------------------------------------------------------------ grayscale


def grayscale(doc: pymupdf.Document) -> int:
    """Turn every colour in the document into a shade of grey."""
    doc.recolor(components=1)
    return doc.page_count


# ------------------------------------------------------------------ blank pages


def blank_pages(doc: pymupdf.Document, *, sensitivity: str = "normal") -> list[int]:
    """Pages with (practically) nothing on them.

    ``sensitivity``: "strict" counts a page as blank only when it is clean;
    "loose" also takes pages with a little show-through or a stray mark.
    """
    limit = {"strict": 0.0004, "normal": 0.0015, "loose": 0.004}.get(sensitivity, 0.0015)
    found = []
    for index in range(doc.page_count):
        page = doc[index]
        if page.get_text("text", flags=TEXT_ONLY).strip():
            continue
        rect = page.rect
        if rect.is_empty:
            continue
        # About 30 pixels per inch: enough to see a line of text, fast on a long scan.
        zoom = 30 / 72
        inset = pymupdf.Rect(rect.x0 + rect.width * 0.05, rect.y0 + rect.height * 0.05,
                             rect.x1 - rect.width * 0.05, rect.y1 - rect.height * 0.05)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY,
                              clip=inset, alpha=False, annots=True)
        samples = pix.samples
        if not samples:
            continue
        # Paper is not pure white on a scan: compare with the page's own background.
        histogram = [0] * 256
        for value in samples:
            histogram[value] += 1
        paper = max(range(256), key=histogram.__getitem__)
        dark = sum(histogram[: max(0, paper - 60)])
        if dark / len(samples) <= limit:
            found.append(index)
    return found


# ------------------------------------------------------------------ personal information

# Full-width digits and the many dashes Japanese text uses, mapped one-to-one
# so that positions in the normalised text are positions in the original.
_FOLD = str.maketrans(
    "０１２３４５６７８９＠．－―‐ー−（）＋　",
    "0123456789@.-----()+ ",
)

_PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "phone": re.compile(
        r"(?<![\d-])(?:\+81[- ]?|\(?0)\d{1,4}(?:[- ]|\)|-?\()?\d{1,4}(?:[- ]|\))?\d{3,4}(?![\d-])"
    ),
    "postal": re.compile(r"(?:〒\s?)?(?<![\d-])\d{3}-\d{4}(?![\d-])"),
    "mynumber": re.compile(r"(?<![\d-])\d{4}[- ]?\d{4}[- ]?\d{4}(?![\d-])"),
    "card": re.compile(r"(?<![\d-])(?:\d{4}[- ]?){3}\d{1,4}(?![\d-])"),
}

PII_LABELS = {
    "email": "メールアドレス",
    "phone": "電話番号",
    "postal": "郵便番号",
    "mynumber": "マイナンバー",
    "card": "クレジットカード番号",
}


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _my_number_ok(digits: str) -> bool:
    """The check digit of an individual number (番号法施行令 第8条)."""
    if len(digits) != 12:
        return False
    body = [int(d) for d in digits[:11]]
    total = 0
    for n in range(1, 12):
        p = body[11 - n]
        q = n + 1 if n <= 6 else n - 5
        total += p * q
    rest = total % 11
    check = 0 if rest <= 1 else 11 - rest
    return check == int(digits[11])


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 16:
        return False
    total = 0
    for position, char in enumerate(reversed(digits)):
        value = int(char)
        if position % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _phone_ok(match: str) -> bool:
    digits = _digits(match)
    if match.startswith("+81"):
        return 9 <= len(digits) - 2 <= 10
    # Japanese numbers are ten digits, mobiles and 0120/0800 eleven, and a
    # number written all in one run needs its separators to be told from a total.
    if not 10 <= len(digits) <= 11:
        return False
    return any(sep in match for sep in "-() ") or digits.startswith(("070", "080", "090", "050", "0120", "0800"))


def _lines(page: pymupdf.Page):
    """Each text line of the page as (text, boxes): one box per character."""
    data = page.get_text("rawdict", flags=RAW_TEXT_ONLY)
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            text, boxes = [], []
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    text.append(char.get("c", ""))
                    boxes.append(pymupdf.Rect(char["bbox"]))
            if text:
                yield "".join(text), boxes


def find_personal_info(doc: pymupdf.Document, kinds: list[str] | None = None) -> list[dict]:
    """Every match, in reading order, with the boxes to redact."""
    wanted = [k for k in (kinds or list(_PATTERNS)) if k in _PATTERNS]
    found = []
    for index in range(doc.page_count):
        page = doc[index]
        for text, boxes in _lines(page):
            folded = text.translate(_FOLD)
            taken: list[tuple[int, int]] = []
            # The checked kinds first, so a valid My Number is not also called a phone number.
            for kind in sorted(wanted, key=["card", "mynumber", "email", "postal", "phone"].index):
                for match in _PATTERNS[kind].finditer(folded):
                    start, end = match.span()
                    if any(start < b and a < end for a, b in taken):
                        continue
                    value = match.group(0)
                    if kind == "mynumber" and not _my_number_ok(_digits(value)):
                        continue
                    if kind == "card" and not _luhn_ok(_digits(value)):
                        continue
                    if kind == "phone" and not _phone_ok(value):
                        continue
                    rect = pymupdf.Rect()
                    for box in boxes[start:end]:
                        if not box.is_empty:
                            rect |= box
                    if rect.is_empty:
                        continue
                    taken.append((start, end))
                    found.append({
                        "page": index, "kind": kind, "label": PII_LABELS[kind],
                        "text": text[start:end], "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                    })
    return found


def mark_redactions(doc: pymupdf.Document, items: list[dict], *, overlay: str = "") -> int:
    """Add a redaction mark over each box (they are applied later, as usual)."""
    count = 0
    for item in items:
        index = int(item.get("page", -1))
        if not 0 <= index < doc.page_count:
            continue
        rect = pymupdf.Rect(item.get("rect") or [0, 0, 0, 0])
        if rect.is_empty:
            continue
        page = doc[index]
        page.add_redact_annot(
            rect + (-0.5, -0.5, 0.5, 0.5),
            text=overlay or None,
            fontname="japan" if overlay else None,
            fontsize=8,
            text_color=(1, 1, 1),
            fill=(0, 0, 0),
            cross_out=False,
        )
        count += 1
    return count


# ------------------------------------------------------------------ booklet

BOOKLET_SHEETS = {"a4": (841.89, 595.28), "a3": (1190.55, 841.89), "b4": (1031.81, 728.50)}


def booklet_order(count: int) -> list[tuple[int, int]]:
    """Pairs of page indexes (left, right) for each side of each sheet; -1 is blank.

    Sheet 1 front is (last, first), its back (second, second to last), and so
    on inwards — the order a saddle-stitched booklet needs.
    """
    total = (count + 3) // 4 * 4
    pages = list(range(count)) + [-1] * (total - count)
    sides = []
    for sheet in range(total // 4):
        front = (pages[total - 1 - 2 * sheet], pages[2 * sheet])
        back = (pages[2 * sheet + 1], pages[total - 2 - 2 * sheet])
        sides += [front, back]
    return sides


def booklet(doc: pymupdf.Document, paper: str = "a4") -> bytes:
    """Lay the pages out for a folded booklet: two pages to each side of a sheet."""
    width, height = BOOKLET_SHEETS.get(str(paper).lower(), BOOKLET_SHEETS["a4"])
    source = pymupdf.open("pdf", doc.tobytes())
    out = pymupdf.open()
    try:
        try:
            source.bake()  # markup is not carried by show_pdf_page
        except Exception:
            pass
        half = width / 2
        margin = 14
        for left, right in booklet_order(source.page_count):
            sheet = out.new_page(width=width, height=height)
            for slot, index in ((0, left), (1, right)):
                if index < 0:
                    continue
                cell = pymupdf.Rect(slot * half + margin, margin, (slot + 1) * half - margin, height - margin)
                sheet.show_pdf_page(cell, source, index, keep_proportion=True)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()
        source.close()


# ------------------------------------------------------------------ compress to a size

_LADDER = [(200, 85), (150, 75), (120, 70), (100, 60), (85, 55), (72, 50)]


def compress_to_size(data: bytes, target: int) -> tuple[bytes, dict]:
    """The best-looking version of ``data`` that fits in ``target`` bytes.

    Each setting is tried on a fresh copy, gentlest first. If none fits, the
    smallest is returned with ``fits`` false so the caller can say so.
    """
    from . import pages  # same module graph rule as the other helpers

    best = None
    tried = []
    for dpi, quality in _LADDER:
        copy = pymupdf.open("pdf", data)
        try:
            pages.compress(copy, dpi=dpi, quality=quality)
            try:
                copy.subset_fonts()  # a fresh copy, cut down once: safe (see content.subset)
            except Exception:  # noqa: BLE001
                pass
            out = copy.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=True)
        except TypeError:  # an older PyMuPDF without object streams
            out = copy.tobytes(garbage=4, deflate=True)
        finally:
            copy.close()
        tried.append(len(out))
        if best is None or len(out) < len(best[0]):
            best = (out, dpi)
        if len(out) <= target:
            return out, {"fits": True, "dpi": dpi, "size": len(out), "tried": tried}
    return best[0], {"fits": False, "dpi": best[1], "size": len(best[0]), "tried": tried}


# ------------------------------------------------------------------ replace pages


def replace_pages(doc: pymupdf.Document, targets: list[int], source: pymupdf.Document, first: int = 0) -> int:
    """Put pages of ``source`` (from ``first`` on) in place of ``targets``.

    The replacement goes in at the same position and the old page comes out,
    so the outline and the other pages are left as they were — the usual job
    of swapping in a corrected page or a signed page.
    """
    targets = sorted({int(t) for t in targets if 0 <= int(t) < doc.page_count})
    if not targets:
        raise ValueError("差し替えるページがありません")
    if first < 0 or first + len(targets) > source.page_count:
        raise ValueError(f"差し替えに使うPDFのページが足りません（{source.page_count} ページ）")
    for offset, target in enumerate(targets):
        doc.insert_pdf(source, from_page=first + offset, to_page=first + offset, start_at=target)
        doc.delete_page(target + 1)
    return len(targets)


# ------------------------------------------------------------------ spreads and note pages


def split_spreads(doc: pymupdf.Document, targets: list[int] | None = None, *, right_to_left: bool = False) -> int:
    """Cut each two-page spread (a book scanned open) into its left and right pages.

    The page is copied and each copy shows one half (a crop, so the text stays
    searchable and nothing is thrown away). Only landscape pages are split;
    a portrait page in the same scan is a single page already. Right to left
    is for books bound on the right — vertical Japanese text, manga.
    """
    from . import pages

    chosen = sorted({int(t) for t in (targets or range(doc.page_count)) if 0 <= int(t) < doc.page_count})
    count = 0
    for index in reversed(chosen):
        rect = doc[index].rect
        if rect.width <= rect.height * 1.05:
            continue
        half = rect.width / 2
        left = [0, 0, half, rect.height]
        right = [half, 0, rect.width, rect.height]
        first, second = (right, left) if right_to_left else (left, right)
        pages.copy_page_after(doc, index)
        pages.crop(doc, [index], first)
        pages.crop(doc, [index + 1], second)
        count += 1
    return count


def insert_note_page(doc: pymupdf.Document, at: int, width: float, height: float, pattern: str = "lines") -> None:
    """A blank page of note paper: ruled lines, a grid or dots, with a writing margin."""
    from . import pages

    at = max(0, min(int(at), doc.page_count))
    page = doc.new_page(pno=at, width=width, height=height)
    if pattern in ("lines", "grid", "dots"):
        pages.rule_regions(page, [pymupdf.Rect(0, 0, width, height)], pattern, inset_mm=12)
