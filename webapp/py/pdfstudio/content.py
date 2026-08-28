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

from .common import hex_to_rgb

# ---------------------------------------------------------------- searching


def search_relaxed(page: pymupdf.Page, needle: str) -> list[pymupdf.Quad]:
    """Find a phrase while ignoring whitespace and line breaks.

    OCR output puts spaces between Japanese characters ("契約 金額"), and normal
    documents break phrases across lines, so an exact search misses text the
    reader can plainly see. This walks the page character by character, matches
    against the whitespace-stripped string, and rebuilds quads from the
    characters that matched.
    """
    target = "".join(needle.split())
    if not target:
        return []

    chars: list[tuple[str, pymupdf.Rect]] = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    glyph = char.get("c", "")
                    if glyph.strip():
                        chars.append((glyph, pymupdf.Rect(char["bbox"])))

    haystack = "".join(c for c, _ in chars)
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
    data = page.get_text("dict")
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


def replace_text(page: pymupdf.Page, rect: list[float], new_text: str, *,
                 size: float = 11, colour: str = "#000000",
                 align: int = 0, background: str | None = None) -> None:
    """Remove the text inside `rect` and lay new text in its place.

    Redaction is what actually deletes the old glyphs — covering them would
    leave the original selectable underneath.
    """
    box = pymupdf.Rect(rect)
    page.add_redact_annot(box, fill=hex_to_rgb(background) if background else None)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    if not new_text:
        return
    grown = pymupdf.Rect(box.x0, box.y0 - 1, box.x1 + 2, box.y1 + 4)
    page.insert_htmlbox(
        grown,
        f'<span style="font-size:{size}pt;color:{colour};'
        f'text-align:{["left", "center", "right"][align]}">{_escape(new_text)}</span>',
    )


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
