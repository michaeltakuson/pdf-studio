"""Signatures.

Two different things share the word. A drawn or typed signature is a picture
of a name — it proves nothing on its own. A digital signature carries a
certificate and detects any later change to the file. The app keeps them
clearly apart rather than letting the first pass for the second.
"""

from __future__ import annotations

import base64
import datetime

import pymupdf

from .common import hex_to_rgb, rect_to_page, to_page


def place_drawn(page: pymupdf.Page, rect: list[float], strokes: list[dict], *,
                colour: str = "#12305e", width: float = 1.6) -> dict:
    """Place a hand-drawn signature as an Ink annotation."""
    moved = to_page(page, {"strokes": strokes})["strokes"]
    paths = [
        [(float(x), float(y)) for x, y in stroke.get("pts", [])]
        for stroke in moved
        if len(stroke.get("pts", [])) >= 2
    ]
    if not paths:
        raise ValueError("署名の筆跡がありません")
    annot = page.add_ink_annot(paths)
    annot.set_colors(stroke=hex_to_rgb(colour))
    annot.set_border(width=width)
    annot.update()
    return {"type": "drawn", "page": page.number, "rect": list(annot.rect)}


def place_typed(page: pymupdf.Page, rect: list[float], name: str, *,
                font: str = "japan", size: float = 20,
                colour: str = "#12305e") -> dict:
    """Place a typed signature as text on the page."""
    page.insert_htmlbox(
        pymupdf.Rect(rect_to_page(page, rect)),
        f'<div style="font-size:{size}pt;color:{colour};'
        f'font-family:serif">{_escape(name)}</div>',
    )
    return {"type": "typed", "page": page.number, "rect": list(rect)}


def place_image(page: pymupdf.Page, rect: list[float], data: bytes) -> dict:
    """Place a scanned signature image."""
    page.insert_image(
        pymupdf.Rect(rect_to_page(page, rect)), stream=data, keep_proportion=True,
    )
    return {"type": "image", "page": page.number, "rect": list(rect)}


def decode_data_url(value: str) -> bytes:
    if "," in value and value.startswith("data:"):
        value = value.split(",", 1)[1]
    return base64.b64decode(value)


def signature_block(name: str, *, reason: str = "", place: str = "") -> str:
    """The text that usually accompanies a signature."""
    stamped = datetime.datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [name, stamped]
    if reason:
        lines.append(f"理由: {reason}")
    if place:
        lines.append(f"場所: {place}")
    return "\n".join(lines)


# ---------------------------------------------------------------- digital


def digital_signature_state(doc: pymupdf.Document) -> dict:
    """Report existing digital signatures, and whether we can add one.

    PyMuPDF can read and validate signature fields but cannot create a
    certificate-based signature, so the app says so instead of offering a
    button that would produce something weaker than it looks.
    """
    fields = []
    for page in doc:
        for widget in page.widgets():
            if widget.field_type != pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                continue
            fields.append({
                "name": widget.field_name or "",
                "page": page.number,
                "rect": list(widget.rect),
                "signed": bool(widget.field_value),
            })
    return {
        "fields": fields,
        "signed": sum(1 for f in fields if f["signed"]),
        # Creating certificate signatures needs a signing library and a
        # certificate store; neither is present, so this is honest rather
        # than a placeholder.
        "canSign": False,
        "note": (
            "電子証明書によるデジタル署名（改ざん検知つき）はこのアプリでは作成できません。"
            "ここで置ける署名は「手書き・タイプ・画像」の見た目だけのもので、改ざん検知の効力はありません。"
        ),
    }


def add_signature_field(page: pymupdf.Page, rect: list[float], name: str) -> dict:
    """Create an empty signature field for someone else to sign later."""
    widget = pymupdf.Widget()
    widget.rect = pymupdf.Rect(rect_to_page(page, rect))
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    widget.field_name = name or f"signature_{page.number}"
    added = page.add_widget(widget)
    return {"name": widget.field_name, "xref": added.xref, "page": page.number}


def certify(doc: pymupdf.Document, *, allow: str = "annotations") -> dict:
    """Record the intended level of later change (a document permissions note).

    Without a certificate this is a declaration, not an enforced lock, and the
    return value says as much.
    """
    levels = {"none": 1, "fill": 2, "annotations": 3}
    doc.xref_set_key(
        doc.pdf_catalog(), "PDFStudioCertifyIntent",
        pymupdf.get_pdf_str(allow),
    )
    return {
        "level": levels.get(allow, 3),
        "enforced": False,
        "note": "証明書が無いため、この設定は宣言であって変更を強制的に防ぐものではありません。",
    }


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
