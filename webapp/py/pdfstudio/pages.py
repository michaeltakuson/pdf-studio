"""Page and document structure operations (layer ③).

Everything here changes the file rather than the markup on top of it, so each
operation runs on the server and the viewer reloads afterwards. Operations that
destroy content take a snapshot first — see session.Doc.snapshot.
"""

from __future__ import annotations

import pymupdf

from .common import hex_to_rgb, rect_to_page

# The five boxes a PDF page can define. Cropping only touches CropBox, which is
# why "trimming" never actually removes what falls outside it.
PAGE_BOXES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")


def describe_boxes(page: pymupdf.Page) -> dict:
    out = {}
    for name in PAGE_BOXES:
        try:
            box = page.mediabox if name == "MediaBox" else page.cropbox if name == "CropBox" else None
            if box is None:
                kind, value = page.parent.xref_get_key(page.xref, name)
                box = value if kind == "array" else None
            out[name] = list(box) if hasattr(box, "__iter__") else box
        except Exception:
            out[name] = None
    return out


def rotate(doc: pymupdf.Document, pages: list[int], degrees: int) -> None:
    for index in pages:
        page = doc[index]
        page.set_rotation((page.rotation + degrees) % 360)


def delete(doc: pymupdf.Document, pages: list[int]) -> None:
    if len(set(pages)) >= doc.page_count:
        raise ValueError("すべてのページは削除できません")
    doc.delete_pages(sorted(set(pages), reverse=True))


def duplicate(doc: pymupdf.Document, pages: list[int]) -> None:
    for index in sorted(set(pages), reverse=True):
        doc.fullcopy_page(index, index + 1)


def move(doc: pymupdf.Document, source: int, target: int) -> None:
    doc.move_page(source, target)


def insert_blank(doc: pymupdf.Document, at: int, width: float, height: float) -> None:
    doc.new_page(pno=at, width=width, height=height)


def extract(doc: pymupdf.Document, pages: list[int]) -> bytes:
    out = pymupdf.open()
    for index in sorted(set(pages)):
        out.insert_pdf(doc, from_page=index, to_page=index)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def merge(doc: pymupdf.Document, other: bytes, at: int | None = None) -> int:
    incoming = pymupdf.open("pdf", other)
    added = incoming.page_count
    doc.insert_pdf(incoming, start_at=doc.page_count if at is None else at)
    incoming.close()
    return added


def crop(doc: pymupdf.Document, pages: list[int], rect: list[float]) -> None:
    """Set the visible area. Data outside the box stays in the file."""
    for index in pages:
        page = doc[index]
        box = pymupdf.Rect(rect_to_page(page, rect)) & page.mediabox
        if box.is_empty:
            raise ValueError("トリミング範囲がページの外です")
        page.set_cropbox(box)


def reset_crop(doc: pymupdf.Document, pages: list[int]) -> None:
    for index in pages:
        page = doc[index]
        page.set_cropbox(page.mediabox)


def add_margins(doc: pymupdf.Document, pages: list[int] | None, *, left=0.0, top=0.0,
                right=0.0, bottom=0.0) -> int:
    """Grow the paper around the content: room to write notes beside a slide.

    Only the page boxes change, so the content, its links and every annotation
    stay exactly where they were relative to each other.
    """
    count = 0
    for index in _target_pages(doc, pages):
        page = doc[index]
        box = pymupdf.Rect(page.mediabox)
        crop = pymupdf.Rect(page.cropbox)
        # Margins are given as the reader sees the page; the boxes live in the
        # unrotated page, so turn them back first.
        sides = [left, top, right, bottom]
        turns = (page.rotation // 90) % 4
        l, t, r, b = sides[turns:] + sides[:turns]
        # PDF space has y pointing up: "top" is the high y edge.
        grown = pymupdf.Rect(box.x0 - l, box.y0 - b, box.x1 + r, box.y1 + t)
        doc.xref_set_key(page.xref, "MediaBox", f"[{grown.x0:.2f} {grown.y0:.2f} {grown.x1:.2f} {grown.y1:.2f}]")
        if crop != box:
            doc.xref_set_key(page.xref, "CropBox", "null")
        count += 1
    return count


def nup(doc: pymupdf.Document, per_sheet: int = 2, *, border: bool = True) -> bytes:
    """Several pages per sheet of A4 — handouts and exam-revision printouts."""
    source = pymupdf.open("pdf", doc.tobytes())
    try:
        try:
            source.bake()  # markup is not carried by show_pdf_page, so burn it in
        except Exception:
            pass
        layouts = {2: (842, 595, 2, 1), 4: (595, 842, 2, 2), 6: (595, 842, 2, 3),
                   8: (842, 595, 4, 2), 9: (595, 842, 3, 3)}
        width, height, cols, rows = layouts.get(int(per_sheet), layouts[2])
        margin, gap = 24, 10
        cell_w = (width - 2 * margin - (cols - 1) * gap) / cols
        cell_h = (height - 2 * margin - (rows - 1) * gap) / rows
        out = pymupdf.open()
        sheet = None
        for index in range(source.page_count):
            slot = index % (cols * rows)
            if slot == 0:
                sheet = out.new_page(width=width, height=height)
            col, row = slot % cols, slot // cols
            x = margin + col * (cell_w + gap)
            y = margin + row * (cell_h + gap)
            cell = pymupdf.Rect(x, y, x + cell_w, y + cell_h)
            src = source[index].rect
            scale = min(cell.width / src.width, cell.height / src.height)
            w, h = src.width * scale, src.height * scale
            placed = pymupdf.Rect(cell.x0 + (cell.width - w) / 2, cell.y0 + (cell.height - h) / 2,
                                  cell.x0 + (cell.width + w) / 2, cell.y0 + (cell.height + h) / 2)
            sheet.show_pdf_page(placed, source, index)
            if border:
                sheet.draw_rect(placed, color=(0.6, 0.6, 0.6), width=0.5)
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data
    finally:
        source.close()


def parse_ranges(text: str, page_count: int) -> list[list[int]]:
    """"1-3, 5, 8-" -> [[0,1,2],[4],[7..last]]. Raises ValueError on nonsense."""
    groups = []
    cleaned = str(text)
    for wide, plain in (("、", ","), ("，", ","), ("ー", "-"), ("－", "-"), ("〜", "-"), ("~", "-"), ("～", "-")):
        cleaned = cleaned.replace(wide, plain)
    cleaned = cleaned.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                first, last = part.split("-", 1)
                start = int(first) if first.strip() else 1
                end = int(last) if last.strip() else page_count
            else:
                start = end = int(part)
        except ValueError:
            raise ValueError(f"ページ範囲が読み取れません: {part}（例: 1-3, 5, 8-）")
        if start < 1 or end > page_count or start > end:
            raise ValueError(f"ページ範囲が正しくありません: {part}（全 {page_count} ページ）")
        groups.append(list(range(start - 1, end)))
    if not groups:
        raise ValueError("ページ範囲を入力してください（例: 1-3, 5, 8-）")
    return groups


def split(doc: pymupdf.Document, stem: str, *, ranges: str = "", every: int = 0) -> bytes:
    """Split into several PDFs, returned as one zip."""
    import io
    import zipfile

    if every and every > 0:
        groups = [list(range(i, min(i + every, doc.page_count))) for i in range(0, doc.page_count, every)]
    else:
        groups = parse_ranges(ranges, doc.page_count)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for group in groups:
            part = pymupdf.open()
            part.insert_pdf(doc, from_page=group[0], to_page=group[-1])
            label = f"{group[0] + 1}" if len(group) == 1 else f"{group[0] + 1}-{group[-1] + 1}"
            archive.writestr(f"{stem}_p{label}.pdf", part.tobytes(garbage=3, deflate=True))
            part.close()
    return buffer.getvalue()


def to_images(doc: pymupdf.Document, stem: str, pages: list[int] | None, *,
              dpi: int = 150, fmt: str = "png") -> tuple[str, str, bytes]:
    """Render pages (markup included) to pictures. One page comes back as the
    picture itself, several as a zip."""
    import io
    import zipfile

    targets = _target_pages(doc, pages)
    fmt = "jpg" if fmt in ("jpg", "jpeg") else "png"
    media = "image/jpeg" if fmt == "jpg" else "image/png"

    def render(index: int) -> bytes:
        pix = doc[index].get_pixmap(dpi=int(dpi), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=90) if fmt == "jpg" else pix.tobytes("png")

    if len(targets) == 1:
        return f"{stem}_p{targets[0] + 1}.{fmt}", media, render(targets[0])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        digits = len(str(doc.page_count))
        for index in targets:
            archive.writestr(f"{stem}_p{str(index + 1).zfill(digits)}.{fmt}", render(index))
    return f"{stem}_画像.zip", "application/zip", buffer.getvalue()


def to_text(doc: pymupdf.Document) -> str:
    parts = []
    for page in doc:
        parts.append(f"===== {page.number + 1} ページ =====\n{page.get_text().strip()}")
    return "\n\n".join(parts) + "\n"


def images_to_pdf(files: list[tuple[str, bytes]]) -> bytes:
    """One A4 page per picture, turned to suit the picture's shape."""
    out = pymupdf.open()
    for name, data in files:
        try:
            pix = pymupdf.Pixmap(data)
        except Exception as exc:
            raise ValueError(f"{name} は画像として読めませんでした") from exc
        landscape = pix.width > pix.height
        width, height = (842, 595) if landscape else (595, 842)
        page = out.new_page(width=width, height=height)
        margin = 18
        page.insert_image(pymupdf.Rect(margin, margin, width - margin, height - margin),
                          stream=data, keep_proportion=True)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def compress(doc: pymupdf.Document, *, dpi: int = 150, quality: int = 75) -> dict:
    """Make the file smaller by resampling pictures — where the weight is."""
    before = len(doc.tobytes())
    images = False
    try:
        doc.rewrite_images(dpi_threshold=int(dpi * 1.2), dpi_target=int(dpi), quality=int(quality))
        images = True
    except Exception:
        pass
    try:
        doc.subset_fonts()
    except Exception:
        pass
    after = len(doc.tobytes(garbage=4, deflate=True))
    return {"before": before, "after": after, "saved": max(0, before - after), "images": images}


def set_outline(doc: pymupdf.Document, toc: list) -> None:
    clean = []
    for level, title, page in toc:
        clean.append([max(1, int(level)), str(title), max(1, min(doc.page_count, int(page)))])
    # Levels may only step down one at a time; flatten anything that jumps.
    previous = 0
    for row in clean:
        row[0] = min(row[0], previous + 1)
        previous = row[0]
    doc.set_toc(clean)


def _font():
    from . import content

    return content.body_font()


def _subset(doc):
    from . import content

    content.subset(doc)


# ---------------------------------------------------------------- overlays


def _target_pages(doc: pymupdf.Document, pages: list[int] | None) -> list[int]:
    return sorted(set(pages)) if pages else list(range(doc.page_count))


def watermark(doc: pymupdf.Document, text: str, *, pages=None, colour="#c0c0c0",
              size=48, opacity=0.25, angle=45) -> int:
    count = 0
    font = _font()
    for index in _target_pages(doc, pages):
        page = doc[index]
        rect = page.rect
        writer = pymupdf.TextWriter(rect, opacity=opacity)
        width = font.text_length(text, size)
        writer.append(
            pymupdf.Point((rect.width - width) / 2, rect.height / 2),
            text, font=font, fontsize=size,
        )
        writer.write_text(
            page,
            color=hex_to_rgb(colour),
            morph=(pymupdf.Point(rect.width / 2, rect.height / 2),
                   pymupdf.Matrix(angle)),
        )
        count += 1
    _subset(doc)
    return count


def header_footer(doc: pymupdf.Document, *, header="", footer="", pages=None,
                  size=9, colour="#555555", margin=28) -> int:
    """Place running text, substituting {page} and {pages}."""
    font = _font()
    targets = _target_pages(doc, pages)
    for index in targets:
        page = doc[index]
        rect = page.rect
        writer = pymupdf.TextWriter(rect)
        for text, y in ((header, margin), (footer, rect.height - margin + size)):
            if not text:
                continue
            filled = text.replace("{page}", str(index + 1)).replace("{pages}", str(doc.page_count))
            width = font.text_length(filled, size)
            writer.append(
                pymupdf.Point((rect.width - width) / 2, y), filled, font=font, fontsize=size
            )
        writer.write_text(page, color=hex_to_rgb(colour))
    _subset(doc)
    return len(targets)


def bates(doc: pymupdf.Document, *, prefix="", start=1, digits=6, suffix="",
          size=9, colour="#333333", margin=28) -> int:
    """Sequential numbering across the document — the legal-discovery standard."""
    font = _font()
    for offset, page in enumerate(doc):
        label = f"{prefix}{str(start + offset).zfill(digits)}{suffix}"
        rect = page.rect
        writer = pymupdf.TextWriter(rect)
        width = font.text_length(label, size)
        writer.append(
            pymupdf.Point(rect.width - margin - width, rect.height - margin),
            label, font=font, fontsize=size,
        )
        writer.write_text(page, color=hex_to_rgb(colour))
    _subset(doc)
    return doc.page_count


# ---------------------------------------------------------------- redaction


def apply_redactions(doc: pymupdf.Document, *, images=True) -> dict:
    """Turn redaction marks into actual deletion.

    Up to this point a redaction is only a marked intention; the text is still
    in the file and still copyable. This is the step that removes it.
    """
    removed = 0
    pages_touched = 0
    for page in doc:
        marks = [a for a in page.annots() if a.type[0] == pymupdf.PDF_ANNOT_REDACT]
        if not marks:
            continue
        removed += len(marks)
        pages_touched += 1
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_PIXELS if images else pymupdf.PDF_REDACT_IMAGE_NONE,
        )
    return {"applied": removed, "pages": pages_touched}


def search_and_mark_redactions(doc: pymupdf.Document, needle: str, *,
                               fill="#000000", overlay="", overlay_colour="#ffffff",
                               overlay_size=8) -> int:
    from . import content  # imported here to keep the module import graph flat

    count = 0
    for page in doc:
        quads = page.search_for(needle, quads=True, flags=pymupdf.TEXTFLAGS_SEARCH)
        if not quads:
            # Same reasoning as the search endpoint: OCR'd Japanese carries
            # spaces between characters, so exact matching would miss it.
            quads = content.search_relaxed(page, needle)
        for quad in quads:
            page.add_redact_annot(
                quad,
                text=overlay or None,
                # The default Helvetica cannot draw Japanese, so overlay text
                # would silently vanish; the built-in CJK face covers both.
                fontname="japan" if overlay else None,
                fontsize=overlay_size,
                text_color=hex_to_rgb(overlay_colour),
                fill=hex_to_rgb(fill),
                cross_out=False,
            )
            count += 1
    return count


def scrub(doc: pymupdf.Document, **options) -> None:
    """Strip the invisible leftovers: metadata, embedded files, hidden layers."""
    doc.scrub(
        attached_files=options.get("attachments", True),
        clean_pages=options.get("cleanPages", True),
        embedded_files=options.get("embedded", True),
        hidden_text=options.get("hiddenText", True),
        javascript=options.get("javascript", True),
        metadata=options.get("metadata", True),
        redactions=False,
        remove_links=options.get("links", False),
        reset_fields=options.get("fields", False),
        reset_responses=options.get("responses", False),
        thumbnails=options.get("thumbnails", True),
        xml_metadata=options.get("xmlMetadata", True),
    )


# ---------------------------------------------------------------- security


PERMISSION_BITS = {
    "print": pymupdf.PDF_PERM_PRINT,
    "modify": pymupdf.PDF_PERM_MODIFY,
    "copy": pymupdf.PDF_PERM_COPY,
    "annotate": pymupdf.PDF_PERM_ANNOTATE,
    "form": pymupdf.PDF_PERM_FORM,
    "accessibility": pymupdf.PDF_PERM_ACCESSIBILITY,
    "assemble": pymupdf.PDF_PERM_ASSEMBLE,
    "printHQ": pymupdf.PDF_PERM_PRINT_HQ,
}


def save_options(*, user_password="", owner_password="", permissions=None) -> dict:
    """Build save arguments for encryption, or plain settings when unprotected."""
    if not user_password and not owner_password:
        return {"encryption": pymupdf.PDF_ENCRYPT_NONE}
    allowed = 0
    for name, bit in PERMISSION_BITS.items():
        if (permissions or {}).get(name, True):
            allowed |= bit
    return {
        "encryption": pymupdf.PDF_ENCRYPT_AES_256,
        # Owner and user passwords must stay distinct: making them the same
        # would grant owner rights to anyone who can open the file, quietly
        # cancelling every permission restriction below.
        "owner_pw": owner_password,
        "user_pw": user_password,
        "permissions": allowed,
    }


# ---------------------------------------------------------------- optimisation


def optimise(doc: pymupdf.Document, *, subset=True) -> dict:
    """Report what optimisation can reclaim; the caller saves with these flags."""
    before = len(doc.tobytes())
    if subset:
        doc.subset_fonts()
    after = len(doc.tobytes(garbage=4, deflate=True, clean=True))
    return {"before": before, "after": after, "saved": max(0, before - after)}
