"""Page content operations (layer ②): real text and images.

Unlike the annotation layer, everything here rewrites what is *inside* the
page. There is no per-object undo once saved, which is why the app takes a
snapshot before each of these.

OCR is not part of this build: it shells out to the Tesseract executable,
which does not exist inside a browser sandbox. The server-based version of
PDF Studio still has it.
"""

from __future__ import annotations


import pymupdf

from .common import RAW_TEXT_ONLY, TEXT_ONLY, hex_to_rgb

# ---------------------------------------------------------------- searching


def search_relaxed(page: pymupdf.Page, needle: str) -> list[pymupdf.Quad]:
    """Find a phrase while ignoring whitespace and line breaks.

    OCR output puts spaces between Japanese characters ("契約 金額"), and normal
    documents break phrases across lines, so an exact search misses text the
    reader can plainly see. This walks the page character by character, matches
    against the whitespace-stripped string, and rebuilds quads from the
    characters that matched.
    """
    target = "".join(needle.split()).lower()
    if not target:
        return []
    # Walking a page character by character is slow, and most pages do not
    # contain the phrase at all: rule those out with a plain-text check first.
    if target not in "".join(page.get_text().split()).lower():
        return []

    chars: list[tuple[str, pymupdf.Rect]] = []
    for block in page.get_text("rawdict", flags=RAW_TEXT_ONLY).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    glyph = char.get("c", "")
                    if glyph.strip():
                        chars.append((glyph, pymupdf.Rect(char["bbox"])))

    # Lower-casing must not change the length, or positions would drift.
    haystack = "".join(c.lower() if len(c.lower()) == 1 else c for c, _ in chars)
    results: list[pymupdf.Quad] = []
    start = haystack.find(target)
    while start != -1:
        matched = chars[start : start + len(target)]
        # One quad per line, so a match spanning a line break stays accurate.
        run: list[pymupdf.Rect] = []
        for _, rect in matched:
            if run and abs(rect.y0 - run[-1].y0) > run[-1].height * 0.6:
                results.append(_union(run).quad)
                run = []
            run.append(rect)
        if run:
            results.append(_union(run).quad)
        start = haystack.find(target, start + 1)
    return results


def _union(rects: list[pymupdf.Rect]) -> pymupdf.Rect:
    box = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        box |= rect
    return box


# ---------------------------------------------------------------- text editing


def find_text_blocks(page: pymupdf.Page) -> list[dict]:
    """Editable text spans, with the geometry needed to replace them in place."""
    blocks = []
    data = page.get_text("dict", flags=TEXT_ONLY)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                blocks.append({
                    "text": text,
                    "rect": list(span["bbox"]),
                    "size": span.get("size", 11),
                    "font": span.get("font", ""),
                    "colour": "#%06x" % (span.get("color", 0) & 0xFFFFFF),
                    "page": page.number,
                })
    return blocks


def find_text_lines(page: pymupdf.Page) -> list[dict]:
    """Lines of body text, each with what is needed to retype it in place."""
    out = []
    for block in page.get_text("dict", flags=TEXT_ONLY).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            direction = line.get("dir") or (1, 0)
            if abs(direction[0] - 1) > 0.01 or abs(direction[1]) > 0.01:
                continue  # vertical or slanted text: not something to retype in a box
            first = max(spans, key=lambda s: len(s.get("text", "")))
            flags = int(first.get("flags", 0))
            out.append({
                "text": "".join(s.get("text", "") for s in line.get("spans", [])).rstrip(),
                "rect": list(line["bbox"]),
                "origin": list(spans[0].get("origin") or (line["bbox"][0], line["bbox"][3])),
                "size": round(float(first.get("size", 11)), 2),
                "colour": "#%06x" % (int(first.get("color", 0)) & 0xFFFFFF),
                "serif": bool(flags & 4),
                "bold": bool(flags & 16),
                "font": first.get("font", ""),
                "page": page.number,
            })
    return out


def body_font(serif: bool = False, bold: bool = False) -> pymupdf.Font:
    """A Japanese face for text written into the page itself.

    MuPDF's built-in CJK fallback draws kanji in their Chinese forms, so the
    bundled Japanese faces are used wherever they can be found.
    """
    from . import textap

    regular, heavy, _ = textap.FAMILIES["mincho" if serif else "gothic"]
    data = textap._font_bytes(heavy if (bold and heavy) else regular)
    if data:
        try:
            return pymupdf.Font(fontbuffer=data)
        except Exception:
            pass
    return pymupdf.Font("japan-s" if serif else "japan")


def subset(doc: pymupdf.Document) -> None:
    """Shrink fonts embedded whole by the text writer down to what is used."""
    try:
        doc.subset_fonts()
    except Exception:
        pass


def replace_text(page: pymupdf.Page, rect: list[float], new_text: str, *,
                 size: float = 11, colour: str = "#000000",
                 align: int = 0, background: str | None = None,
                 origin: list[float] | None = None,
                 serif: bool = False, bold: bool = False) -> None:
    """Remove the text inside `rect` and lay new text in its place.

    Redaction is what actually deletes the old glyphs — covering them would
    leave the original selectable underneath. Pictures and line art under the
    box are left alone, so table rules and backgrounds survive.
    """
    box = pymupdf.Rect(rect)
    # A hair smaller than the line box, so neighbouring lines are not caught.
    target = pymupdf.Rect(box.x0, box.y0 + box.height * 0.12, box.x1, box.y1 - box.height * 0.12)
    page.add_redact_annot(target, fill=hex_to_rgb(background) if background else None)
    try:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=0)
    except TypeError:  # older PyMuPDF without the graphics switch
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    if not new_text:
        return
    font = body_font(serif, bold)
    start = pymupdf.Point(origin) if origin else pymupdf.Point(box.x0, box.y1 - size * 0.2)
    writer = pymupdf.TextWriter(page.rect)
    for index, line in enumerate(str(new_text).split("\n")):
        width = font.text_length(line, size)
        x = start.x
        if align == 1:
            x = box.x0 + (box.width - width) / 2
        elif align == 2:
            x = box.x1 - width
        writer.append(pymupdf.Point(x, start.y + index * size * 1.3), line, font=font, fontsize=size)
    writer.write_text(page, color=hex_to_rgb(colour))


def search_replace(doc: pymupdf.Document, needle: str, replacement: str, *,
                   size: float | None = None, colour: str = "#000000") -> int:
    count = 0
    for page in doc:
        hits = page.search_for(needle, flags=pymupdf.TEXTFLAGS_SEARCH)
        if not hits:
            continue
        spans = {tuple(round(v, 1) for v in b["rect"]): b for b in find_text_blocks(page)}
        for rect in hits:
            match = None
            for key, block in spans.items():
                if pymupdf.Rect(key).intersects(rect):
                    match = block
                    break
            replace_text(
                page, list(rect), replacement,
                size=size or (match or {}).get("size", 11),
                colour=colour,
            )
            count += 1
    if count:
        subset(doc)
    return count


# ---------------------------------------------------------------- images


def list_images(page: pymupdf.Page) -> list[dict]:
    out = []
    for info in page.get_images(full=True):
        xref = info[0]
        for rect in page.get_image_rects(xref):
            out.append({
                "xref": xref,
                "rect": list(rect),
                "width": info[2],
                "height": info[3],
                "page": page.number,
            })
    return out


def insert_image(page: pymupdf.Page, rect: list[float], data: bytes) -> None:
    page.insert_image(pymupdf.Rect(rect), stream=data, keep_proportion=True)


def replace_image(doc: pymupdf.Document, xref: int, data: bytes) -> None:
    doc.replace_image(xref, stream=data)


def delete_image(doc: pymupdf.Document, xref: int) -> None:
    doc.delete_image(xref)


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
