from __future__ import annotations

import datetime

import pymupdf


def hex_to_rgb(value):
    if not value:
        return None
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb):
    if not rgb:
        return None
    if len(rgb) == 1:
        rgb = (rgb[0], rgb[0], rgb[0])
    elif len(rgb) == 4:
        c, m, y, k = rgb
        rgb = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb[:3])


def pdf_date(value: str | None) -> str | None:
    """Convert a PDF date string (D:YYYYMMDDHHmmSS...) to ISO 8601."""
    if not value:
        return None
    raw = value[2:] if value.startswith("D:") else value
    digits = ""
    for ch in raw:
        if ch.isdigit():
            digits += ch
        else:
            break
    if len(digits) < 8:
        return None
    digits = digits.ljust(14, "0")[:14]
    try:
        return datetime.datetime.strptime(digits, "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def now_pdf_date() -> str:
    return datetime.datetime.now().strftime("D:%Y%m%d%H%M%S")


def rect_of(points) -> list[float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def to_points(vertices) -> list[list[float]]:
    return [[float(p[0]), float(p[1])] for p in vertices]


# ---------------------------------------------------------------- rotation
#
# PyMuPDF works in the page's authored coordinates: search_for, get_text and
# annot.rect all ignore /Rotate. The viewer does not — pdf.js renders the page
# rotated, and page.rect reports the rotated size. Those are two different
# frames, and mixing them puts markup in the wrong place on any rotated page.
#
# Everything crossing the API speaks *view space* (what the user sees) and
# everything inside speaks *page space* (what PyMuPDF expects). These two
# functions are the only place that difference is handled.

GEOMETRY_KEYS = ("rect", "points", "quads", "strokes", "callout")


def _rect(value, matrix) -> list[float]:
    box = pymupdf.Rect(value) * matrix
    box.normalize()
    return list(box)


def _points(value, matrix) -> list[list[float]]:
    return [list(pymupdf.Point(p) * matrix) for p in value]


def _quad(value, matrix) -> list[float]:
    out: list[float] = []
    for index in range(0, len(value) - 1, 2):
        point = pymupdf.Point(value[index], value[index + 1]) * matrix
        out.extend([point.x, point.y])
    return out


def transform_geometry(item: dict, matrix) -> dict:
    """Return a copy of an annotation with its geometry moved to another frame."""
    if matrix is None:
        return item
    moved = dict(item)
    if item.get("rect"):
        moved["rect"] = _rect(item["rect"], matrix)
    if item.get("points"):
        moved["points"] = _points(item["points"], matrix)
    if item.get("quads"):
        moved["quads"] = [_quad(q, matrix) for q in item["quads"]]
    if item.get("callout"):
        moved["callout"] = _points(item["callout"], matrix)
    if item.get("strokes"):
        moved["strokes"] = [
            {**stroke, "pts": _points(stroke.get("pts", []), matrix)}
            for stroke in item["strokes"]
        ]
    return moved


def to_view(page: pymupdf.Page, item: dict) -> dict:
    """Page space -> view space (for sending to the browser)."""
    return transform_geometry(item, page.rotation_matrix if page.rotation else None)


def to_page(page: pymupdf.Page, item: dict) -> dict:
    """View space -> page space (for handing to PyMuPDF)."""
    return transform_geometry(item, page.derotation_matrix if page.rotation else None)


def rect_to_page(page: pymupdf.Page, rect) -> list[float]:
    """A single rectangle from the browser, in the frame PyMuPDF expects."""
    if not page.rotation:
        return list(rect)
    return _rect(rect, page.derotation_matrix)


def rect_to_view(page: pymupdf.Page, rect) -> list[float]:
    if not page.rotation:
        return list(rect)
    return _rect(rect, page.rotation_matrix)


def page_info(page: pymupdf.Page) -> dict:
    rect = page.rect
    return {
        "index": page.number,
        "width": rect.width,
        "height": rect.height,
        "rotation": page.rotation,
    }
