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
    return {
        "id": entry.id,
        "name": entry.name,
        "pageCount": doc.page_count,
        "pages": [page_info(page) for page in doc],
        "toc": doc.get_toc(simple=True),
        "annots": annots.read_document(doc),
        "metadata": doc.metadata or {},
        "isEncrypted": doc.is_encrypted,
        "needsPass": doc.needs_pass,
    }


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
    count = annots.write_document(entry.doc, payload.get("annots") or [])
    entry.commit()
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
    annots.write_document(entry.doc, payload.get("annots") or [])
    entry.commit()
    backup = entry.snapshot("before-flatten")
    entry.doc.bake(annots=True, widgets=bool(payload.get("widgets", False)))
    entry.commit()
    return _reload(entry, backup)


def clear_annots(payload: dict) -> dict:
    entry = _doc(payload)
    backup = entry.snapshot("before-clear")
    removed = len(annots.read_document(entry.doc))
    annots.write_document(entry.doc, [])
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
    backup = entry.snapshot(f"before-{action}")
    try:
        if action == "rotate":
            pages.rotate(entry.doc, targets, int(payload.get("degrees", 90)))
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
    data = _bytes(payload.get("data"))
    backup = entry.snapshot("before-merge")
    try:
        added = pages.merge(entry.doc, data, payload.get("at"))
    except Exception as exc:
        raise ApiError(400, f"結合できませんでした: {exc}")
    entry.commit()
    result = _reload(entry, backup)
    result["added"] = added
    return result


def stamp_pages(payload: dict) -> dict:
    entry = _doc(payload)
    kind = payload.get("kind")
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    annots.write_document(entry.doc, payload.get("annots") or [])
    entry.commit()
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
    backup = entry.snapshot("before-text-edit")
    page = entry.doc[int(payload["page"])]
    content.replace_text(
        page, rect_to_page(page, payload["rect"]), payload.get("text", ""),
        size=float(payload.get("size", 11)),
        colour=payload.get("colour", "#000000"),
        align=int(payload.get("align", 0)),
        background=payload.get("background"),
    )
    entry.commit()
    return _reload(entry, backup)


def text_search_replace(payload: dict) -> dict:
    entry = _doc(payload)
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    if payload.get("annots") is not None:
        annots.write_document(entry.doc, payload["annots"])
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
    return f"{entry.name}", "application/pdf", entry.bytes()


def _download(payload: dict):
    entry = _doc(payload)
    entry.commit()
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
}


def dispatch(action: str, payload) -> dict:
    """The one function the browser calls. `payload` is a toPy()-converted dict."""
    payload = dict(payload) if payload is not None else {}
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
