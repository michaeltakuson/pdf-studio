"""AcroForm fields: creating them, filling them, and getting the data out.

Only AcroForm is supported. XFA — Adobe's XML form format — is deliberately not:
it was removed in PDF 2.0 and no other viewer opens it, which is the usual
reason a form "won't open".
"""

from __future__ import annotations

import csv
import io
import json

import pymupdf

from .common import hex_to_rgb, rect_to_page, rect_to_view, rgb_to_hex

FIELD_TYPES = {
    "text": pymupdf.PDF_WIDGET_TYPE_TEXT,
    "checkbox": pymupdf.PDF_WIDGET_TYPE_CHECKBOX,
    "radio": pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON,
    "dropdown": pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
    "list": pymupdf.PDF_WIDGET_TYPE_LISTBOX,
    "button": pymupdf.PDF_WIDGET_TYPE_BUTTON,
    "signature": pymupdf.PDF_WIDGET_TYPE_SIGNATURE,
}
TYPE_NAMES = {v: k for k, v in FIELD_TYPES.items()}


def read_fields(doc: pymupdf.Document) -> list[dict]:
    fields = []
    for page in doc:
        for widget in page.widgets():
            fields.append({
                "name": widget.field_name or "",
                "label": widget.field_label or "",
                "type": TYPE_NAMES.get(widget.field_type, "text"),
                "value": widget.field_value,
                "options": list(widget.choice_values or []),
                "rect": rect_to_view(page, widget.rect),
                "page": page.number,
                "required": bool(widget.field_flags & 2),
                "readOnly": bool(widget.field_flags & 1),
                "maxLength": widget.text_maxlen or 0,
                "fontSize": widget.text_fontsize or 0,
                "colour": rgb_to_hex(widget.text_color) if widget.text_color else "#000000",
                "border": rgb_to_hex(widget.border_color) if widget.border_color else None,
                "fill": rgb_to_hex(widget.fill_color) if widget.fill_color else None,
                "xref": widget.xref,
            })
    return fields


def has_xfa(doc: pymupdf.Document) -> bool:
    try:
        kind, _ = doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")
    except Exception:
        return False
    # A missing key comes back as the string "null", which is truthy.
    return kind not in ("null", "unknown")


def _apply(widget: pymupdf.Widget, spec: dict) -> None:
    widget.field_name = spec.get("name") or widget.field_name
    if spec.get("label"):
        widget.field_label = spec["label"]
    if spec.get("value") is not None:
        widget.field_value = spec["value"]
    if spec.get("options"):
        widget.choice_values = list(spec["options"])
    if spec.get("maxLength"):
        widget.text_maxlen = int(spec["maxLength"])
    widget.text_fontsize = float(spec.get("fontSize") or 0)
    # The built-in CJK face keeps Japanese from silently vanishing in fields.
    widget.text_font = spec.get("font") or "japan"
    widget.text_color = hex_to_rgb(spec.get("colour") or "#000000")
    if spec.get("border"):
        widget.border_color = hex_to_rgb(spec["border"])
    if spec.get("fill"):
        widget.fill_color = hex_to_rgb(spec["fill"])

    flags = 0
    if spec.get("readOnly"):
        flags |= 1
    if spec.get("required"):
        flags |= 2
    widget.field_flags = flags


def create_field(page: pymupdf.Page, spec: dict) -> dict:
    kind = spec.get("type", "text")
    if kind not in FIELD_TYPES:
        raise ValueError(f"未対応のフィールド種別: {kind}")
    widget = pymupdf.Widget()
    widget.rect = pymupdf.Rect(rect_to_page(page, spec["rect"]))
    widget.field_type = FIELD_TYPES[kind]
    widget.field_name = spec.get("name") or f"field_{page.number}_{int(widget.rect.x0)}"
    _apply(widget, spec)
    # add_widget returns a plain Annot, so the field details come from the spec.
    added = page.add_widget(widget)
    return {"name": widget.field_name, "xref": added.xref, "page": page.number}


def update_field(doc: pymupdf.Document, xref: int, patch: dict) -> bool:
    for page in doc:
        for widget in page.widgets():
            if widget.xref != xref:
                continue
            if patch.get("rect"):
                widget.rect = pymupdf.Rect(rect_to_page(page, patch["rect"]))
            _apply(widget, {**{
                "name": widget.field_name,
                "fontSize": widget.text_fontsize,
            }, **patch})
            widget.update()
            return True
    return False


def delete_field(doc: pymupdf.Document, xref: int) -> bool:
    for page in doc:
        for widget in page.widgets():
            if widget.xref == xref:
                page.delete_widget(widget)
                return True
    return False


def fill(doc: pymupdf.Document, values: dict) -> int:
    """Set field values by name."""
    filled = 0
    for page in doc:
        for widget in page.widgets():
            name = widget.field_name
            if name not in values:
                continue
            widget.field_value = values[name]
            widget.update()
            filled += 1
    return filled


def autodetect(page: pymupdf.Page, *, height: float = 18.0) -> list[dict]:
    """Turn ruled lines and boxes on a printed form into real fields.

    Looks for horizontal rules wide enough to write on — the usual shape of a
    paper form that has been scanned or exported to PDF.
    """
    found = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.width < 40:
            continue
        if rect.height <= 2:  # a rule to write above
            found.append([rect.x0, rect.y0 - height, rect.x1, rect.y0])
        elif 12 <= rect.height <= 40 and rect.width > 60:  # a box to write in
            found.append(list(rect))
    found = [rect_to_view(page, box) for box in found]
    # Drop overlaps so one visual box does not become two fields.
    unique: list[list[float]] = []
    for box in sorted(found, key=lambda b: (round(b[1]), b[0])):
        if any(_overlaps(box, existing) for existing in unique):
            continue
        unique.append(box)
    return [{"rect": box, "type": "text", "page": page.number} for box in unique]


def _overlaps(a: list[float], b: list[float]) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


# ---------------------------------------------------------------- data in/out


def to_fdf(doc: pymupdf.Document, name: str) -> str:
    """FDF carries form data alone, the way XFDF carries annotations alone."""
    rows = []
    for field in read_fields(doc):
        if field["type"] in ("button", "signature"):
            continue
        value = field["value"]
        if value is None:
            value = ""
        rows.append(
            f"<< /T ({_escape(field['name'])}) /V ({_escape(str(value))}) >>"
        )
    body = "\n".join(rows)
    return (
        "%FDF-1.2\n1 0 obj\n<< /FDF << /Fields [\n"
        f"{body}\n] /F ({_escape(name)}) >> >>\nendobj\n"
        "trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def to_csv(doc: pymupdf.Document) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["フィールド名", "種別", "値", "ページ", "必須", "読み取り専用"])
    for field in read_fields(doc):
        writer.writerow([
            field["name"], field["type"], field["value"] if field["value"] is not None else "",
            field["page"] + 1, "○" if field["required"] else "",
            "○" if field["readOnly"] else "",
        ])
    return buffer.getvalue()


def to_json(doc: pymupdf.Document) -> str:
    values = {f["name"]: f["value"] for f in read_fields(doc) if f["name"]}
    return json.dumps(values, ensure_ascii=False, indent=2)


def from_fdf(text: str) -> dict:
    """Read /T and /V pairs back out of an FDF file."""
    values: dict[str, str] = {}
    for chunk in text.split("/T (")[1:]:
        name, _, rest = chunk.partition(")")
        if "/V (" not in rest:
            continue
        value = rest.split("/V (", 1)[1].split(")", 1)[0]
        values[_unescape(name)] = _unescape(value)
    return values


def _escape(text: str) -> str:
    return str(text).replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _unescape(text: str) -> str:
    return text.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")


def collate(documents: list[tuple[str, pymupdf.Document]]) -> str:
    """One row per filled-in copy — the point of collecting form responses."""
    names: list[str] = []
    rows = []
    for label, doc in documents:
        values = {f["name"]: f["value"] for f in read_fields(doc) if f["name"]}
        for key in values:
            if key not in names:
                names.append(key)
        rows.append((label, values))

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["ファイル", *names])
    for label, values in rows:
        writer.writerow([label, *[values.get(name, "") for name in names]])
    return buffer.getvalue()
