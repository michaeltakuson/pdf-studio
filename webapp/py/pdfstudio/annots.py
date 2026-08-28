"""Annotation layer: JSON model <-> real PDF annotations (PyMuPDF).

The browser owns an editable JSON model while the user works. This module is
the only place that translates that model into standard-compliant PDF
annotations and back, so anything written here stays readable in Acrobat,
Edge, Foxit and the rest.

Coordinates are in PyMuPDF page space (points, top-left origin, page rotation
already applied), which matches a pdf.js viewport at scale 1.
"""

from __future__ import annotations

import json
import uuid

import pymupdf

from .common import (
    hex_to_rgb,
    now_pdf_date,
    pdf_date,
    rect_of,
    rgb_to_hex,
    to_page,
    to_points,
    to_view,
)

TEXT_MARKUP = {"highlight", "underline", "squiggly", "strikeout"}
VERTEX_TYPES = {"line", "polygon", "polyline"}

PDF_TO_TYPE = {
    pymupdf.PDF_ANNOT_TEXT: "note",
    pymupdf.PDF_ANNOT_FREE_TEXT: "freetext",
    pymupdf.PDF_ANNOT_LINE: "line",
    pymupdf.PDF_ANNOT_SQUARE: "square",
    pymupdf.PDF_ANNOT_CIRCLE: "circle",
    pymupdf.PDF_ANNOT_POLYGON: "polygon",
    pymupdf.PDF_ANNOT_POLY_LINE: "polyline",
    pymupdf.PDF_ANNOT_HIGHLIGHT: "highlight",
    pymupdf.PDF_ANNOT_UNDERLINE: "underline",
    pymupdf.PDF_ANNOT_SQUIGGLY: "squiggly",
    pymupdf.PDF_ANNOT_STRIKE_OUT: "strikeout",
    pymupdf.PDF_ANNOT_STAMP: "stamp",
    pymupdf.PDF_ANNOT_CARET: "caret",
    pymupdf.PDF_ANNOT_INK: "ink",
    pymupdf.PDF_ANNOT_FILE_ATTACHMENT: "fileattachment",
    pymupdf.PDF_ANNOT_REDACT: "redact",
}

SKIP_TYPES = {
    pymupdf.PDF_ANNOT_POPUP,
    pymupdf.PDF_ANNOT_LINK,
    pymupdf.PDF_ANNOT_WIDGET,
}

# Line ending styles, in the order the PDF spec defines them.
LINE_ENDS = [
    "none",
    "square",
    "circle",
    "diamond",
    "openArrow",
    "closedArrow",
    "butt",
    "rOpenArrow",
    "rClosedArrow",
    "slash",
]

NOTE_ICONS = ["Comment", "Key", "Note", "Help", "NewParagraph", "Paragraph", "Insert"]

BORDER_STYLES = {"solid": "S", "dashed": "D", "beveled": "B", "inset": "I", "underline": "U"}

# Extra state that has no home in the PDF spec (pen pressure, review checkmarks,
# tool-specific settings). Stored under a private key on the annotation object:
# other viewers ignore unknown keys, so the file stays portable.
PRIVATE_KEY = "PDFStudio"

# Model fields the PDF spec has nowhere to put. Replies and review status are
# NOT here: the spec defines those (IRT / RT / State), so they are written as
# real reply annotations that Acrobat and Foxit display in their own panels.
PRIVATE_FIELDS = (
    "checked", "group", "measure", "callout", "tool",
    "label", "stampIndex", "overlayText",
)

# Review states, as the spec spells them in /State with /StateModel /Review.
STATES = {
    "accepted": "Accepted",
    "rejected": "Rejected",
    "completed": "Completed",
    "cancelled": "Cancelled",
    "none": "None",
}
STATES_REVERSE = {v: k for k, v in STATES.items()}


def _default_style() -> dict:
    return {
        "stroke": "#e8b900",
        "fill": None,
        "opacity": 1.0,
        "width": 1.5,
        "dash": [],
        "borderStyle": "solid",
        "cloudIntensity": 0,
        "lineEnds": ["none", "none"],
        "font": {
            "family": "japan",
            "size": 11,
            "color": "#000000",
            "align": "left",
        },
        "rotate": 0,
        "blend": None,
    }


# ---------------------------------------------------------------- reading


def _read_private(annot: pymupdf.Annot, doc: pymupdf.Document) -> dict:
    try:
        kind, value = doc.xref_get_key(annot.xref, PRIVATE_KEY)
    except Exception:
        return {}
    if kind != "string" or not value:
        return {}
    try:
        return json.loads(value.strip("()"))
    except Exception:
        return {}


def _parse_da(doc: pymupdf.Document, xref: int) -> dict:
    """Pull font colour, size and name out of a FreeText /DA string.

    A /DA looks like "0.18 0.43 0.96 rg /japan 12 Tf" — colour operator first
    (g grey, rg RGB, k CMYK), then the font selection.
    """
    try:
        kind, value = doc.xref_get_key(xref, "DA")
    except Exception:
        return {}
    if kind not in ("string", "xref") or not value:
        return {}
    text = value.strip("()")
    tokens = text.split()
    out: dict = {}
    for index, token in enumerate(tokens):
        if token in ("g", "rg", "k"):
            count = {"g": 1, "rg": 3, "k": 4}[token]
            try:
                channels = [float(t) for t in tokens[index - count : index]]
            except ValueError:
                continue
            colour = rgb_to_hex(channels)
            if colour:
                out["color"] = colour
        elif token == "Tf" and index >= 2:
            try:
                out["size"] = float(tokens[index - 1])
            except ValueError:
                pass
            name = tokens[index - 2]
            if name.startswith("/"):
                out["family"] = name[1:]
    return out


def _read_geometry(annot: pymupdf.Annot, kind: str) -> dict:
    out: dict = {}
    vertices = annot.vertices
    if kind in TEXT_MARKUP or kind == "redact":
        if vertices:
            pts = to_points(vertices)
            out["quads"] = [
                [c for p in pts[i : i + 4] for c in p] for i in range(0, len(pts), 4)
            ]
    elif kind == "ink":
        if vertices:
            out["strokes"] = [
                {"pts": to_points(stroke), "pressure": None} for stroke in vertices
            ]
    elif kind in VERTEX_TYPES:
        if vertices:
            out["points"] = to_points(vertices)
    return out


def annot_to_json(annot: pymupdf.Annot, doc: pymupdf.Document, page_index: int) -> dict | None:
    kind = PDF_TO_TYPE.get(annot.type[0])
    if kind is None:
        return None

    info = annot.info
    colors = annot.colors or {}
    border = annot.border or {}
    style = _default_style()
    if kind == "freetext":
        # A FreeText annotation's /C is its background, not its border colour.
        # Its text appearance lives in /DA, and viewers draw the border in that
        # same colour — so one colour governs text, border and callout line.
        style["fill"] = rgb_to_hex(colors.get("stroke"))
        style["font"].update(_parse_da(doc, annot.xref))
        style["stroke"] = style["font"]["color"]
    else:
        style["stroke"] = rgb_to_hex(colors.get("stroke")) or style["stroke"]
        style["fill"] = rgb_to_hex(colors.get("fill"))
    style["opacity"] = annot.opacity if annot.opacity >= 0 else 1.0
    if border.get("width", -1) >= 0:
        style["width"] = border["width"]
    style["dash"] = list(border.get("dashes") or [])
    style["cloudIntensity"] = max(0, border.get("clouds", 0) or 0)

    if kind == "line":
        ends = annot.line_ends or (0, 0)
        style["lineEnds"] = [
            LINE_ENDS[e] if 0 <= e < len(LINE_ENDS) else "none" for e in ends
        ]

    flags = annot.flags
    data = {
        "id": info.get("id") or uuid.uuid4().hex[:12],
        "page": page_index,
        "type": kind,
        "rect": list(annot.rect),
        "contents": info.get("content", ""),
        "author": info.get("title", ""),
        "subject": info.get("subject", ""),
        "created": pdf_date(info.get("creationDate")),
        "modified": pdf_date(info.get("modDate")),
        "icon": info.get("name") or None,
        "style": style,
        "flags": {
            "locked": bool(flags & pymupdf.PDF_ANNOT_IS_LOCKED),
            "readOnly": bool(flags & pymupdf.PDF_ANNOT_IS_READ_ONLY),
            "print": bool(flags & pymupdf.PDF_ANNOT_IS_PRINT),
            "hidden": bool(flags & pymupdf.PDF_ANNOT_IS_HIDDEN),
        },
        "state": None,
        "checked": False,
        "replies": [],
        "xref": annot.xref,
    }
    data.update(_read_geometry(annot, kind))

    if kind == "freetext":
        data["text"] = info.get("content", "")

    private = _read_private(annot, doc)
    if private:
        # Pressure profiles and review state only exist in our own key.
        if "strokes" in data and private.get("pressure"):
            for stroke, pressure in zip(data["strokes"], private["pressure"]):
                stroke["pressure"] = pressure
        for key in PRIVATE_FIELDS:
            if key in private:
                data[key] = private[key]
        if private.get("style"):
            data["style"].update(private["style"])
        authored = private.get("rect")
        if authored and _encloses(data["rect"], authored):
            # PyMuPDF pads /Rect to cover borders and cloud scallops. Taking
            # that padded box back as the shape would grow it a little on every
            # save, so prefer the rect we authored — but only while it still
            # sits inside the stored one, which means nothing else moved it.
            data["rect"] = authored
    return data


def _encloses(outer: list[float], inner: list[float], tolerance: float = 0.6) -> bool:
    return (
        outer[0] - tolerance <= inner[0]
        and outer[1] - tolerance <= inner[1]
        and outer[2] + tolerance >= inner[2]
        and outer[3] + tolerance >= inner[3]
    )


def _read_state(doc: pymupdf.Document, xref: int) -> str | None:
    try:
        kind, value = doc.xref_get_key(xref, "State")
    except Exception:
        return None
    if kind not in ("string", "name") or not value:
        return None
    return STATES_REVERSE.get(value.strip("()/"))


def read_page(page: pymupdf.Page) -> list[dict]:
    """Read one page, folding reply annotations into their parents.

    A reply is an annotation carrying /IRT (in reply to) with /RT /R. Acrobat
    and Foxit write review status the same way — a reply whose /State says
    Accepted or Rejected — so following that convention means their panels and
    ours show the same conversation.
    """
    doc = page.parent
    parents: dict[int, dict] = {}
    order: list[dict] = []
    responses: list[tuple[int, pymupdf.Annot]] = []

    for annot in page.annots():
        if annot.type[0] in SKIP_TYPES:
            continue
        irt = annot.irt_xref
        if irt and irt != annot.xref:
            responses.append((irt, annot))
            continue
        item = annot_to_json(annot, doc, page.number)
        if item:
            parents[annot.xref] = item
            order.append(item)

    for parent_xref, annot in responses:
        parent = parents.get(parent_xref)
        if parent is None:
            # An orphaned reply (its parent was removed elsewhere) is still a
            # comment someone wrote, so surface it rather than dropping it.
            item = annot_to_json(annot, doc, page.number)
            if item:
                order.append(item)
            continue
        state = _read_state(doc, annot.xref)
        info = annot.info
        if state:
            parent["state"] = None if state == "none" else state
            continue
        parent["replies"].append({
            "id": uuid.uuid4().hex[:12],
            "author": info.get("title", ""),
            "contents": info.get("content", ""),
            "created": pdf_date(info.get("creationDate")),
        })

    # Everything above works in page space, the way PyMuPDF reports it. The
    # browser draws what the reader sees, so convert once on the way out.
    return [to_view(page, item) for item in order]


def read_document(doc: pymupdf.Document) -> list[dict]:
    out = []
    for page in doc:
        out.extend(read_page(page))
    return out


# ---------------------------------------------------------------- writing


def _quads(item: dict) -> list[pymupdf.Quad]:
    """Model quads are 8 flat numbers (ul, ur, ll, lr); PyMuPDF wants 4 points."""
    out = []
    for q in item.get("quads", []):
        if len(q) != 8:
            continue
        out.append(pymupdf.Quad((q[0], q[1]), (q[2], q[3]), (q[4], q[5]), (q[6], q[7])))
    return out


def _create(page: pymupdf.Page, item: dict) -> pymupdf.Annot | None:
    kind = item["type"]
    style = {**_default_style(), **(item.get("style") or {})}
    font = {**_default_style()["font"], **(style.get("font") or {})}
    rect = pymupdf.Rect(item["rect"]) if item.get("rect") else None

    if kind in TEXT_MARKUP:
        quads = _quads(item)
        if not quads:
            return None
        adder = {
            "highlight": page.add_highlight_annot,
            "underline": page.add_underline_annot,
            "squiggly": page.add_squiggly_annot,
            "strikeout": page.add_strikeout_annot,
        }[kind]
        return adder(quads=quads)

    if kind == "note":
        icon = item.get("icon") or "Comment"
        return page.add_text_annot(rect.tl, item.get("contents", ""), icon=icon)

    if kind == "freetext":
        callout = item.get("callout")
        return page.add_freetext_annot(
            rect,
            item.get("text", item.get("contents", "")),
            fontsize=font["size"],
            fontname=font["family"],
            text_color=hex_to_rgb(font["color"]),
            fill_color=hex_to_rgb(style["fill"]),
            border_width=style["width"],
            dashes=style["dash"] or None,
            callout=[pymupdf.Point(p) for p in callout] if callout else None,
            opacity=style["opacity"],
            align={"left": 0, "center": 1, "right": 2}.get(font["align"], 0),
            rotate=int(style["rotate"]),
        )

    if kind == "line":
        pts = item.get("points") or []
        if len(pts) < 2:
            return None
        return page.add_line_annot(pymupdf.Point(pts[0]), pymupdf.Point(pts[-1]))

    if kind == "square":
        return page.add_rect_annot(rect)

    if kind == "circle":
        return page.add_circle_annot(rect)

    if kind == "polygon":
        pts = item.get("points") or []
        return page.add_polygon_annot([pymupdf.Point(p) for p in pts]) if len(pts) >= 3 else None

    if kind == "polyline":
        pts = item.get("points") or []
        return page.add_polyline_annot([pymupdf.Point(p) for p in pts]) if len(pts) >= 2 else None

    if kind == "ink":
        strokes = [
            [(float(p[0]), float(p[1])) for p in stroke["pts"]]
            for stroke in item.get("strokes", [])
            if len(stroke.get("pts", [])) >= 2
        ]
        return page.add_ink_annot(strokes) if strokes else None

    if kind == "caret":
        return page.add_caret_annot(rect.tl)

    if kind == "redact":
        quads = _quads(item)
        target = quads[0] if quads else rect
        overlay = item.get("overlayText") or None
        annot = page.add_redact_annot(
            target,
            text=overlay,
            # Helvetica cannot draw Japanese, so overlay text set in it would
            # silently disappear; the built-in CJK face covers Latin too.
            fontname="japan" if overlay else None,
            fill=hex_to_rgb(style["fill"] or "#000000"),
            text_color=hex_to_rgb(font["color"]),
            fontsize=font["size"],
            cross_out=False,
        )
        for extra in quads[1:]:
            page.add_redact_annot(
                extra,
                fill=hex_to_rgb(style["fill"] or "#000000"),
                cross_out=False,
            )
        return annot

    if kind == "stamp":
        # Only 0-13 map to distinct spec stamps; the rest fall back to Approved.
        index = max(0, min(13, int(item.get("stampIndex", 0))))
        return page.add_stamp_annot(rect, stamp=index)

    return None


def _apply_style(annot: pymupdf.Annot, item: dict) -> None:
    kind = item["type"]
    style = {**_default_style(), **(item.get("style") or {})}

    stroke = hex_to_rgb(style["stroke"])
    fill = hex_to_rgb(style["fill"])
    if kind in TEXT_MARKUP or kind in {"note", "stamp", "caret"}:
        # For these, /C is the single colour of the mark or icon.
        annot.set_colors(stroke=stroke)
    elif kind == "freetext":
        pass  # PyMuPDF rejects set_colors here; the background is set at creation.
    elif kind != "redact":
        annot.set_colors(stroke=stroke, fill=fill)

    if kind not in TEXT_MARKUP and kind not in {"note", "stamp", "caret"}:
        annot.set_border(
            width=style["width"],
            dashes=style["dash"] or None,
            style=BORDER_STYLES.get(style.get("borderStyle", "solid"), "S"),
            clouds=style.get("cloudIntensity", 0) or -1,
        )

    if kind == "line":
        ends = style.get("lineEnds") or ["none", "none"]
        annot.set_line_ends(
            LINE_ENDS.index(ends[0]) if ends[0] in LINE_ENDS else 0,
            LINE_ENDS.index(ends[1]) if ends[1] in LINE_ENDS else 0,
        )

    annot.set_opacity(float(style.get("opacity", 1.0)))

    flags = 0
    f = item.get("flags") or {}
    if f.get("print", True):
        flags |= pymupdf.PDF_ANNOT_IS_PRINT
    if f.get("locked"):
        flags |= pymupdf.PDF_ANNOT_IS_LOCKED
    if f.get("readOnly"):
        flags |= pymupdf.PDF_ANNOT_IS_READ_ONLY
    if f.get("hidden"):
        flags |= pymupdf.PDF_ANNOT_IS_HIDDEN
    annot.set_flags(flags)

    annot.set_info(
        content=item.get("contents", "") or "",
        title=item.get("author", "") or "",
        subject=item.get("subject", "") or "",
        creationDate=item.get("createdPdf") or now_pdf_date(),
        modDate=now_pdf_date(),
    )

    blend = style.get("blend")
    if blend:
        annot.update(blend_mode=blend)
    else:
        annot.update()


def _write_private(annot: pymupdf.Annot, doc: pymupdf.Document, item: dict) -> None:
    payload = {"id": item.get("id"), "rect": [round(v, 2) for v in item["rect"]]}
    pressure = [s.get("pressure") for s in item.get("strokes", [])]
    if any(p for p in pressure):
        payload["pressure"] = pressure
    for key in PRIVATE_FIELDS:
        value = item.get(key)
        if value not in (None, [], False):
            payload[key] = value
    if len(payload) == 1:
        return
    doc.xref_set_key(annot.xref, PRIVATE_KEY, pymupdf.get_pdf_str(json.dumps(payload)))


def _write_responses(page: pymupdf.Page, parent: pymupdf.Annot, item: dict) -> None:
    """Write replies and review status as real /IRT reply annotations."""
    doc = page.parent
    parent_xref = parent.xref

    def response(contents: str, author: str, created: str | None = None) -> pymupdf.Annot:
        reply = page.add_text_annot(parent.rect.tl, contents, icon="Comment")
        reply.set_info(
            content=contents,
            title=author or "",
            creationDate=created or now_pdf_date(),
            modDate=now_pdf_date(),
        )
        # Replies are listed in comment panels, never drawn on the page.
        reply.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
        reply.update()
        reply.set_irt_xref(parent_xref)
        doc.xref_set_key(reply.xref, "RT", "/R")
        return reply

    for reply in item.get("replies") or []:
        response(reply.get("contents", ""), reply.get("author", ""))

    state = item.get("state")
    if state in STATES:
        marker = response("", item.get("author", ""))
        doc.xref_set_key(marker.xref, "State", pymupdf.get_pdf_str(STATES[state]))
        doc.xref_set_key(marker.xref, "StateModel", pymupdf.get_pdf_str("Review"))


def write_document(doc: pymupdf.Document, items: list[dict]) -> int:
    """Replace every managed annotation in `doc` with `items`.

    Widgets, links and popups are left untouched — they are owned by other
    parts of the app, not by the markup model.
    """
    for page in doc:
        # Deleting an annotation also removes its replies, which invalidates
        # any handles taken beforehand — so rescan after every removal.
        while True:
            target = next(
                (a for a in page.annots() if a.type[0] not in SKIP_TYPES), None
            )
            if target is None:
                break
            page.delete_annot(target)

    written = 0
    by_page: dict[int, list[dict]] = {}
    for item in items:
        by_page.setdefault(int(item.get("page", 0)), []).append(item)

    for index, page_items in by_page.items():
        if index < 0 or index >= doc.page_count:
            continue
        page = doc[index]
        for incoming in page_items:
            if not incoming.get("rect") and incoming.get("points"):
                incoming["rect"] = rect_of(incoming["points"])
            # The browser sends what the reader sees; PyMuPDF wants the page as
            # authored. On an unrotated page these are the same thing.
            item = to_page(page, incoming)
            annot = _create(page, item)
            if annot is None:
                continue
            _apply_style(annot, item)
            _write_private(annot, doc, item)
            if item.get("replies") or item.get("state"):
                _write_responses(page, annot, item)
            written += 1
    return written
