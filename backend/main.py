from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import quote

import pymupdf
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import (
    accessibility, annots, compare, content, export, forms, measure, pages,
    session, signing,
)
from .common import page_info, rect_to_page, to_view

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="PDF Studio")


def _doc(doc_id: str) -> session.Doc:
    try:
        return session.get(doc_id)
    except KeyError:
        raise HTTPException(404, "この文書は開かれていません")


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


@app.post("/api/open")
async def open_document(file: UploadFile = File(...), password: str = Form("")):
    data = await file.read()
    try:
        entry = session.create(file.filename or "untitled.pdf", data, password)
    except session.PasswordRequired as exc:
        # 401 rather than 400: the request was fine, the credential was not.
        # The browser uses this to ask for a password and try again.
        raise HTTPException(401, str(exc))
    except Exception as exc:
        raise HTTPException(400, f"PDFを開けませんでした: {exc}")
    payload = _describe(entry)
    payload["wasProtected"] = entry.was_protected
    return payload


@app.post("/api/new")
async def new_document(body: dict = Body(default={})):
    entry = session.create_blank(
        body.get("name") or "無題.pdf",
        float(body.get("width", 595)),
        float(body.get("height", 842)),
    )
    return _describe(entry)


@app.get("/api/doc/{doc_id}")
async def describe(doc_id: str):
    return _describe(_doc(doc_id))


@app.get("/api/doc/{doc_id}/file")
async def raw_file(doc_id: str):
    entry = _doc(doc_id)
    return FileResponse(
        entry.path,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/doc/{doc_id}/annots")
async def save_annots(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        count = annots.write_document(entry.doc, body.get("annots") or [])
        entry.commit()
    return {"written": count}


@app.post("/api/doc/{doc_id}/search")
async def search(doc_id: str, body: dict = Body(...)):
    """Locate a phrase and return quads in model space.

    PyMuPDF returns one quad per matched line, already following the text's
    own baseline, so rotated or multi-line hits stay accurate — this is what
    makes 'search and mark up everything' reliable.
    """
    entry = _doc(doc_id)
    needle = (body.get("query") or "").strip()
    if not needle:
        return {"hits": []}
    # MuPDF's own search is case-insensitive; there is no flag to change that,
    # so case-sensitive matching is done afterwards against the found text.
    case_sensitive = bool(body.get("caseSensitive"))
    hits = []
    relaxed_used = False
    with entry.lock:
        for page in entry.doc:
            quads = page.search_for(needle, quads=True, flags=pymupdf.TEXTFLAGS_SEARCH)
            if not quads:
                # OCR output separates Japanese characters with spaces, and any
                # document can break a phrase across a line, so fall back to a
                # whitespace-insensitive scan before reporting no match.
                quads = content.search_relaxed(page, needle)
                relaxed_used = relaxed_used or bool(quads)
            for quad in quads:
                if case_sensitive and needle not in page.get_textbox(quad.rect):
                    continue
                # Hits come back in page space; the browser draws in view space.
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


@app.post("/api/doc/{doc_id}/flatten")
async def flatten(doc_id: str, body: dict = Body(default={})):
    """Burn annotations into the page content.

    This is one-way: afterwards the marks are part of the page and cannot be
    selected, moved or deleted. A snapshot is taken first so the editable
    version is always recoverable.
    """
    entry = _doc(doc_id)
    with entry.lock:
        annots.write_document(entry.doc, body.get("annots") or [])
        entry.commit()
        backup = entry.snapshot("before-flatten")
        entry.doc.bake(annots=True, widgets=bool(body.get("widgets", False)))
        entry.commit()
    return _reload(entry, backup)


@app.post("/api/doc/{doc_id}/clear-annots")
async def clear_annots(doc_id: str):
    """Remove every markup annotation, leaving the page itself untouched."""
    entry = _doc(doc_id)
    with entry.lock:
        backup = entry.snapshot("before-clear")
        removed = len(annots.read_document(entry.doc))
        annots.write_document(entry.doc, [])
        entry.commit()
    payload = _reload(entry, backup)
    payload["removed"] = removed
    return payload


@app.post("/api/doc/{doc_id}/export/{fmt}")
async def export_annots(doc_id: str, fmt: str, body: dict = Body(default={})):
    entry = _doc(doc_id)
    items = body.get("annots") or annots.read_document(entry.doc)
    stem = entry.name.rsplit(".", 1)[0]

    with entry.lock:
        if fmt == "xfdf":
            data = export.to_xfdf(entry.doc, items, entry.name).encode("utf-8")
            media, filename = "application/vnd.adobe.xfdf", f"{stem}.xfdf"
        elif fmt == "csv":
            # A BOM keeps Excel from mangling Japanese in the CSV.
            data = "﻿".encode() + export.to_csv(entry.doc, items).encode("utf-8")
            media, filename = "text/csv", f"{stem}-注釈.csv"
        elif fmt == "markdown":
            text = export.to_markdown(
                entry.doc, items, entry.name, body.get("colourTags") or {}
            )
            data = text.encode("utf-8")
            media, filename = "text/markdown", f"{stem}-注釈.md"
        elif fmt == "summary":
            data = export.summary_pdf(entry.doc, items, entry.name)
            media, filename = "application/pdf", f"{stem}-注釈一覧.pdf"
        else:
            raise HTTPException(400, f"未対応の書き出し形式: {fmt}")

    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition":
                f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@app.post("/api/doc/{doc_id}/import-xfdf")
async def import_xfdf(doc_id: str, file: UploadFile = File(...)):
    entry = _doc(doc_id)
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        items = export.from_xfdf(entry.doc, raw)
    except Exception as exc:
        raise HTTPException(400, f"XFDFを読み込めませんでした: {exc}")
    return {"annots": items}


# ---------------------------------------------------------------- layer ③


def _reload(entry: session.Doc, backup: Path | None = None) -> dict:
    """Common response after a structural change: the viewer reloads from this."""
    payload = _describe(entry)
    if backup is not None:
        payload["backup"] = backup.name
    return payload


@app.post("/api/doc/{doc_id}/pages/{action}")
async def page_action(doc_id: str, action: str, body: dict = Body(default={})):
    entry = _doc(doc_id)
    targets = [int(p) for p in (body.get("pages") or [])]
    with entry.lock:
        # Persist pending markup first: page surgery moves annotations with the
        # pages, and unsaved ones would be lost.
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        backup = entry.snapshot(f"before-{action}")
        try:
            if action == "rotate":
                pages.rotate(entry.doc, targets, int(body.get("degrees", 90)))
            elif action == "delete":
                pages.delete(entry.doc, targets)
            elif action == "duplicate":
                pages.duplicate(entry.doc, targets)
            elif action == "move":
                pages.move(entry.doc, int(body["from"]), int(body["to"]))
            elif action == "blank":
                pages.insert_blank(
                    entry.doc, int(body.get("at", entry.doc.page_count)),
                    float(body.get("width", 595)), float(body.get("height", 842)),
                )
            elif action == "crop":
                pages.crop(entry.doc, targets, body["rect"])
            elif action == "reset-crop":
                pages.reset_crop(entry.doc, targets)
            else:
                raise HTTPException(404, f"未対応のページ操作: {action}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc))
        entry.commit()
    return _reload(entry, backup)


@app.post("/api/doc/{doc_id}/pages/extract")
async def extract_pages(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        data = pages.extract(entry.doc, [int(p) for p in body.get("pages") or []])
    stem = entry.name.rsplit(".", 1)[0]
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(stem + '-抽出.pdf')}"},
    )


@app.post("/api/doc/{doc_id}/merge")
async def merge_pdf(doc_id: str, file: UploadFile = File(...), at: int | None = None):
    entry = _doc(doc_id)
    data = await file.read()
    with entry.lock:
        backup = entry.snapshot("before-merge")
        try:
            added = pages.merge(entry.doc, data, at)
        except Exception as exc:
            raise HTTPException(400, f"結合できませんでした: {exc}")
        entry.commit()
    result = _reload(entry, backup)
    result["added"] = added
    return result


@app.post("/api/doc/{doc_id}/stamp-pages")
async def stamp_pages(doc_id: str, body: dict = Body(...)):
    """Watermark, header/footer and Bates numbering — page-wide overlays."""
    entry = _doc(doc_id)
    kind = body.get("kind")
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        backup = entry.snapshot(f"before-{kind}")
        try:
            if kind == "watermark":
                pages.watermark(
                    entry.doc, body.get("text") or "", pages=body.get("pages"),
                    colour=body.get("colour", "#c0c0c0"),
                    size=float(body.get("size", 48)),
                    opacity=float(body.get("opacity", 0.25)),
                    angle=float(body.get("angle", 45)),
                )
            elif kind == "headerFooter":
                pages.header_footer(
                    entry.doc, header=body.get("header", ""), footer=body.get("footer", ""),
                    pages=body.get("pages"), size=float(body.get("size", 9)),
                    colour=body.get("colour", "#555555"),
                )
            elif kind == "bates":
                pages.bates(
                    entry.doc, prefix=body.get("prefix", ""),
                    start=int(body.get("start", 1)), digits=int(body.get("digits", 6)),
                    suffix=body.get("suffix", ""),
                )
            else:
                raise HTTPException(404, f"未対応の種類: {kind}")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400, str(exc))
        entry.commit()
    return _reload(entry, backup)


@app.post("/api/doc/{doc_id}/redact/apply")
async def redact_apply(doc_id: str, body: dict = Body(default={})):
    """The step that actually deletes: marks become removed content."""
    entry = _doc(doc_id)
    with entry.lock:
        annots.write_document(entry.doc, body.get("annots") or [])
        entry.commit()
        backup = entry.snapshot("before-redaction")
        result = pages.apply_redactions(entry.doc, images=bool(body.get("images", True)))
        if body.get("scrub"):
            pages.scrub(entry.doc)
        entry.commit()
    payload = _reload(entry, backup)
    payload.update(result)
    return payload


@app.post("/api/doc/{doc_id}/redact/search")
async def redact_search(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        marked = pages.search_and_mark_redactions(
            entry.doc, body.get("query", ""),
            fill=body.get("fill", "#000000"), overlay=body.get("overlay", ""),
        )
        entry.commit()
    payload = _reload(entry)
    payload["marked"] = marked
    return payload


@app.post("/api/doc/{doc_id}/scrub")
async def scrub_document(doc_id: str, body: dict = Body(default={})):
    entry = _doc(doc_id)
    with entry.lock:
        backup = entry.snapshot("before-scrub")
        pages.scrub(entry.doc, **body)
        entry.commit()
    return _reload(entry, backup)


@app.post("/api/doc/{doc_id}/optimise")
async def optimise(doc_id: str):
    entry = _doc(doc_id)
    with entry.lock:
        backup = entry.snapshot("before-optimise")
        report = pages.optimise(entry.doc)
        entry.commit()
    payload = _reload(entry, backup)
    payload.update(report)
    payload["actual"] = entry.path.stat().st_size
    return payload


@app.post("/api/doc/{doc_id}/protect")
async def protect(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    options = pages.save_options(
        user_password=body.get("userPassword", ""),
        owner_password=body.get("ownerPassword", ""),
        permissions=body.get("permissions"),
    )
    stem = entry.name.rsplit(".", 1)[0]
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        data = entry.doc.tobytes(garbage=3, deflate=True, **options)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(stem + '-保護.pdf')}"},
    )


# ---------------------------------------------------------------- layer ②


@app.get("/api/ocr/status")
async def ocr_status():
    return content.tesseract_state()


@app.post("/api/doc/{doc_id}/ocr")
async def run_ocr(doc_id: str, body: dict = Body(default={})):
    entry = _doc(doc_id)
    with entry.lock:
        backup = entry.snapshot("before-ocr")
        try:
            report = content.ocr_document(
                entry.doc,
                language=body.get("language", "jpn+eng"),
                dpi=int(body.get("dpi", 300)),
                psm=body.get("layout", content.DEFAULT_PSM),
                pages=body.get("pages"),
                force=bool(body.get("force")),
            )
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))
        entry.commit()
    payload = _reload(entry, backup)
    payload.update(report)
    return payload


@app.get("/api/doc/{doc_id}/text/{page_index}")
async def page_text_blocks(doc_id: str, page_index: int):
    entry = _doc(doc_id)
    if page_index < 0 or page_index >= entry.doc.page_count:
        raise HTTPException(404, "ページがありません")
    with entry.lock:
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


@app.post("/api/doc/{doc_id}/text/replace")
async def replace_text(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        backup = entry.snapshot("before-text-edit")
        page = entry.doc[int(body["page"])]
        content.replace_text(
            page, rect_to_page(page, body["rect"]), body.get("text", ""),
            size=float(body.get("size", 11)),
            colour=body.get("colour", "#000000"),
            align=int(body.get("align", 0)),
            background=body.get("background"),
        )
        entry.commit()
    return _reload(entry, backup)


@app.post("/api/doc/{doc_id}/text/search-replace")
async def search_replace(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        backup = entry.snapshot("before-search-replace")
        count = content.search_replace(
            entry.doc, body.get("query", ""), body.get("replacement", ""),
            colour=body.get("colour", "#000000"),
        )
        entry.commit()
    payload = _reload(entry, backup)
    payload["replaced"] = count
    return payload


@app.post("/api/doc/{doc_id}/image")
async def add_image(doc_id: str, file: UploadFile = File(...),
                    page_index: int = 0, rect: str = ""):
    entry = _doc(doc_id)
    data = await file.read()
    try:
        box = [float(v) for v in rect.split(",")]
    except ValueError:
        raise HTTPException(400, "画像の配置範囲が不正です")
    with entry.lock:
        backup = entry.snapshot("before-image")
        content.insert_image(entry.doc[page_index], box, data)
        entry.commit()
    return _reload(entry, backup)


# ---------------------------------------------------------------- measurement


@app.post("/api/measure")
async def compute_measure(body: dict = Body(...)):
    """Stateless: the browser sends points and a scale, and gets the value."""
    try:
        return measure.measure(
            body.get("kind", "distance"),
            body.get("points") or [],
            body.get("scale") or {},
            depth=float(body.get("depth", 0)),
            precision=int(body.get("precision", 2)),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/measure/calibrate")
async def calibrate(body: dict = Body(...)):
    points = body.get("points") or []
    if len(points) < 2:
        raise HTTPException(400, "2点を指定してください")
    return measure.calibrate(
        points[0], points[1],
        float(body.get("realLength", 1)), body.get("unit", "mm"),
    )


@app.post("/api/doc/{doc_id}/takeoff")
async def takeoff(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    items = body.get("annots") or []
    summary = measure.summarise(items)
    if not body.get("csv"):
        return summary
    data = "﻿".encode() + measure.to_csv(summary, items).encode("utf-8")
    stem = entry.name.rsplit(".", 1)[0]
    return Response(
        content=data, media_type="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(stem + '-数量拾い.csv')}"},
    )


# ---------------------------------------------------------------- forms


@app.get("/api/doc/{doc_id}/fields")
async def list_fields(doc_id: str):
    entry = _doc(doc_id)
    with entry.lock:
        return {
            "fields": forms.read_fields(entry.doc),
            # XFA forms are the usual reason a form will not open elsewhere.
            "hasXfa": forms.has_xfa(entry.doc),
        }


@app.post("/api/doc/{doc_id}/fields")
async def add_field(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        page = entry.doc[int(body.get("page", 0))]
        try:
            created = forms.create_field(page, body)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        entry.commit()
    return {"field": created, "fields": forms.read_fields(entry.doc)}


@app.patch("/api/doc/{doc_id}/fields/{xref}")
async def patch_field(doc_id: str, xref: int, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        if not forms.update_field(entry.doc, xref, body):
            raise HTTPException(404, "フィールドが見つかりません")
        entry.commit()
    return {"fields": forms.read_fields(entry.doc)}


@app.delete("/api/doc/{doc_id}/fields/{xref}")
async def remove_field(doc_id: str, xref: int):
    entry = _doc(doc_id)
    with entry.lock:
        if not forms.delete_field(entry.doc, xref):
            raise HTTPException(404, "フィールドが見つかりません")
        entry.commit()
    return {"fields": forms.read_fields(entry.doc)}


@app.post("/api/doc/{doc_id}/fields/fill")
async def fill_fields(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        filled = forms.fill(entry.doc, body.get("values") or {})
        entry.commit()
    return {"filled": filled, "fields": forms.read_fields(entry.doc)}


@app.post("/api/doc/{doc_id}/fields/detect")
async def detect_fields(doc_id: str, body: dict = Body(default={})):
    entry = _doc(doc_id)
    with entry.lock:
        page = entry.doc[int(body.get("page", 0))]
        candidates = forms.autodetect(page)
        if body.get("create"):
            for index, spec in enumerate(candidates):
                forms.create_field(page, {**spec, "name": f"auto_{page.number}_{index}"})
            entry.commit()
            return {"created": len(candidates), "fields": forms.read_fields(entry.doc)}
    return {"candidates": candidates}


@app.post("/api/doc/{doc_id}/fields/export/{fmt}")
async def export_fields(doc_id: str, fmt: str):
    entry = _doc(doc_id)
    stem = entry.name.rsplit(".", 1)[0]
    with entry.lock:
        if fmt == "fdf":
            data = forms.to_fdf(entry.doc, entry.name).encode("utf-8")
            media, filename = "application/vnd.fdf", f"{stem}.fdf"
        elif fmt == "csv":
            data = "﻿".encode() + forms.to_csv(entry.doc).encode("utf-8")
            media, filename = "text/csv", f"{stem}-フォーム.csv"
        elif fmt == "json":
            data = forms.to_json(entry.doc).encode("utf-8")
            media, filename = "application/json", f"{stem}-フォーム.json"
        else:
            raise HTTPException(400, f"未対応の形式: {fmt}")
    return Response(
        content=data, media_type=media,
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.post("/api/doc/{doc_id}/fields/import")
async def import_fields(doc_id: str, file: UploadFile = File(...)):
    entry = _doc(doc_id)
    raw = (await file.read()).decode("utf-8", errors="replace")
    values = json.loads(raw) if raw.lstrip().startswith("{") else forms.from_fdf(raw)
    with entry.lock:
        filled = forms.fill(entry.doc, values)
        entry.commit()
    return {"filled": filled, "fields": forms.read_fields(entry.doc)}


@app.post("/api/doc/{doc_id}/fields/collate")
async def collate_fields(doc_id: str, files: list[UploadFile] = File(...)):
    entry = _doc(doc_id)
    documents = []
    opened = []
    try:
        for upload in files:
            doc = pymupdf.open("pdf", await upload.read())
            opened.append(doc)
            documents.append((upload.filename or "?", doc))
        data = "﻿".encode() + forms.collate(documents).encode("utf-8")
    finally:
        for doc in opened:
            doc.close()
    stem = entry.name.rsplit(".", 1)[0]
    return Response(
        content=data, media_type="text/csv",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(stem + '-回答集計.csv')}"},
    )


# ---------------------------------------------------------------- comparison


@app.post("/api/doc/{doc_id}/compare")
async def compare_documents(doc_id: str, file: UploadFile = File(...),
                            mode: str = "diff", author: str = ""):
    """`diff` returns cloud annotations; `overlay` returns a tinted PDF."""
    entry = _doc(doc_id)
    other = pymupdf.open("pdf", await file.read())
    try:
        with entry.lock:
            if mode == "overlay":
                data = compare.overlay(other, entry.doc)
                stem = entry.name.rsplit(".", 1)[0]
                return Response(
                    content=data, media_type="application/pdf",
                    headers={"Content-Disposition":
                             f"attachment; filename*=UTF-8''{quote(stem + '-重ね合わせ.pdf')}"},
                )
            items = compare.compare(other, entry.doc, author=author)
    finally:
        other.close()
    return {"annots": items, "differences": len(items)}


# ---------------------------------------------------------------- signatures


@app.get("/api/doc/{doc_id}/signatures")
async def signature_state(doc_id: str):
    entry = _doc(doc_id)
    with entry.lock:
        return signing.digital_signature_state(entry.doc)


@app.post("/api/doc/{doc_id}/sign")
async def sign(doc_id: str, body: dict = Body(...)):
    """Place a visible signature. This is a picture of a name, not a digital
    signature — see /signatures for what the app can and cannot do."""
    entry = _doc(doc_id)
    kind = body.get("kind", "typed")
    page_index = int(body.get("page", 0))
    rect = body.get("rect")
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        backup = entry.snapshot("before-signature")
        page = entry.doc[page_index]
        try:
            if kind == "drawn":
                result = signing.place_drawn(
                    page, rect, body.get("strokes") or [],
                    colour=body.get("colour", "#12305e"),
                    width=float(body.get("width", 1.6)),
                )
            elif kind == "image":
                result = signing.place_image(
                    page, rect, signing.decode_data_url(body.get("image", "")),
                )
            elif kind == "field":
                result = signing.add_signature_field(page, rect, body.get("name", ""))
            else:
                result = signing.place_typed(
                    page, rect, body.get("name", ""),
                    size=float(body.get("size", 20)),
                    colour=body.get("colour", "#12305e"),
                )
        except Exception as exc:
            raise HTTPException(400, str(exc))
        if body.get("block"):
            note = signing.signature_block(
                body.get("name", ""), reason=body.get("reason", ""),
                place=body.get("place", ""),
            )
            page.insert_htmlbox(
                pymupdf.Rect(rect[0], rect[3] + 2, rect[2] + 90, rect[3] + 56),
                f'<div style="font-size:7.5pt;color:#555">{note}</div>'.replace("\n", "<br>"),
            )
        entry.commit()
    payload = _reload(entry, backup)
    payload["signature"] = result
    return payload


# ---------------------------------------------------------------- accessibility


@app.get("/api/doc/{doc_id}/accessibility")
async def accessibility_audit(doc_id: str):
    entry = _doc(doc_id)
    with entry.lock:
        return accessibility.audit(entry.doc)


@app.post("/api/doc/{doc_id}/accessibility/autotag")
async def autotag(doc_id: str, body: dict = Body(default={})):
    entry = _doc(doc_id)
    with entry.lock:
        if body.get("annots") is not None:
            annots.write_document(entry.doc, body["annots"])
        backup = entry.snapshot("before-autotag")
        report = accessibility.autotag(entry.doc, language=body.get("language", "ja-JP"))
        entry.commit()
    payload = _reload(entry, backup)
    payload.update(report)
    payload["audit"] = accessibility.audit(entry.doc)
    return payload


@app.post("/api/doc/{doc_id}/accessibility/alt")
async def set_alt(doc_id: str, body: dict = Body(...)):
    entry = _doc(doc_id)
    with entry.lock:
        for item in body.get("items") or []:
            accessibility.set_alt_text(entry.doc, int(item["xref"]), item.get("alt", ""))
        if body.get("language"):
            accessibility.set_language(entry.doc, body["language"])
        if body.get("title"):
            metadata = entry.doc.metadata or {}
            metadata["title"] = body["title"]
            entry.doc.set_metadata(metadata)
        entry.commit()
    return {"audit": accessibility.audit(entry.doc)}


@app.get("/api/doc/{doc_id}/accessibility/order/{page_index}")
async def read_order(doc_id: str, page_index: int):
    entry = _doc(doc_id)
    if page_index < 0 or page_index >= entry.doc.page_count:
        raise HTTPException(404, "ページがありません")
    with entry.lock:
        return {"blocks": accessibility.reading_order(entry.doc, page_index)}


@app.get("/api/doc/{doc_id}/download")
async def download(doc_id: str):
    entry = _doc(doc_id)
    with entry.lock:
        entry.commit()
    name = entry.name if entry.name.lower().endswith(".pdf") else f"{entry.name}.pdf"
    return FileResponse(entry.path, media_type="application/pdf", filename=name)


@app.post("/api/doc/{doc_id}/close")
async def close_document(doc_id: str):
    session.close(doc_id)
    return {"ok": True}


@app.middleware("http")
async def no_cache(request, call_next):
    """Serve the app itself uncached so an edit is always the code that runs."""
    response = await call_next(request)
    if not request.url.path.startswith("/vendor/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def index():
    return FileResponse(FRONTEND / "index.html")


# The browser test harness lives with the tests, but has to be reachable as a
# module URL for the UI tests to import it.
TESTS = Path(__file__).resolve().parent.parent / "tests"
if TESTS.exists():
    app.mount("/tests", StaticFiles(directory=TESTS), name="tests")

app.mount("/", StaticFiles(directory=FRONTEND), name="frontend")
