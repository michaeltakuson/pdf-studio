"""The JS <-> Python boundary for the browser-only build.

This replaces backend/main.py's FastAPI routes. There is no HTTP here: the
browser calls `dispatch(action, payload)` directly through Pyodide, where
`payload` is a JS object already converted by `pyodide.toPy()` — nested dicts
and lists arrive as Python dicts and lists, and any binary field (a
Uint8Array) arrives as a `memoryview`, which is why every handler that reads
uploaded bytes does `bytes(payload["xxxB64_or_raw"])` rather than base64
decoding: there is no wire format to decode, the object graph crosses
directly.

Each action mirrors one FastAPI route from the server build, function for
function, so this file is best read side by side with backend/main.py.
Binary-producing actions return `(filename, media_type, data)`; everything
else returns a plain JSON-shaped dict. `dispatch()` is the only function the
JS side calls; it is what turns either shape into the uniform response the
fetch shim expects.
"""

from __future__ import annotations

import json

import pymupdf

from . import accessibility, annots, compare, content, export, forms, measure, pages, session, signing
from .common import page_info, rect_to_page, to_view


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def _doc(payload: dict) -> session.Doc:
    doc_id = payload.get("docId")
    try:
        return session.get(doc_id)
    except KeyError:
        raise ApiError(404, "この文書は開かれていません")


def _describe(entry: session.Doc) -> dict:
    doc = entry.doc
    items = annots.read_document(doc)
    # What the browser is about to hold is, by definition, what is in the file.
    entry.baseline = annots.baseline_of(items)
    return {
        "id": entry.id,
        "name": entry.name,
        "pageCount": doc.page_count,
        "pages": [page_info(page) for page in doc],
        "toc": doc.get_toc(simple=True),
        "annots": items,
        "metadata": doc.metadata or {},
        "isEncrypted": doc.is_encrypted,
        "needsPass": doc.needs_pass,
        "undoDepth": len(entry.snapshots),
    }


def _sync(entry: session.Doc, payload: dict, required: bool = False) -> int:
    """Bring the file's markup up to date with what the browser sent.

    Every operation that rewrites the document does this first, so nothing the
    user drew a moment ago is lost when the page under it changes.
    """
    items = payload.get("annots")
    if items is None and not required:
        return 0
    return annots.write_document(entry.doc, items or [], entry.baseline)


def _reload(entry: session.Doc, backup: str | None = None) -> dict:
    payload = _describe(entry)
    if backup is not None:
        payload["backup"] = backup
    return payload


def _bytes(value) -> bytes:
    """Normalise a payload field that may be a memoryview, bytearray or str."""
    if value is None:
        return b""
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


# ==================================================================== JSON actions


def open_document(payload: dict) -> dict:
    name = payload.get("name") or "untitled.pdf"
    data = _bytes(payload.get("data"))
    password = payload.get("password") or ""
    try:
        entry = session.create(name, data, password)
    except session.PasswordRequired as exc:
        raise ApiError(401, str(exc))
    except Exception as exc:
        raise ApiError(400, f"PDFを開けませんでした: {exc}")
    result = _describe(entry)
    result["wasProtected"] = entry.was_protected
    return result


def new_document(payload: dict) -> dict:
    entry = session.create_blank(
        payload.get("name") or "無題.pdf",
        float(payload.get("width", 595)),
        float(payload.get("height", 842)),
    )
    return _describe(entry)


def describe_document(payload: dict) -> dict:
    return _describe(_doc(payload))


def save_annots(payload: dict) -> dict:
    entry = _doc(payload)
    count = _sync(entry, payload, required=True)
    return {"written": count}


def search(payload: dict) -> dict:
    entry = _doc(payload)
    needle = (payload.get("query") or "").strip()
    if not needle:
        return {"hits": []}
    case_sensitive = bool(payload.get("caseSensitive"))
    hits = []
    relaxed_used = False
    for page in entry.doc:
        quads = page.search_for(needle, quads=True, flags=pymupdf.TEXTFLAGS_SEARCH)
        if not quads:
            quads = content.search_relaxed(page, needle)
            relaxed_used = relaxed_used or bool(quads)
        for quad in quads:
            if case_sensitive and needle not in page.get_textbox(quad.rect):
                continue
            found = to_view(page, {
                "quads": [[c for p in (quad.ul, quad.ur, quad.ll, quad.lr) for c in p]],
                "rect": list(quad.rect),
            })
            hits.append({
                "page": page.number,
                "quad": found["quads"][0],
                "rect": found["rect"],
            })
    return {"hits": hits, "relaxed": relaxed_used}


def flatten(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload, required=True)
    backup = entry.snapshot("before-flatten")
    entry.doc.bake(annots=True, widgets=bool(payload.get("widgets", False)))
    entry.commit()
    return _reload(entry, backup)


def clear_annots(payload: dict) -> dict:
    entry = _doc(payload)
    backup = entry.snapshot("before-clear")
    removed = len(entry.baseline)
    annots.write_document(entry.doc, [], entry.baseline)
    entry.commit()
    result = _reload(entry, backup)
    result["removed"] = removed
    return result


def import_xfdf(payload: dict) -> dict:
    entry = _doc(payload)
    raw = payload.get("xml") or ""
    try:
        items = export.from_xfdf(entry.doc, raw)
    except Exception as exc:
        raise ApiError(400, f"XFDFを読み込めませんでした: {exc}")
    return {"annots": items}


def page_action(payload: dict) -> dict:
    entry = _doc(payload)
    action = payload.get("action")
    targets = [int(p) for p in (payload.get("pages") or [])]
    _sync(entry, payload)
    backup = entry.snapshot(f"before-{action}")
    try:
        if action == "rotate":
            pages.rotate(entry.doc, targets or list(range(entry.doc.page_count)),
                         int(payload.get("degrees", 90)))
        elif action == "delete":
            pages.delete(entry.doc, targets)
        elif action == "duplicate":
            pages.duplicate(entry.doc, targets)
        elif action == "move":
            pages.move(entry.doc, int(payload["from"]), int(payload["to"]))
        elif action == "blank":
            pages.insert_blank(
                entry.doc, int(payload.get("at", entry.doc.page_count)),
                float(payload.get("width", 595)), float(payload.get("height", 842)),
            )
        elif action == "crop":
            pages.crop(entry.doc, targets, payload["rect"])
        elif action == "reset-crop":
            pages.reset_crop(entry.doc, targets)
        elif action == "margins":
            pages.add_margins(
                entry.doc, targets or None,
                left=float(payload.get("left", 0)), top=float(payload.get("top", 0)),
                right=float(payload.get("right", 0)), bottom=float(payload.get("bottom", 0)),
            )
        elif action == "reorder":
            order = [int(p) for p in payload.get("order") or []]
            if sorted(order) != list(range(entry.doc.page_count)):
                raise ApiError(400, "ページの並びが正しくありません")
            entry.doc.select(order)
        else:
            raise ApiError(404, f"未対応のページ操作: {action}")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(400, str(exc))
    entry.commit()
    return _reload(entry, backup)


def merge(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    backup = entry.snapshot("before-merge")
    try:
        added = 0
        sources = payload.get("files") or [{"data": payload.get("data")}]
        at = payload.get("at")
        for source in sources:
            count = pages.merge(entry.doc, _bytes(source.get("data")), None if at is None else int(at) + added)
            added += count
    except Exception as exc:
        raise ApiError(400, f"結合できませんでした: {exc}")
    entry.commit()
    result = _reload(entry, backup)
    result["added"] = added
    return result


def stamp_pages(payload: dict) -> dict:
    entry = _doc(payload)
    kind = payload.get("kind")
    _sync(entry, payload)
    backup = entry.snapshot(f"before-{kind}")
    try:
        if kind == "watermark":
            pages.watermark(
                entry.doc, payload.get("text") or "", pages=payload.get("pages"),
                colour=payload.get("colour", "#c0c0c0"),
                size=float(payload.get("size", 48)),
                opacity=float(payload.get("opacity", 0.25)),
                angle=float(payload.get("angle", 45)),
            )
        elif kind == "headerFooter":
            pages.header_footer(
                entry.doc, header=payload.get("header", ""), footer=payload.get("footer", ""),
                pages=payload.get("pages"), size=float(payload.get("size", 9)),
                colour=payload.get("colour", "#555555"),
            )
        elif kind == "bates":
            pages.bates(
                entry.doc, prefix=payload.get("prefix", ""),
                start=int(payload.get("start", 1)), digits=int(payload.get("digits", 6)),
                suffix=payload.get("suffix", ""),
            )
        else:
            raise ApiError(404, f"未対応の種類: {kind}")
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(400, str(exc))
    entry.commit()
    return _reload(entry, backup)


def redact_apply(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload, required=True)
    backup = entry.snapshot("before-redaction")
    result = pages.apply_redactions(entry.doc, images=bool(payload.get("images", True)))
    if payload.get("scrub"):
        pages.scrub(entry.doc)
    entry.commit()
    out = _reload(entry, backup)
    out.update(result)
    return out


def redact_search(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    marked = pages.search_and_mark_redactions(
        entry.doc, payload.get("query", ""),
        fill=payload.get("fill", "#000000"), overlay=payload.get("overlay", ""),
    )
    entry.commit()
    out = _reload(entry)
    out["marked"] = marked
    return out


def scrub_document(payload: dict) -> dict:
    entry = _doc(payload)
    backup = entry.snapshot("before-scrub")
    options = {k: v for k, v in payload.items() if k not in ("docId", "annots")}
    pages.scrub(entry.doc, **options)
    entry.commit()
    return _reload(entry, backup)


def optimise_document(payload: dict) -> dict:
    entry = _doc(payload)
    backup = entry.snapshot("before-optimise")
    report = pages.optimise(entry.doc)
    entry.commit()
    out = _reload(entry, backup)
    out.update(report)
    out["actual"] = len(entry.bytes())
    return out


def text_blocks(payload: dict) -> dict:
    entry = _doc(payload)
    page_index = int(payload.get("page", 0))
    if page_index < 0 or page_index >= entry.doc.page_count:
        raise ApiError(404, "ページがありません")
    page = entry.doc[page_index]
    return {
        "lines": [
            {**b, "pageRect": b["rect"], "rect": to_view(page, {"rect": b["rect"]})["rect"]}
            for b in content.find_text_lines(page)
        ],
        "blocks": [
            {**b, "rect": to_view(page, {"rect": b["rect"]})["rect"]}
            for b in content.find_text_blocks(page)
        ],
        "images": [
            {**i, "rect": to_view(page, {"rect": i["rect"]})["rect"]}
            for i in content.list_images(page)
        ],
    }


def text_replace(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    backup = entry.snapshot("before-text-edit")
    page = entry.doc[int(payload["page"])]
    # `pageRect` and `origin` come straight from text.blocks, already in the
    # page's own frame; a bare `rect` is what the reader sees.
    rect = payload.get("pageRect") or rect_to_page(page, payload["rect"])
    content.replace_text(
        page, rect, payload.get("text", ""),
        size=float(payload.get("size", 11)),
        colour=payload.get("colour", "#000000"),
        align=int(payload.get("align", 0)),
        background=payload.get("background"),
        origin=payload.get("origin"),
        serif=bool(payload.get("serif")),
        bold=bool(payload.get("bold")),
    )
    content.subset(entry.doc)
    entry.commit()
    return _reload(entry, backup)


def text_search_replace(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    backup = entry.snapshot("before-search-replace")
    count = content.search_replace(
        entry.doc, payload.get("query", ""), payload.get("replacement", ""),
        colour=payload.get("colour", "#000000"),
    )
    entry.commit()
    out = _reload(entry, backup)
    out["replaced"] = count
    return out


def image_insert(payload: dict) -> dict:
    entry = _doc(payload)
    backup = entry.snapshot("before-image")
    page = entry.doc[int(payload.get("pageIndex", 0))]
    content.insert_image(page, payload["rect"], _bytes(payload.get("data")))
    entry.commit()
    return _reload(entry, backup)


def measure_compute(payload: dict) -> dict:
    try:
        return measure.measure(
            payload.get("kind", "distance"),
            payload.get("points") or [],
            payload.get("scale") or {},
            depth=float(payload.get("depth", 0)),
            precision=int(payload.get("precision", 2)),
        )
    except ValueError as exc:
        raise ApiError(400, str(exc))


def measure_calibrate(payload: dict) -> dict:
    points = payload.get("points") or []
    if len(points) < 2:
        raise ApiError(400, "2点を指定してください")
    return measure.calibrate(
        points[0], points[1],
        float(payload.get("realLength", 1)), payload.get("unit", "mm"),
    )


def takeoff(payload: dict) -> dict:
    items = payload.get("annots") or []
    return measure.summarise(items)


def fields_list(payload: dict) -> dict:
    entry = _doc(payload)
    return {"fields": forms.read_fields(entry.doc), "hasXfa": forms.has_xfa(entry.doc)}


def fields_add(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    page = entry.doc[int(payload.get("page", 0))]
    try:
        created = forms.create_field(page, payload)
    except ValueError as exc:
        raise ApiError(400, str(exc))
    entry.commit()
    return {"field": created, "fields": forms.read_fields(entry.doc)}


def fields_patch(payload: dict) -> dict:
    entry = _doc(payload)
    if not forms.update_field(entry.doc, int(payload["xref"]), payload):
        raise ApiError(404, "フィールドが見つかりません")
    entry.commit()
    return {"fields": forms.read_fields(entry.doc)}


def fields_delete(payload: dict) -> dict:
    entry = _doc(payload)
    if not forms.delete_field(entry.doc, int(payload["xref"])):
        raise ApiError(404, "フィールドが見つかりません")
    entry.commit()
    return {"fields": forms.read_fields(entry.doc)}


def fields_fill(payload: dict) -> dict:
    entry = _doc(payload)
    filled = forms.fill(entry.doc, payload.get("values") or {})
    entry.commit()
    return {"filled": filled, "fields": forms.read_fields(entry.doc)}


def fields_detect(payload: dict) -> dict:
    entry = _doc(payload)
    page = entry.doc[int(payload.get("page", 0))]
    candidates = forms.autodetect(page)
    if payload.get("create"):
        for index, spec in enumerate(candidates):
            forms.create_field(page, {**spec, "name": f"auto_{page.number}_{index}"})
        entry.commit()
        return {"created": len(candidates), "fields": forms.read_fields(entry.doc)}
    return {"candidates": candidates}


def fields_import(payload: dict) -> dict:
    entry = _doc(payload)
    raw = payload.get("text") or ""
    values = json.loads(raw) if raw.lstrip().startswith("{") else forms.from_fdf(raw)
    filled = forms.fill(entry.doc, values)
    entry.commit()
    return {"filled": filled, "fields": forms.read_fields(entry.doc)}


def compare_diff(payload: dict) -> dict:
    entry = _doc(payload)
    other = pymupdf.open("pdf", _bytes(payload.get("data")))
    try:
        items = compare.compare(other, entry.doc, author=payload.get("author", ""))
    finally:
        other.close()
    return {"annots": items, "differences": len(items)}


def signatures_state(payload: dict) -> dict:
    entry = _doc(payload)
    return signing.digital_signature_state(entry.doc)


def sign(payload: dict) -> dict:
    entry = _doc(payload)
    kind = payload.get("kind", "typed")
    page_index = int(payload.get("page", 0))
    rect = payload.get("rect")
    _sync(entry, payload)
    backup = entry.snapshot("before-signature")
    page = entry.doc[page_index]
    try:
        if kind == "drawn":
            result = signing.place_drawn(
                page, rect, payload.get("strokes") or [],
                colour=payload.get("colour", "#12305e"),
                width=float(payload.get("width", 1.6)),
            )
        elif kind == "image":
            result = signing.place_image(page, rect, signing.decode_data_url(payload.get("image", "")))
        elif kind == "field":
            result = signing.add_signature_field(page, rect, payload.get("name", ""))
        else:
            result = signing.place_typed(
                page, rect, payload.get("name", ""),
                size=float(payload.get("size", 20)),
                colour=payload.get("colour", "#12305e"),
            )
    except Exception as exc:
        raise ApiError(400, str(exc))
    if payload.get("block"):
        note = signing.signature_block(
            payload.get("name", ""), reason=payload.get("reason", ""),
            place=payload.get("place", ""),
        )
        page.insert_htmlbox(
            pymupdf.Rect(rect[0], rect[3] + 2, rect[2] + 90, rect[3] + 56),
            f'<div style="font-size:7.5pt;color:#555">{note}</div>'.replace("\n", "<br>"),
        )
    entry.commit()
    out = _reload(entry, backup)
    out["signature"] = result
    return out


def accessibility_audit(payload: dict) -> dict:
    entry = _doc(payload)
    return accessibility.audit(entry.doc)


def accessibility_autotag(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    backup = entry.snapshot("before-autotag")
    report = accessibility.autotag(entry.doc, language=payload.get("language", "ja-JP"))
    entry.commit()
    out = _reload(entry, backup)
    out.update(report)
    out["audit"] = accessibility.audit(entry.doc)
    return out


def accessibility_alt(payload: dict) -> dict:
    entry = _doc(payload)
    for item in payload.get("items") or []:
        accessibility.set_alt_text(entry.doc, int(item["xref"]), item.get("alt", ""))
    if payload.get("language"):
        accessibility.set_language(entry.doc, payload["language"])
    if payload.get("title"):
        metadata = entry.doc.metadata or {}
        metadata["title"] = payload["title"]
        entry.doc.set_metadata(metadata)
    entry.commit()
    return {"audit": accessibility.audit(entry.doc)}


def accessibility_order(payload: dict) -> dict:
    entry = _doc(payload)
    page_index = int(payload.get("pageIndex", 0))
    if page_index < 0 or page_index >= entry.doc.page_count:
        raise ApiError(404, "ページがありません")
    return {"blocks": accessibility.reading_order(entry.doc, page_index)}


def undo_structural(payload: dict) -> dict:
    """Step back over the last operation that rewrote the document itself."""
    entry = _doc(payload)
    label = entry.restore()
    if label is None:
        raise ApiError(400, "これ以上は元に戻せません")
    out = _describe(entry)
    out["restored"] = label
    return out


def from_images(payload: dict) -> dict:
    files = [(f.get("filename") or "image", _bytes(f.get("data"))) for f in payload.get("files") or []]
    if not files:
        raise ApiError(400, "画像が選ばれていません")
    try:
        data = pages.images_to_pdf(files)
    except ValueError as exc:
        raise ApiError(400, str(exc))
    stem = files[0][0].rsplit(".", 1)[0]
    entry = session.create(f"{stem}.pdf", data)
    return _describe(entry)


def compress_document(payload: dict) -> dict:
    entry = _doc(payload)
    _sync(entry, payload)
    backup = entry.snapshot("before-compress")
    report = pages.compress(
        entry.doc, dpi=int(payload.get("dpi", 150)), quality=int(payload.get("quality", 75)),
    )
    entry.commit()
    out = _reload(entry, backup)
    out.update(report)
    out["actual"] = len(entry.bytes())
    return out


def outline_set(payload: dict) -> dict:
    entry = _doc(payload)
    try:
        pages.set_outline(entry.doc, payload.get("toc") or [])
    except Exception as exc:
        raise ApiError(400, f"しおりを保存できませんでした: {exc}")
    return {"toc": entry.doc.get_toc(simple=True)}


def metadata_set(payload: dict) -> dict:
    entry = _doc(payload)
    metadata = dict(entry.doc.metadata or {})
    for key in ("title", "author", "subject", "keywords"):
        if key in payload:
            metadata[key] = payload.get(key) or ""
    entry.doc.set_metadata(metadata)
    return {"metadata": entry.doc.metadata or {}}


def ocr_apply(payload: dict) -> dict:
    """Lay recognised words under the page image as invisible, searchable text.

    The recognition itself runs in the browser (tesseract.js); this only
    receives the words and where they were found.
    """
    entry = _doc(payload)
    _sync(entry, payload)
    backup = entry.snapshot("before-ocr")
    font = content.body_font()
    total = 0
    for sheet in payload.get("pages") or []:
        index = int(sheet.get("page", 0))
        if index < 0 or index >= entry.doc.page_count:
            continue
        page = entry.doc[index]
        writer = pymupdf.TextWriter(page.rect)
        for word in sheet.get("words") or []:
            text = (word.get("text") or "").strip()
            box = word.get("rect")
            if not text or not box:
                continue
            x0, y0, x1, y1 = rect_to_page(page, box)
            size = max(4.0, (y1 - y0) * 0.82)
            natural = font.text_length(text, size) or 1
            # Stretch each word to the width it has in the picture, so a
            # selection drawn over the scan lines up with the letters.
            try:
                writer.append(pymupdf.Point(x0, y1 - (y1 - y0) * 0.2), text, font=font, fontsize=size * min(1.6, max(0.5, (x1 - x0) / natural)))
                total += len(text)
            except Exception:
                continue
        writer.write_text(page, render_mode=3)
    content.subset(entry.doc)
    entry.commit()
    out = _reload(entry, backup)
    out["characters"] = total
    return out


def page_text(payload: dict) -> dict:
    entry = _doc(payload)
    index = int(payload.get("page", 0))
    if index < 0 or index >= entry.doc.page_count:
        raise ApiError(404, "ページがありません")
    return {"text": entry.doc[index].get_text()}


def close_document(payload: dict) -> dict:
    session.close(payload.get("docId"))
    return {"ok": True}


# ==================================================================== binary actions


def _export(payload: dict):
    entry = _doc(payload)
    fmt = payload.get("fmt")
    items = payload.get("annots") or annots.read_document(entry.doc)
    stem = entry.name.rsplit(".", 1)[0]
    if fmt == "xfdf":
        return f"{stem}.xfdf", "application/vnd.adobe.xfdf", export.to_xfdf(entry.doc, items, entry.name).encode("utf-8")
    if fmt == "csv":
        return f"{stem}-注釈.csv", "text/csv", "﻿".encode() + export.to_csv(entry.doc, items).encode("utf-8")
    if fmt == "markdown":
        text = export.to_markdown(entry.doc, items, entry.name, payload.get("colourTags") or {})
        return f"{stem}-注釈.md", "text/markdown", text.encode("utf-8")
    if fmt == "summary":
        return f"{stem}-注釈一覧.pdf", "application/pdf", export.summary_pdf(entry.doc, items, entry.name)
    raise ApiError(400, f"未対応の書き出し形式: {fmt}")


def _pages_extract(payload: dict):
    entry = _doc(payload)
    _sync(entry, payload)
    data = pages.extract(entry.doc, [int(p) for p in payload.get("pages") or []])
    stem = entry.name.rsplit(".", 1)[0]
    return f"{stem}-抽出.pdf", "application/pdf", data


def _protect(payload: dict):
    entry = _doc(payload)
    options = pages.save_options(
        user_password=payload.get("userPassword", ""),
        owner_password=payload.get("ownerPassword", ""),
        permissions=payload.get("permissions"),
    )
    stem = entry.name.rsplit(".", 1)[0]
    _sync(entry, payload)
    data = entry.doc.tobytes(garbage=3, deflate=True, **options)
    return f"{stem}-保護.pdf", "application/pdf", data


def _fields_export(payload: dict):
    entry = _doc(payload)
    fmt = payload.get("fmt")
    stem = entry.name.rsplit(".", 1)[0]
    if fmt == "fdf":
        return f"{stem}.fdf", "application/vnd.fdf", forms.to_fdf(entry.doc, entry.name).encode("utf-8")
    if fmt == "csv":
        return f"{stem}-フォーム.csv", "text/csv", "﻿".encode() + forms.to_csv(entry.doc).encode("utf-8")
    if fmt == "json":
        return f"{stem}-フォーム.json", "application/json", forms.to_json(entry.doc).encode("utf-8")
    raise ApiError(400, f"未対応の形式: {fmt}")


def _fields_collate(payload: dict):
    entry = _doc(payload)
    documents = []
    opened = []
    try:
        for item in payload.get("files") or []:
            doc = pymupdf.open("pdf", _bytes(item.get("data")))
            opened.append(doc)
            documents.append((item.get("filename") or "?", doc))
        data = "﻿".encode() + forms.collate(documents).encode("utf-8")
    finally:
        for doc in opened:
            doc.close()
    stem = entry.name.rsplit(".", 1)[0]
    return f"{stem}-回答集計.csv", "text/csv", data


def _compare_overlay(payload: dict):
    entry = _doc(payload)
    other = pymupdf.open("pdf", _bytes(payload.get("data")))
    try:
        data = compare.overlay(other, entry.doc)
    finally:
        other.close()
    stem = entry.name.rsplit(".", 1)[0]
    return f"{stem}-重ね合わせ.pdf", "application/pdf", data


def _takeoff_csv(payload: dict):
    entry = _doc(payload)
    items = payload.get("annots") or []
    summary = measure.summarise(items)
    data = "﻿".encode() + measure.to_csv(summary, items).encode("utf-8")
    stem = entry.name.rsplit(".", 1)[0]
    return f"{stem}-数量拾い.csv", "text/csv", data


def _file_bytes(payload: dict):
    entry = _doc(payload)
    return f"{entry.name}", "application/pdf", annots.view_bytes(entry.doc)


def _stem(entry: session.Doc) -> str:
    return entry.name.rsplit(".", 1)[0]


def _nup(payload: dict):
    entry = _doc(payload)
    _sync(entry, payload)
    data = pages.nup(entry.doc, int(payload.get("perSheet", 2)), border=bool(payload.get("border", True)))
    return f"{_stem(entry)}-{int(payload.get('perSheet', 2))}up.pdf", "application/pdf", data


def _split(payload: dict):
    entry = _doc(payload)
    _sync(entry, payload)
    try:
        data = pages.split(entry.doc, _stem(entry), ranges=payload.get("ranges", ""),
                           every=int(payload.get("every") or 0))
    except ValueError as exc:
        raise ApiError(400, str(exc))
    return f"{_stem(entry)}-分割.zip", "application/zip", data


def _images(payload: dict):
    entry = _doc(payload)
    _sync(entry, payload)
    return pages.to_images(entry.doc, _stem(entry), payload.get("pages"),
                           dpi=int(payload.get("dpi", 150)), fmt=payload.get("format", "png"))


def _text(payload: dict):
    entry = _doc(payload)
    return f"{_stem(entry)}.txt", "text/plain", ("\ufeff" + pages.to_text(entry.doc)).encode("utf-8")


def _extract_ranges(payload: dict):
    entry = _doc(payload)
    _sync(entry, payload)
    try:
        groups = pages.parse_ranges(payload.get("ranges", ""), entry.doc.page_count)
    except ValueError as exc:
        raise ApiError(400, str(exc))
    wanted = [index for group in groups for index in group]
    out = pymupdf.open()
    for index in wanted:
        out.insert_pdf(entry.doc, from_page=index, to_page=index)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return f"{_stem(entry)}-抽出.pdf", "application/pdf", data


def _download(payload: dict):
    entry = _doc(payload)
    _sync(entry, payload)
    name = entry.name if entry.name.lower().endswith(".pdf") else f"{entry.name}.pdf"
    return name, "application/pdf", entry.bytes()


# ==================================================================== dispatch table

_JSON_ROUTES = {
    "open": open_document,
    "new": new_document,
    "describe": describe_document,
    "annots.save": save_annots,
    "search": search,
    "flatten": flatten,
    "clear-annots": clear_annots,
    "import-xfdf": import_xfdf,
    "pages.action": page_action,
    "merge": merge,
    "stamp-pages": stamp_pages,
    "redact.apply": redact_apply,
    "redact.search": redact_search,
    "scrub": scrub_document,
    "optimise": optimise_document,
    "text.blocks": text_blocks,
    "text.replace": text_replace,
    "text.search-replace": text_search_replace,
    "image.insert": image_insert,
    "measure.compute": measure_compute,
    "measure.calibrate": measure_calibrate,
    "takeoff": takeoff,
    "fields.list": fields_list,
    "fields.add": fields_add,
    "fields.patch": fields_patch,
    "fields.delete": fields_delete,
    "fields.fill": fields_fill,
    "fields.detect": fields_detect,
    "fields.import": fields_import,
    "compare.diff": compare_diff,
    "signatures.state": signatures_state,
    "sign": sign,
    "accessibility.audit": accessibility_audit,
    "accessibility.autotag": accessibility_autotag,
    "accessibility.alt": accessibility_alt,
    "accessibility.order": accessibility_order,
    "close": close_document,
    "undo": undo_structural,
    "from-images": from_images,
    "compress": compress_document,
    "outline.set": outline_set,
    "metadata.set": metadata_set,
    "ocr.apply": ocr_apply,
    "page.text": page_text,
}

_BINARY_ROUTES = {
    "export": _export,
    "pages.extract": _pages_extract,
    "protect": _protect,
    "fields.export": _fields_export,
    "fields.collate": _fields_collate,
    "compare.overlay": _compare_overlay,
    "takeoff.csv": _takeoff_csv,
    "file": _file_bytes,
    "download": _download,
    "nup": _nup,
    "split": _split,
    "images": _images,
    "text": _text,
    "pages.extract-ranges": _extract_ranges,
}


try:  # only present inside Pyodide >= 0.28
    from pyodide.ffi import jsnull as _JSNULL
except ImportError:  # pragma: no cover - the server build / unit tests
    _JSNULL = None


def _unjs(value):
    """Turn Pyodide's JsNull (what a JS `null` becomes under toPy) into None.

    Without this, a null anywhere in an annotation (an unset colour, a missing
    link target...) survives into the document and later fails with
    "Object of type JsNull is not JSON serializable" when the save writes it out.
    """
    if _JSNULL is not None and value is _JSNULL:
        return None
    if isinstance(value, dict):
        return {k: _unjs(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unjs(v) for v in value]
    return value


def dispatch(action: str, payload) -> dict:
    """The one function the browser calls. `payload` is a toPy()-converted dict."""
    payload = _unjs(dict(payload)) if payload is not None else {}
    try:
        if action in _JSON_ROUTES:
            body = _JSON_ROUTES[action](payload)
            return {"status": 200, "json": body}
        if action in _BINARY_ROUTES:
            filename, media_type, data = _BINARY_ROUTES[action](payload)
            return {
                "status": 200,
                "filename": filename,
                "mediaType": media_type,
                "data": data,
            }
        return {"status": 404, "json": {"detail": f"未対応の操作です: {action}"}}
    except ApiError as exc:
        return {"status": exc.status, "json": {"detail": exc.message}}
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a toast
        return {"status": 500, "json": {"detail": str(exc)}}
