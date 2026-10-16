"""Whole-file operations that turn one kind of document into another, or several into one.

Everything here takes file bytes in and gives file bytes (or a new open document) out; the bridge adds the
routes. There are no limits on how many files or how often: the only guard is against inputs so large that the
browser's memory would run out, which is reported as a plain message instead of a crash.
"""

from __future__ import annotations

import io
import re
import zipfile

import pymupdf

from . import pages as pages_mod

# One file bigger than this (after unzipping, for Office files) cannot be handled in a browser tab.
MAX_OFFICE_UNZIPPED = 400 * 1024 * 1024
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class ConvertError(Exception):
    pass


def sniff(name: str, data: bytes) -> str:
    """'docx' | 'pptx' | 'xlsx' | 'pdf' | 'image' | 'text' | 'ole' | 'unknown', by content first and then by name."""
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(OLE_MAGIC):
        return "ole"
    if head.startswith(b"PK"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return "unknown"
        if "word/document.xml" in names:
            return "docx"
        if "ppt/presentation.xml" in names:
            return "pptx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        return "unknown"
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext in ("png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"):
        return "image"
    if ext in ("txt", "csv", "md"):
        return "text"
    return "unknown"


def unzipped_size(data: bytes) -> int:
    try:
        return sum(i.file_size for i in zipfile.ZipFile(io.BytesIO(data)).infolist())
    except zipfile.BadZipFile:
        return len(data)


def office_to_pdf(name: str, data: bytes) -> tuple[bytes, list[str]]:
    """One Word, PowerPoint or Excel file to PDF bytes (plus any warnings)."""
    kind = sniff(name, data)
    if kind == "ole":
        raise ConvertError("古い形式（.doc / .ppt / .xls）は読み込めません。Word・PowerPoint・Excel で「名前を付けて保存」から新しい形式（.docx / .pptx / .xlsx）にしてください")
    if kind in ("docx", "pptx", "xlsx") and unzipped_size(data) > MAX_OFFICE_UNZIPPED:
        raise ConvertError("ファイルが大きすぎて、ブラウザの中では変換できません（展開後 400MB 超）")
    if kind == "docx":
        from . import from_docx
        return from_docx.convert(data, name), []
    if kind == "pptx":
        from . import from_pptx
        return from_pptx.convert(data, name), []
    if kind == "xlsx":
        from . import from_xlsx
        return from_xlsx.convert(data, name)
    if kind == "text":
        return text_to_pdf(data), []
    raise ConvertError("対応していない形式です（Word .docx、PowerPoint .pptx、Excel .xlsx に対応しています）")


def text_to_pdf(data: bytes) -> bytes:
    from . import typeset as T
    for enc in ("utf-8-sig", "cp932", "utf-16"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", "replace")
    style = T.Style(family="Yu Gothic", size=10.5)
    sheets = []
    W, H, M = 595.3, 841.9, 56.0
    sheet = T.Sheet(W, H)
    y = M
    sheets.append(sheet)
    for raw in text.replace("\r\n", "\n").split("\n"):
        atoms = T.atomize([("text", raw.replace("\t", "    "), style)]) + [T.Atom("", style, 0.0, "mark", brk=True)]
        lines = T.break_lines(atoms, lambda i, l: (0.0, W - 2 * M), [], 36.0)
        for ln in lines:
            h, b = T.line_metrics(ln, None, 0.0, "ppt")
            if y + h > H - M:
                sheet = T.Sheet(W, H)
                sheets.append(sheet)
                y = M
            T.place_line(ln, "left", W - 2 * M)
            T.emit_line(sheet, ln, M, y + b, y, h)
            y += h
    doc = T.render(sheets)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out


def merge_pdfs(sources: list[dict], bookmarks: bool = False) -> bytes:
    """Join PDFs (each ``{name, data, ranges?}``) in the given order; optionally one bookmark per source."""
    out = pymupdf.open()
    toc = []
    for src in sources:
        name = src.get("name") or "文書"
        try:
            doc = pymupdf.open("pdf", src["data"])
        except Exception as exc:
            raise ConvertError(f"{name} を PDF として開けませんでした") from exc
        if doc.needs_pass:
            doc.close()
            raise ConvertError(f"{name} はパスワードで保護されています。先に開いてパスワードを解除してから結合してください")
        ranges = (src.get("ranges") or "").strip()
        start = out.page_count
        try:
            if ranges:
                groups = pages_mod.parse_ranges(ranges, doc.page_count)
                for group in groups:
                    for idx in group:
                        out.insert_pdf(doc, from_page=idx, to_page=idx)
            else:
                out.insert_pdf(doc)
        except ValueError as exc:
            raise ConvertError(f"{name}: {exc}") from exc
        if bookmarks and out.page_count > start:
            toc.append([1, re.sub(r"\.pdf$", "", name, flags=re.I), start + 1])
        doc.close()
    if out.page_count == 0:
        raise ConvertError("結合するページがありません")
    if toc:
        out.set_toc(toc)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def extract_images(doc: pymupdf.Document, stem: str, pages: list[int] | None, *, fmt: str = "original", min_size: int = 16) -> tuple[bytes, int]:
    """Every picture embedded in the PDF, as a zip. ``fmt``: original (as stored, PNG when it cannot be) | jpg | png."""
    buffer = io.BytesIO()
    seen: set[int] = set()
    count = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for pno in (pages if pages is not None else range(doc.page_count)):
            page = doc[pno]
            n = 0
            for item in page.get_images(full=True):
                xref = item[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    info = doc.extract_image(xref)
                except Exception:  # noqa: BLE001
                    continue
                if not info:
                    continue
                if info.get("width", 0) < min_size or info.get("height", 0) < min_size:
                    continue
                n += 1
                name = f"{stem}_p{pno + 1}_{n}"
                data, ext = info["image"], info["ext"]
                smask = info.get("smask", 0)
                needs_convert = fmt in ("jpg", "png") and ext != ("jpg" if fmt == "jpg" else "png")
                if smask or ext not in ("jpeg", "jpg", "png", "gif", "bmp", "tiff") or needs_convert:
                    try:
                        pix = pymupdf.Pixmap(doc, xref)
                        if smask:
                            pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
                        if pix.colorspace and pix.colorspace.n > 3:
                            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                        if fmt == "jpg":
                            if pix.alpha:
                                pix = pymupdf.Pixmap(pix, 0)
                            data, ext = pix.tobytes("jpeg", jpg_quality=92), "jpg"
                        else:
                            data, ext = pix.tobytes("png"), "png"
                    except Exception:  # noqa: BLE001
                        continue
                if ext == "jpeg":
                    ext = "jpg"
                archive.writestr(f"{name}.{ext}", data)
                count += 1
    return buffer.getvalue(), count


PAPER_SIZES = {"a4": (595.3, 841.9), "a3": (841.9, 1190.6), "a5": (419.5, 595.3), "b5": (515.9, 728.5), "b4": (728.5, 1031.8),
               "letter": (612, 792), "legal": (612, 1008)}


def images_to_pdf(files: list[tuple[str, bytes]], *, orientation: str = "auto", paper: str = "a4", margin: float = 18.0) -> bytes:
    """One page per picture. ``paper`` 'fit' makes each page the size of its picture; ``orientation`` portrait,
    landscape, or auto (turned to suit the picture)."""
    out = pymupdf.open()
    for name, data in files:
        try:
            pix = pymupdf.Pixmap(data)
        except Exception as exc:
            raise ValueError(f"{name} は画像として読めませんでした") from exc
        w_px, h_px = pix.width, pix.height
        # honour the camera's rotation tag where MuPDF reports it
        if paper == "fit":
            width, height = w_px * 0.75, h_px * 0.75
            width, height = max(width, 36), max(height, 36)
            page = out.new_page(width=width, height=height)
            page.insert_image(pymupdf.Rect(0, 0, width, height), stream=data, keep_proportion=False)
            continue
        pw, ph = PAPER_SIZES.get(paper, PAPER_SIZES["a4"])
        landscape = (w_px > h_px) if orientation == "auto" else orientation == "landscape"
        width, height = (ph, pw) if landscape else (pw, ph)
        page = out.new_page(width=width, height=height)
        box = pymupdf.Rect(margin, margin, width - margin, height - margin)
        page.insert_image(box, stream=data, keep_proportion=True)
    result = out.tobytes(garbage=3, deflate=True)
    out.close()
    return result


def split_by_size(doc: pymupdf.Document, stem: str, max_bytes: int) -> bytes:
    """Cut into parts that are each (about) no larger than ``max_bytes``; a single page larger than that stays alone."""
    def build(a: int, b: int) -> bytes:
        part = pymupdf.open()
        part.insert_pdf(doc, from_page=a, to_page=b)
        data = part.tobytes(garbage=3, deflate=True)
        part.close()
        return data

    buffer = io.BytesIO()
    n = doc.page_count
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        start = 0
        part_no = 1
        while start < n:
            best_end, best = start, build(start, start)
            if len(best) <= max_bytes:
                # gallop to a page count that is too big, then bisect
                step = 1
                lo = start
                hi = None
                while hi is None:
                    cand = min(n - 1, lo + step)
                    if cand == lo:
                        break
                    data = build(start, cand)
                    if len(data) <= max_bytes:
                        lo, best_end, best = cand, cand, data
                        step *= 2
                    else:
                        hi = cand
                if hi is not None:
                    while hi - lo > 1:
                        mid = (lo + hi) // 2
                        data = build(start, mid)
                        if len(data) <= max_bytes:
                            lo, best_end, best = mid, mid, data
                        else:
                            hi = mid
            archive.writestr(f"{stem}_{part_no}.pdf", best)
            start = best_end + 1
            part_no += 1
    return buffer.getvalue()
