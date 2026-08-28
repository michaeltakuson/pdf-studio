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


# ---------------------------------------------------------------- overlays


def _target_pages(doc: pymupdf.Document, pages: list[int] | None) -> list[int]:
    return sorted(set(pages)) if pages else list(range(doc.page_count))


def watermark(doc: pymupdf.Document, text: str, *, pages=None, colour="#c0c0c0",
              size=48, opacity=0.25, angle=45) -> int:
    count = 0
    font = pymupdf.Font("japan")
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
    return count


def header_footer(doc: pymupdf.Document, *, header="", footer="", pages=None,
                  size=9, colour="#555555", margin=28) -> int:
    """Place running text, substituting {page} and {pages}."""
    font = pymupdf.Font("japan")
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
    return len(targets)


def bates(doc: pymupdf.Document, *, prefix="", start=1, digits=6, suffix="",
          size=9, colour="#333333", margin=28) -> int:
    """Sequential numbering across the document — the legal-discovery standard."""
    font = pymupdf.Font("japan")
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
