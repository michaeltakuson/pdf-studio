"""Comparing two documents: pixel differences and coloured overlays.

Drawing review works this way because a revised sheet rarely says what changed.
Differences come back as cloud annotations — the standard revision notation —
so they land in the same comment list as everything else.
"""

from __future__ import annotations

import pymupdf

DIFF_DPI = 110
# Pixels differing by less than this are treated as noise from rasterising.
TOLERANCE = 40


def _grey(page: pymupdf.Page, dpi: int) -> tuple[bytearray, int, int]:
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, annots=False)
    return bytearray(pixmap.samples), pixmap.width, pixmap.height


def _cells(width: int, height: int, size: int):
    for y in range(0, height, size):
        for x in range(0, width, size):
            yield x, y, min(x + size, width), min(y + size, height)


def diff_page(base: pymupdf.Page, revised: pymupdf.Page, *,
              dpi: int = DIFF_DPI, cell: int = 12,
              tolerance: int = TOLERANCE) -> list[list[float]]:
    """Rectangles, in the revised page's coordinates, where the pages differ."""
    old, ow, oh = _grey(base, dpi)
    new, nw, nh = _grey(revised, dpi)
    width, height = min(ow, nw), min(oh, nh)
    scale = 72 / dpi

    changed: list[tuple[int, int, int, int]] = []
    for x0, y0, x1, y1 in _cells(width, height, cell):
        hit = False
        for y in range(y0, y1):
            row_old = y * ow
            row_new = y * nw
            for x in range(x0, x1):
                if abs(old[row_old + x] - new[row_new + x]) > tolerance:
                    hit = True
                    break
            if hit:
                break
        if hit:
            changed.append((x0, y0, x1, y1))

    # Pages of different size differ everywhere past the shared area.
    if (ow, oh) != (nw, nh):
        if nw > width:
            changed.append((width, 0, nw, nh))
        if nh > height:
            changed.append((0, height, nw, nh))

    return [[x0 * scale, y0 * scale, x1 * scale, y1 * scale]
            for x0, y0, x1, y1 in _merge(changed, cell)]


def _merge(cells: list[tuple[int, int, int, int]], size: int) -> list[tuple[int, int, int, int]]:
    """Join touching cells so one change becomes one cloud, not fifty."""
    remaining = set(cells)
    lookup = {(c[0], c[1]): c for c in cells}
    groups = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        group = [seed]
        while stack:
            x0, y0, x1, y1 = stack.pop()
            for dx, dy in ((size, 0), (-size, 0), (0, size), (0, -size)):
                neighbour = lookup.get((x0 + dx, y0 + dy))
                if neighbour and neighbour in remaining:
                    remaining.discard(neighbour)
                    stack.append(neighbour)
                    group.append(neighbour)
        groups.append((
            min(c[0] for c in group), min(c[1] for c in group),
            max(c[2] for c in group), max(c[3] for c in group),
        ))
    return groups


def compare(base: pymupdf.Document, revised: pymupdf.Document, *,
            dpi: int = DIFF_DPI, colour: str = "#e0403a",
            author: str = "", pad: float = 3.0) -> list[dict]:
    """Model annotations marking every difference, ready to add to the model."""
    items: list[dict] = []
    for index in range(max(base.page_count, revised.page_count)):
        if index >= revised.page_count:
            break
        if index >= base.page_count:
            page = revised[index]
            items.append(_cloud(index, list(page.rect), colour, author, "追加されたページ"))
            continue
        for rect in diff_page(base[index], revised[index], dpi=dpi):
            # Rasterised differences are already in the frame the reader sees.
            items.append(_cloud(
                index,
                [rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad],
                colour, author, "変更あり",
            ))
    return items


def _cloud(page: int, rect: list[float], colour: str, author: str, note: str) -> dict:
    return {
        "type": "square",
        "page": page,
        "rect": rect,
        "contents": note,
        "subject": "文書比較",
        "author": author,
        "style": {
            "stroke": colour, "fill": None, "width": 1.5,
            "opacity": 1, "cloudIntensity": 2,
        },
        "flags": {"print": True, "locked": False, "readOnly": False, "hidden": False},
    }


def overlay(base: pymupdf.Document, revised: pymupdf.Document, *,
            base_colour=(0.85, 0.15, 0.15), revised_colour=(0.15, 0.35, 0.85),
            dpi: int = 150) -> bytes:
    """Two versions tinted and laid over each other, so shifts show as colour."""
    out = pymupdf.open()
    for index in range(max(base.page_count, revised.page_count)):
        source = revised[index] if index < revised.page_count else base[index]
        page = out.new_page(width=source.rect.width, height=source.rect.height)
        for document, tint in ((base, base_colour), (revised, revised_colour)):
            if index >= document.page_count:
                continue
            pixmap = document[index].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, annots=False)
            tinted = _tint(pixmap, tint)
            page.insert_image(page.rect, pixmap=tinted, overlay=True)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def _tint(pixmap: pymupdf.Pixmap, colour) -> pymupdf.Pixmap:
    """Grey page to a coloured layer: paper becomes transparent, ink the tint."""
    grey = pixmap.samples
    out = bytearray(pixmap.width * pixmap.height * 4)
    r, g, b = (int(c * 255) for c in colour)
    for i, value in enumerate(grey):
        base = i * 4
        out[base] = r
        out[base + 1] = g
        out[base + 2] = b
        # Half opacity so both layers stay readable where they overlap.
        out[base + 3] = (255 - value) // 2
    return pymupdf.Pixmap(
        pymupdf.csRGB, pixmap.width, pixmap.height, bytes(out), True
    )
