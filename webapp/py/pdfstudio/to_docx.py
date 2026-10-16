"""PDF to Word (.docx).

Two ways of doing it, chosen by the caller:

``flow``    a document you can edit: text is regrouped into paragraphs (headings
            recognised, tables rebuilt as Word tables, pictures kept inline,
            repeated headers and footers moved into the page header and footer).
``layout``  looks like the PDF: each page's graphics are a picture behind the
            page and every block of text sits at its original place as an
            editable text frame.

Scanned pages (no text) come out as a picture of the page; run character
recognition first to get text.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

import pymupdf

from .pdf_analyze import (Block, Line, PageInfo, Span, analyze_page, block_text_lines, inside, line_text, merge_row_blocks, merge_paragraph_blocks, word_font, cell_lines)

EMU = 12700
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def esc(text: str) -> str:
    return escape(_CTRL.sub("", text), {'"': "&quot;"})


def tw(pt: float) -> int:
    return int(round(pt * 20))


# ============================================================================ low-level XML builders


class Media:
    def __init__(self):
        self.items: list[tuple[str, bytes, str]] = []     # (name, data, ext)
        self.rels: list[tuple[str, str, str]] = []        # (rid, type, target)
        self._n = 0

    def add_image(self, data: bytes, ext: str) -> str:
        self._n += 1
        name = f"image{self._n}.{ext}"
        rid = f"rIdImg{self._n}"
        self.items.append((name, data, ext))
        self.rels.append((rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", f"media/{name}"))
        return rid

    def add_link(self, url: str) -> str:
        rid = f"rIdLnk{len(self.rels) + 1}"
        self.rels.append((rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", url + "\0external"))
        return rid


def run_xml(text: str, span: Span | None, font_override: str | None = None, size_override: float | None = None,
            link_rid: str | None = None) -> str:
    if text == "":
        return ""
    props = ""
    if span is not None:
        font = esc(word_font(span.font)) if font_override is None else esc(font_override)
        props += f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}" w:cs="{font}"/>'
        if span.bold:
            props += "<w:b/><w:bCs/>"
        if span.italic:
            props += "<w:i/><w:iCs/>"
        if span.color and span.color != "000000":
            props += f'<w:color w:val="{span.color}"/>'
        size = size_override if size_override is not None else span.size
        half = max(2, int(round(size * 2)))
        props += f'<w:sz w:val="{half}"/><w:szCs w:val="{half}"/>'
        if link_rid:
            props += '<w:u w:val="single"/>'
        if span.sup:
            props += '<w:vertAlign w:val="superscript"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    parts = text.split("\t")
    body = ""
    for i, part in enumerate(parts):
        if i:
            body += "<w:tab/>"
        if part:
            body += f'<w:t xml:space="preserve">{esc(part)}</w:t>'
    run = f"<w:r>{rpr}{body}</w:r>"
    if link_rid:
        run = f'<w:hyperlink r:id="{link_rid}" w:history="1">{run}</w:hyperlink>'
    return run


def br_xml() -> str:
    return "<w:r><w:br/></w:r>"


def inline_image(rid: str, w_pt: float, h_pt: float, idx: int) -> str:
    cx, cy = int(w_pt * EMU), int(h_pt * EMU)
    return (
        f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{idx}" name="図 {idx}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="{idx}" name="図 {idx}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
    )


def anchored_image(rid: str, x_pt: float, y_pt: float, w_pt: float, h_pt: float, idx: int, behind: bool = True) -> str:
    cx, cy = int(w_pt * EMU), int(h_pt * EMU)
    ox, oy = int(x_pt * EMU), int(y_pt * EMU)
    return (
        f'<w:r><w:drawing><wp:anchor distT="0" distB="0" distL="0" distR="0" simplePos="0" relativeHeight="{idx}" '
        f'behindDoc="{1 if behind else 0}" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="page"><wp:posOffset>{ox}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="page"><wp:posOffset>{oy}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/>'
        f'<wp:docPr id="{idx}" name="図 {idx}"/><wp:cNvGraphicFramePr/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="{idx}" name="図 {idx}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>'
    )


def para_xml(runs: str, style: str | None = None, align: str | None = None, left: float = 0.0, first: float = 0.0,
             before: float = 0.0, after: float = 0.0, line: float | None = None, line_rule: str = "atLeast",
             frame: tuple | None = None, sect: str = "", keep_next: bool = False, hang_ok: bool = True,
             right: float = 0.0, tabs: list | None = None) -> str:
    ppr = ""
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if keep_next:
        ppr += "<w:keepNext/>"
    if frame:
        x, y, w, h = frame
        ppr += (f'<w:framePr w:w="{tw(w)}" w:h="{tw(h)}" w:hRule="atLeast" w:hSpace="0" w:vSpace="0" w:wrap="around" '
                f'w:vAnchor="page" w:hAnchor="page" w:x="{tw(x)}" w:y="{tw(y)}"/>')
    if tabs:
        ppr += "<w:tabs>" + "".join(f'<w:tab w:val="left" w:pos="{tw(max(t, 1.0))}"/>' for t in sorted(set(round(t, 1) for t in tabs))) + "</w:tabs>"
    sp = f'<w:spacing w:before="{tw(before)}" w:after="{tw(after)}"'
    if line:
        sp += f' w:line="{tw(line)}" w:lineRule="{line_rule}"'
    sp += "/>"
    ppr += sp
    if left or first or right:
        ind = f'<w:ind w:left="{tw(left)}"'
        if right:
            ind += f' w:right="{tw(right)}"'
        if first < 0:
            ind += f' w:hanging="{tw(-first)}"'
        elif first > 0:
            ind += f' w:firstLine="{tw(first)}"'
        ppr += ind + "/>"
    if align and align != "left":
        ppr += f'<w:jc w:val="{align}"/>'
    ppr += sect
    return f"<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>"


# ============================================================================ page content to elements


class Element:
    def __init__(self, kind: str, bbox: tuple, payload=None):
        self.kind = kind          # text, table, image, figure
        self.bbox = bbox
        self.payload = payload


_BULLET = re.compile(r"^(?:[・•●○▪■◆◇★☆※\-–—‣◦▶▷]|[（(]?\d+[\.\)）．、]|[①-⑳]|[（(][0-9a-zA-Zぁ-んァ-ン一-龥０-９]+[）)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ][\.．、]?)\s*")


def tab_stops(lines, origin: float) -> list[float]:
    return [sp.bbox[2] - origin for ln in lines for sp in ln.spans if sp.text == "	"]


def _first_word_width(text: str, size: float) -> float:
    text = text.lstrip()
    if not text:
        return 0.0
    m = re.match(r"[A-Za-z0-9’'\-.,:;/%$#@&+=_()\[\]]+", text)
    if m:
        return len(m.group(0)) * size * 0.55
    return size


def _join_block(block: Block) -> list[list[Span | str]]:
    """Lines of a block as paragraphs: a list of items (Span, or "\n" for a hard break)."""
    lines = block.lines
    if not lines:
        return []
    right = max(ln.bbox[2] for ln in lines)
    items: list = []
    for i, ln in enumerate(lines):
        for sp in ln.spans:
            items.append(Span(sp.text, sp.font, sp.size, sp.bold, sp.italic, sp.color, sp.bbox, sp.link, sp.sup))
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            this_txt = line_text(ln).rstrip()
            next_txt = line_text(nxt).lstrip()
            size = max((s.size for s in ln.spans), default=10.0)
            gap = right - ln.bbox[2]
            hard = bool(_BULLET.match(next_txt))
            if not hard and gap >= _first_word_width(next_txt, size) + size * 0.3 and gap > size * 1.2:
                hard = True          # the next word would have fitted: the author broke the line here
            if hard:
                items.append("\n")
                continue
            if this_txt.endswith("-") and next_txt[:1].islower():
                _trim_trailing(items, "-")
            elif this_txt and next_txt and _needs_space(this_txt[-1], next_txt[0]):
                items.append(" ")
    return [items]


def _trim_trailing(items: list, text: str) -> None:
    for k in range(len(items) - 1, -1, -1):
        it = items[k]
        if isinstance(it, Span):
            it.text = it.text.rstrip()
            if it.text.endswith(text):
                it.text = it.text[: -len(text)]
            return


def _needs_space(a: str, b: str) -> bool:
    def cjk(c):
        o = ord(c)
        return 0x3000 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF or 0xAC00 <= o <= 0xD7AF
    if cjk(a) or cjk(b):
        return False
    return not a.isspace() and not b.isspace()


def _runs_from_items(items: list, media: Media, size_scale: float = 1.0) -> str:
    out = ""
    for it in items:
        if it == "\n":
            out += br_xml()
        elif isinstance(it, str):
            last = None
            out += run_xml(it, None)
        else:
            rid = media.add_link(it.link) if it.link else None
            out += run_xml(it.text, it, link_rid=rid, size_override=it.size * size_scale)
    return out


def _is_heading(block: Block, info: PageInfo, sizes_rank: list[float]) -> int:
    if len(block.lines) > 2:
        return 0
    size = block.size
    text = block.text().strip()
    if not text or len(text) > 120:
        return 0
    if size >= info.body_size * 1.25 and size in sizes_rank[:3]:
        return sizes_rank.index(size) + 1
    if size >= info.body_size * 1.12 and all(s.bold for ln in block.lines for s in ln.spans if s.text.strip()) and size in sizes_rank[:3]:
        return min(3, sizes_rank.index(size) + 1)
    return 0


# ============================================================================ reading order


def reading_order(elements: list[Element], page_w: float) -> list[Element]:
    """Order elements top to bottom, taking two-column layouts column by column."""
    if len(elements) < 3:
        return sorted(elements, key=lambda e: (round(e.bbox[1] / 3), e.bbox[0]))
    text_w = page_w
    narrow = [e for e in elements if (e.bbox[2] - e.bbox[0]) < page_w * 0.55]
    # a column gutter: an x range that no narrow element crosses, with elements on both sides
    xs = sorted({round(e.bbox[0]) for e in narrow})
    gutter = None
    if len(narrow) >= 4:
        edges = sorted((e.bbox[0], e.bbox[2]) for e in narrow)
        mid_lo, mid_hi = page_w * 0.3, page_w * 0.7
        best = None
        candidates = [x for x in range(int(mid_lo), int(mid_hi), 3)]
        for x in candidates:
            if all(not (e.bbox[0] < x < e.bbox[2]) for e in narrow):
                left = [e for e in narrow if e.bbox[2] <= x]
                right = [e for e in narrow if e.bbox[0] >= x]
                if len(left) >= 2 and len(right) >= 2:
                    best = x
                    break
        gutter = best
    if gutter is None:
        return sorted(elements, key=lambda e: (round(e.bbox[1] / 3), e.bbox[0]))
    wide = [e for e in elements if (e.bbox[2] - e.bbox[0]) >= page_w * 0.55 or (e.bbox[0] < gutter < e.bbox[2])]
    cols_left = [e for e in elements if e not in wide and e.bbox[2] <= gutter + 1]
    cols_right = [e for e in elements if e not in wide and e not in cols_left]
    if not cols_left or not cols_right:
        return sorted(elements, key=lambda e: (round(e.bbox[1] / 3), e.bbox[0]))
    col_top = min(e.bbox[1] for e in cols_left + cols_right)
    col_bottom = max(e.bbox[3] for e in cols_left + cols_right)
    above = sorted([e for e in wide if e.bbox[3] <= col_top + 2], key=lambda e: e.bbox[1])
    below = sorted([e for e in wide if e.bbox[1] >= col_bottom - 2], key=lambda e: e.bbox[1])
    inside_wide = sorted([e for e in wide if e not in above and e not in below], key=lambda e: e.bbox[1])
    out = above
    left = sorted(cols_left, key=lambda e: e.bbox[1])
    right = sorted(cols_right, key=lambda e: e.bbox[1])
    if inside_wide:
        # wide items in the middle split the columns into bands
        cuts = [e.bbox[1] for e in inside_wide]
        bands = []
        prev = -1e9
        for cut in cuts + [1e9]:
            bands.append((prev, cut))
            prev = cut
        for k, (a, b) in enumerate(bands):
            out += [e for e in left if a <= e.bbox[1] < b]
            out += [e for e in right if a <= e.bbox[1] < b]
            if k < len(inside_wide):
                out.append(inside_wide[k])
    else:
        out += left + right
    out += below
    return out


# ============================================================================ the converter


class DocxFromPdf:
    def __init__(self, doc: pymupdf.Document, mode: str = "flow", pages: list[int] | None = None, dpi: int = 150):
        self.doc = doc
        self.mode = mode
        self.pages = pages if pages is not None else list(range(doc.page_count))
        self.dpi = dpi
        self.media = Media()
        self.body: list[str] = []
        self.headers: list[str] = []
        self.footers: list[str] = []
        self._img_id = 100
        self.warnings: list[str] = []
        self._repeat = self._find_repeating()
        self._flow_margins: tuple | None = None

    # ---- helpers
    def _image_part(self, data: bytes, ext: str) -> str:
        return self.media.add_image(data, ext)

    def _next_id(self) -> int:
        self._img_id += 1
        return self._img_id

    def _find_repeating(self):
        """Text that sits in the top or bottom band of most pages (running headers, footers, page numbers)."""
        top: dict[str, int] = {}
        bottom: dict[str, int] = {}
        n = len(self.pages)
        if n < 3:
            return {"top": set(), "bottom": set()}
        for pno in self.pages:
            page = self.doc[pno]
            h = page.rect.height
            seen_t, seen_b = set(), set()
            for b in page.get_text("blocks"):
                if b[6] != 0:
                    continue
                text = re.sub(r"\d+", "#", b[4].strip())[:80]
                if not text:
                    continue
                if b[3] < h * 0.09:
                    seen_t.add(text)
                elif b[1] > h * 0.91:
                    seen_b.add(text)
            for t in seen_t:
                top[t] = top.get(t, 0) + 1
            for t in seen_b:
                bottom[t] = bottom.get(t, 0) + 1
        need = max(2, int(n * 0.5 + 0.5))
        return {"top": {t for t, c in top.items() if c >= need}, "bottom": {t for t, c in bottom.items() if c >= need}}

    def _is_repeating(self, block: Block, info: PageInfo) -> str | None:
        text = re.sub(r"\d+", "#", block.text().strip())[:80]
        if block.bbox[3] < info.height * 0.09 and text in self._repeat["top"]:
            return "top"
        if block.bbox[1] > info.height * 0.91 and text in self._repeat["bottom"]:
            return "bottom"
        return None

    def _render_clip(self, page, rect, dpi=None, jpeg=False) -> tuple[bytes, str]:
        clip = pymupdf.Rect(rect) & page.rect
        if clip.is_empty:
            clip = page.rect
        pix = page.get_pixmap(dpi=dpi or self.dpi, clip=clip, alpha=False)
        if jpeg:
            return pix.tobytes("jpeg", jpg_quality=88), "jpeg"
        return pix.tobytes("png"), "png"

    def _textless_page(self, pno: int, remove_images: bool = False):
        """A one-page copy of the page with its text (and optionally pictures) taken out."""
        src = self.doc[pno]
        tmp = pymupdf.open()
        tmp.insert_pdf(self.doc, from_page=pno, to_page=pno)
        page = tmp[0]
        try:
            for b in page.get_text("blocks"):
                if b[6] == 0:
                    page.add_redact_annot(pymupdf.Rect(b[:4]), fill=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE if remove_images else pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                  text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        except Exception:  # noqa: BLE001 - an older engine: keep the page as it is
            pass
        return tmp, page

    def _cheap_scanned(self, page) -> bool:
        try:
            return len(page.get_text().strip()) < 5 and bool(page.get_images())
        except Exception:  # noqa: BLE001
            return False

    def _prepass_margins(self) -> tuple | None:
        """One set of margins for the whole document (the widest text area any page uses).

        Page by page margins make the text area jump between pages and turn a short last page into a page with a
        huge margin. A single set also lets the pages run on into each other like a document written in Word.
        """
        lefts, rights, tops, bottoms = [], [], [], []
        for pno in self.pages:
            page = self.doc[pno]
            if self._cheap_scanned(page):
                continue
            W, H = page.rect.width, page.rect.height
            boxes = [b[:4] for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()
                     and b[3] > H * 0.09 and b[1] < H * 0.91]
            if not boxes:
                continue
            x0 = min(b[0] for b in boxes)
            x1 = max(b[2] for b in boxes)
            y0 = min(b[1] for b in boxes)
            lefts.append(max(18.0, min(x0, W * 0.4)))
            rights.append(max(18.0, min(W - x1, W * 0.4)))
            tops.append(max(18.0, min(y0, H * 0.4)))
            bottoms.append(max(18.0, min(H - max(b[3] for b in boxes), H * 0.4)))
        if not lefts:
            return None
        top = min(tops)
        return (min(lefts), top, min(rights), min(min(bottoms), max(top, 72.0)))

    def _breaks_after(self, n: int) -> bool:
        """Does a new section have to start after page `n`? Only when the next page is another size or a picture."""
        if n + 1 >= len(self.pages):
            return False
        a, b = self.doc[self.pages[n]], self.doc[self.pages[n + 1]]
        return (abs(a.rect.width - b.rect.width) > 1 or abs(a.rect.height - b.rect.height) > 1
                or self._cheap_scanned(a) or self._cheap_scanned(b))

    # ---- main
    def convert(self) -> bytes:
        if self.mode != "layout":
            self._flow_margins = self._prepass_margins()
        total = len(self.pages)
        last_info = None
        for n, pno in enumerate(self.pages):
            page = self.doc[pno]
            info = analyze_page(page)
            last_info = info
            is_last = n == total - 1
            if self.mode == "layout":
                self._page_layout(page, pno, info, is_last)
            else:
                self._page_flow(page, pno, info, is_last, n)
        return self._package(last_info)

    def _sect(self, info: PageInfo, margins: tuple, last: bool = False, header_ref: str = "", footer_ref: str = "") -> str:
        l, t, r, b = margins
        refs = header_ref + footer_ref
        return (f'<w:sectPr>{refs}<w:pgSz w:w="{tw(info.width)}" w:h="{tw(info.height)}"/>'
                f'<w:pgMar w:top="{tw(t)}" w:right="{tw(r)}" w:bottom="{tw(b)}" w:left="{tw(l)}" w:header="{tw(min(t, 30) * 0.6)}" '
                f'w:footer="{tw(min(b, 30) * 0.6)}" w:gutter="0"/></w:sectPr>')

    # ---- layout mode
    def _page_layout(self, page, pno, info: PageInfo, is_last: bool):
        tmp, tpage = self._textless_page(pno)
        try:
            pix = tpage.get_pixmap(dpi=self.dpi, alpha=False)
            data = pix.tobytes("jpeg", jpg_quality=88) if self._photographic(pix) else pix.tobytes("png")
            ext = "jpeg" if data[:2] == b"\xff\xd8" else "png"
        finally:
            tmp.close()
        rid = self._image_part(data, ext)
        bg = anchored_image(rid, 0, 0, info.width, info.height, self._next_id(), behind=True)
        anchor_para = para_xml(bg, before=0, after=0, line=1, line_rule="exact")
        frames = []
        if not info.bg_image:
            for block in sorted(info.blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
                frames.append(self._frame_for(block, info))
        margins = (36, 36, 36, 36)
        sect = self._sect(info, margins)
        end = f"<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" w:line=\"20\" w:lineRule=\"exact\"/>{sect if not is_last else ''}</w:pPr></w:p>"
        self.body.append(anchor_para + "".join(frames) + end)
        if is_last:
            self._final_sect = sect

    @staticmethod
    def _photographic(pix) -> bool:
        """JPEG for pages with photographs, PNG for flat graphics (crisper, usually smaller)."""
        try:
            small = pymupdf.Pixmap(pix, max(1, pix.width // 8), max(1, pix.height // 8))
            colors = len({small.pixel(x, y) for x in range(0, small.width, 2) for y in range(0, small.height, 2)})
            return colors > 400
        except Exception:  # noqa: BLE001
            return True

    def _frame_for(self, block: Block, info: PageInfo) -> str:
        lines = block.lines
        vertical = any(ln.vertical for ln in lines)
        x0, y0, x1, y1 = block.bbox
        # a one-line frame must not wrap when the reader's font is a little wider than the original
        width = (x1 - x0) * (1.15 if len(lines) == 1 else 1.08) + (8 if len(lines) == 1 else 4)
        pitch = None
        if len(lines) >= 2:
            pitch = (lines[-1].bbox[1] - lines[0].bbox[1]) / (len(lines) - 1)
        else:
            pitch = lines[0].bbox[3] - lines[0].bbox[1]
        height = max(y1 - y0, pitch or 10)
        runs = ""
        for i, ln in enumerate(lines):
            for sp in ln.spans:
                rid = self.media.add_link(sp.link) if sp.link else None
                runs += run_xml(sp.text.rstrip("\n"), sp, link_rid=rid)
            if i + 1 < len(lines):
                runs += br_xml()
        # alignment inside the frame: right-aligned or centred text keeps its edge
        align = "left"
        if len(lines) == 1:
            pass
        return para_xml(runs, align=align, line=pitch, line_rule="exact", frame=(x0, y0, width, height),
                        tabs=tab_stops(lines, x0))

    # ---- flow mode
    def _page_flow(self, page, pno, info: PageInfo, is_last: bool, n: int = 0):
        W, H = info.width, info.height
        if info.scanned:
            # a picture of the page
            data, ext = self._render_clip(page, page.rect, jpeg=True)
            rid = self._image_part(data, ext)
            maxw = W - 72
            scale = min(1.0, maxw / W)
            w, h = W * scale, H * scale
            runs = inline_image(rid, w, h, self._next_id())
            margins = (36, 36, 36, 36)
            sect = self._sect(info, margins)
            p = para_xml(runs, before=0, after=0)
            self.body.append(p + f"<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" w:line=\"20\" w:lineRule=\"exact\"/>{sect if not is_last else ''}</w:pPr></w:p>")
            if is_last:
                self._final_sect = sect
            return
        elements: list[Element] = []
        figure_boxes = list(info.figures)
        table_boxes = [t["bbox"] for t in info.tables]
        for t in info.tables:
            elements.append(Element("table", t["bbox"], t))
        for fb in figure_boxes:
            elements.append(Element("figure", fb))
        textless = None
        for im in info.images:
            bb = im["bbox"]
            if any(inside(bb, f, 0.7) for f in figure_boxes):
                continue
            if (bb[2] - bb[0]) < 12 or (bb[3] - bb[1]) < 12:
                continue
            elements.append(Element("image", bb, im))
        header_texts, footer_texts = [], []
        for block in merge_paragraph_blocks(merge_row_blocks(info.blocks)):
            rep = self._is_repeating(block, info)
            if rep:
                (header_texts if rep == "top" else footer_texts).append(block)
                continue
            if any(inside(block.bbox, tb, 0.6) for tb in table_boxes):
                continue
            if any(inside(block.bbox, fb, 0.6) for fb in figure_boxes):
                continue
            elements.append(Element("text", block.bbox, block))
        if header_texts and not self.headers:
            self.headers.append(self._hf_xml(header_texts, info, "header"))
        if footer_texts and not self.footers:
            self.footers.append(self._hf_xml(footer_texts, info, "footer"))
        body_blocks = [e for e in elements if e.kind == "text"]
        # margins from the body text
        if body_blocks:
            x0 = min(e.bbox[0] for e in body_blocks)
            x1 = max(e.bbox[2] for e in body_blocks)
            y0 = min(e.bbox[1] for e in body_blocks)
            y1 = max(e.bbox[3] for e in body_blocks)
        else:
            x0, y0, x1, y1 = 54, 54, W - 54, H - 54
        for e in elements:
            if e.kind in ("table", "image", "figure"):
                x0 = min(x0, e.bbox[0]) if not body_blocks else x0
                x1 = max(x1, e.bbox[2]) if not body_blocks else x1
        left = max(18.0, min(x0, W * 0.4))
        right = max(18.0, min(W - x1, W * 0.4))
        top = max(18.0, min(y0, H * 0.4))
        # A page whose text stops half way down (the last page of a chapter) must not become a page with a huge
        # bottom margin: that margin would shrink the room for text the moment it is edited or set in another font.
        bottom = max(18.0, min(H - y1, H * 0.4, max(top, 72.0)))
        if self._flow_margins:
            left, top, right, bottom = self._flow_margins
        if self.headers:
            top = max(top, 40.0)
        if self.footers:
            bottom = max(bottom, 40.0)
        text_w = W - left - right
        ordered = reading_order(elements, W)
        sizes = sorted({b.size for e in ordered if e.kind == "text" for b in [e.payload]}, reverse=True)
        sizes_rank = [s for s in sizes if s >= info.body_size * 1.1]
        out: list[str] = []
        prev_bottom = top
        for e in ordered:
            gap = max(0.0, e.bbox[1] - prev_bottom)
            before = min(gap * 0.9, 80.0) if out else 0.0
            if not out and n > 0 and self._flow_margins and not self._breaks_after(n - 1):
                before = 14.0     # this page runs on from the one before: keep the breathing space a new page gave
            if e.kind == "text":
                out.append(self._para_for_block(e.payload, info, left, text_w, before, sizes_rank))
            elif e.kind == "table":
                out.append(self._table_for(e.payload, page, info, left, text_w))
                out.append(para_xml("", before=before and 0, after=0, line=1, line_rule="exact"))
            elif e.kind == "figure":
                out.append(self._picture_para(page, pno, e.bbox, info, left, text_w, before, with_text=True))
            else:
                out.append(self._picture_para(page, pno, e.bbox, info, left, text_w, before, with_text=False, xref=e.payload.get("xref")))
            prev_bottom = max(prev_bottom, e.bbox[3]) if e.kind != "text" else e.bbox[3]
        if not out:
            out.append(para_xml("", line=1, line_rule="exact"))
        hdr = ""
        ftr = ""
        sect = self._sect(info, (left, top, right, bottom),
                          header_ref='<w:headerReference w:type="default" r:id="rIdHdr1"/>' if self.headers else "",
                          footer_ref='<w:footerReference w:type="default" r:id="rIdFtr1"/>' if self.footers else "")
        if is_last:
            self._final_sect = sect
            self.body.append("".join(out))
        elif self._flow_margins and not self._breaks_after(n):
            # same size and margins as the page after it: the text simply runs on, as in a document written in Word
            self.body.append("".join(out))
        else:
            # the section ends in a paragraph of its own so tables and pictures never carry it
            last_tag = "".join(out)
            self.body.append(last_tag + f"<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" w:line=\"20\" w:lineRule=\"exact\"/>{sect}</w:pPr></w:p>")

    def _para_for_block(self, block: Block, info: PageInfo, left: float, text_w: float, before: float, sizes_rank: list[float]) -> str:
        lines = block.lines
        x0, y0, x1, y1 = block.bbox
        items = _join_block(block)[0]
        runs = _runs_from_items(items, self.media)
        heading = _is_heading(block, info, sizes_rank)
        # alignment
        line_lefts = [ln.bbox[0] for ln in lines]
        line_rights = [ln.bbox[2] for ln in lines]
        center = (x0 + x1) / 2
        col_center = left + text_w / 2
        align = "left"
        if abs(center - col_center) < 4 and (x1 - x0) < text_w * 0.9:
            align = "center"
        elif abs(x1 - (left + text_w)) < 4 and (x0 - left) > text_w * 0.2:
            align = "right"
        elif len(lines) >= 3 and sum(1 for r in line_rights[:-1] if abs(r - max(line_rights)) < 2.5) >= len(lines) - 1 and (max(line_rights) - min(line_lefts)) > text_w * 0.7:
            align = "both"
        indent = max(0.0, x0 - left) if align == "left" else 0.0
        first = 0.0
        if len(lines) >= 2 and align in ("left", "both"):
            others = sorted(line_lefts[1:])[len(line_lefts[1:]) // 2]
            first = line_lefts[0] - others
            indent = max(0.0, others - left)
            if abs(first) < 2:
                first = 0.0
        right_ind = 0.0
        if align == "left" and len(lines) >= 2 and (left + text_w) - max(line_rights) > text_w * 0.15:
            right_ind = 0.0
        pitch = None
        if len(lines) >= 2:
            pitch = (lines[-1].bbox[1] - lines[0].bbox[1]) / (len(lines) - 1)
            if pitch < block.size * 1.0:
                pitch = None
        style = f"Heading{heading}" if heading else None
        return para_xml(runs, style=style, align=align, left=indent, first=first, before=before, after=0,
                        line=pitch, line_rule="atLeast", keep_next=bool(heading), tabs=tab_stops(lines, left))

    def _picture_para(self, page, pno, bbox, info, left, text_w, before, with_text: bool, xref: int | None = None) -> str:
        data = ext = None
        if not with_text and xref:
            try:
                img = self.doc.extract_image(xref)
                if img and img.get("ext") in ("png", "jpeg", "jpg") and not img.get("smask") and img.get("colorspace", 3) in (1, 3):
                    data, ext = img["image"], "jpeg" if img["ext"] in ("jpg", "jpeg") else "png"
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            if with_text:
                data, ext = self._render_clip(page, bbox, jpeg=False)
            else:
                tmp, tpage = self._textless_page(pno)
                try:
                    data, ext = self._render_clip(tpage, bbox, jpeg=False)
                finally:
                    tmp.close()
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w > text_w:
            h = h * text_w / w
            w = text_w
        rid = self._image_part(data, ext)
        indent = max(0.0, min(bbox[0] - left, text_w - w))
        return para_xml(inline_image(rid, w, h, self._next_id()), left=indent, before=before, after=0)

    # ---- tables
    def _table_for(self, t: dict, page, info: PageInfo, left: float, text_w: float) -> str:
        cells = t["cells"]
        bbox = t["bbox"]
        rows, cols = t["rows"], t["cols"]
        real = [c for c in cells if c is not None]
        if not real:
            return ""
        xs = sorted({round(v, 1) for c in real for v in (c[0], c[2])})
        ys = sorted({round(v, 1) for c in real for v in (c[1], c[3])})

        def merge_close(vals, tol=1.5):
            out = []
            for v in vals:
                if out and v - out[-1] < tol:
                    continue
                out.append(v)
            return out

        xs, ys = merge_close(xs), merge_close(ys)

        def idx(vals, v):
            best = min(range(len(vals)), key=lambda i: abs(vals[i] - v))
            return best

        grid_w = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        n_r, n_c = len(ys) - 1, len(xs) - 1
        if n_r < 1 or n_c < 1:
            return ""
        placed: dict[tuple[int, int], dict] = {}
        covered: dict[tuple[int, int], tuple[int, int]] = {}
        for c in real:
            c0, c1 = idx(xs, c[0]), idx(xs, c[2])
            r0, r1 = idx(ys, c[1]), idx(ys, c[3])
            if c1 <= c0 or r1 <= r0:
                continue
            if (r0, c0) in placed:
                continue
            placed[(r0, c0)] = {"c0": c0, "c1": c1, "r0": r0, "r1": r1, "bbox": c}
            for rr in range(r0, r1):
                for cc in range(c0, c1):
                    if (rr, cc) != (r0, c0):
                        covered[(rr, cc)] = (r0, c0)
        # the page's text, by cell
        def nearest(cx, cy):
            c = min(range(n_c), key=lambda i: abs((xs[i] + xs[i + 1]) / 2 - cx))
            r = min(range(n_r), key=lambda i: abs((ys[i] + ys[i + 1]) / 2 - cy))
            return covered.get((r, c), (r, c))

        lines_by_cell = cell_lines(info.blocks, placed, bbox, nearest)
        fills = _cell_fills(page, placed)
        twips_w = [tw(w) for w in grid_w]
        total = sum(twips_w)
        ruled = True
        out = [f'<w:tbl><w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>',
               f'<w:tblInd w:w="{tw(max(0.0, xs[0] - left))}" w:type="dxa"/>']
        if ruled:
            out.append('<w:tblBorders>' + "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
                                                     for s in ("top", "left", "bottom", "right", "insideH", "insideV")) + '</w:tblBorders>')
        out.append('<w:tblLayout w:type="fixed"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="40" w:type="dxa"/>'
                   '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="40" w:type="dxa"/></w:tblCellMar></w:tblPr>')
        out.append("<w:tblGrid>" + "".join(f'<w:gridCol w:w="{w}"/>' for w in twips_w) + "</w:tblGrid>")
        for r in range(n_r):
            row_h = ys[r + 1] - ys[r]
            out.append(f'<w:tr><w:trPr><w:cantSplit/><w:trHeight w:val="{tw(row_h)}" w:hRule="atLeast"/></w:trPr>')
            c = 0
            while c < n_c:
                key = (r, c)
                if key in placed:
                    cell = placed[key]
                    span = cell["c1"] - cell["c0"]
                    vspan = cell["r1"] - cell["r0"]
                    width = sum(twips_w[cell["c0"]:cell["c1"]])
                    tcpr = f'<w:tcW w:w="{width}" w:type="dxa"/>'
                    if span > 1:
                        tcpr += f'<w:gridSpan w:val="{span}"/>'
                    if vspan > 1:
                        tcpr += '<w:vMerge w:val="restart"/>'
                    fill = fills.get(key)
                    if fill:
                        tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
                    tcpr += '<w:vAlign w:val="center"/>' if False else ""
                    out.append(f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{self._cell_paras(lines_by_cell.get(key, []), cell['bbox'])}</w:tc>")
                    c += span
                elif key in covered:
                    r0, c0 = covered[key]
                    cell = placed[(r0, c0)]
                    if (r, c) == (r, cell["c0"]) or c == cell["c0"]:
                        span = cell["c1"] - cell["c0"]
                        width = sum(twips_w[cell["c0"]:cell["c1"]])
                        tcpr = f'<w:tcW w:w="{width}" w:type="dxa"/>' + (f'<w:gridSpan w:val="{span}"/>' if span > 1 else "") + "<w:vMerge/>"
                        out.append(f"<w:tc><w:tcPr>{tcpr}</w:tcPr><w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\"/></w:pPr></w:p></w:tc>")
                        c += span
                    else:
                        c += 1
                else:
                    out.append(f'<w:tc><w:tcPr><w:tcW w:w="{twips_w[c]}" w:type="dxa"/></w:tcPr><w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr></w:p></w:tc>')
                    c += 1
            out.append("</w:tr>")
        out.append("</w:tbl>")
        return "".join(out)

    def _cell_paras(self, lines: list[Line], cell_bbox) -> str:
        if not lines:
            return '<w:p><w:pPr><w:spacing w:before="0" w:after="0"/></w:pPr></w:p>'
        lines = sorted(lines, key=lambda ln: (round(ln.bbox[1] / 2), ln.bbox[0]))
        # group lines into paragraphs by vertical gap
        groups: list[list[Line]] = []
        for ln in lines:
            if groups and ln.bbox[1] - groups[-1][-1].bbox[3] < max(2.0, (ln.bbox[3] - ln.bbox[1]) * 0.4) \
                    and abs(ln.bbox[0] - groups[-1][0].bbox[0]) < 6 or (groups and ln.bbox[1] < groups[-1][-1].bbox[3] - 1):
                groups[-1].append(ln)
            else:
                groups.append([ln])
        out = ""
        cw = cell_bbox[2] - cell_bbox[0]
        for g in groups:
            blk = Block(g, (min(l.bbox[0] for l in g), min(l.bbox[1] for l in g), max(l.bbox[2] for l in g), max(l.bbox[3] for l in g)))
            items = _join_block(blk)[0]
            runs = _runs_from_items(items, self.media)
            cx = (blk.bbox[0] + blk.bbox[2]) / 2
            ccx = (cell_bbox[0] + cell_bbox[2]) / 2
            align = "left"
            if abs(cx - ccx) < 3 and (blk.bbox[2] - blk.bbox[0]) < cw * 0.9:
                align = "center"
            elif cell_bbox[2] - blk.bbox[2] < 4 and blk.bbox[0] - cell_bbox[0] > cw * 0.25:
                align = "right"
            out += para_xml(runs, align=align, before=0, after=0, tabs=tab_stops(g, cell_bbox[0]))
        return out

    # ---- header and footer
    def _hf_xml(self, blocks: list[Block], info: PageInfo, kind: str) -> str:
        paras = ""
        for block in blocks:
            text = block.text().strip()
            items = _join_block(block)[0]
            runs = ""
            m = re.fullmatch(r"([-–—\s]*)(\d+)([-–—\s]*)", text)
            numeric = m is not None
            if numeric:
                sp = block.lines[0].spans[0]
                rpr = f'<w:rPr><w:sz w:val="{int(sp.size * 2)}"/></w:rPr>'
                pre = f'<w:r>{rpr}<w:t xml:space="preserve">{esc(m.group(1))}</w:t></w:r>' if m.group(1) else ""
                post = f'<w:r>{rpr}<w:t xml:space="preserve">{esc(m.group(3))}</w:t></w:r>' if m.group(3) else ""
                runs = (pre + f'<w:r>{rpr}<w:fldChar w:fldCharType="begin"/></w:r><w:r>{rpr}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
                        f'<w:r>{rpr}<w:fldChar w:fldCharType="separate"/></w:r><w:r>{rpr}<w:t>1</w:t></w:r><w:r>{rpr}<w:fldChar w:fldCharType="end"/></w:r>' + post)
            else:
                runs = _runs_from_items(items, self.media)
            cx = (block.bbox[0] + block.bbox[2]) / 2
            align = "center" if abs(cx - info.width / 2) < info.width * 0.12 else ("right" if cx > info.width * 0.6 else "left")
            paras += para_xml(runs, align=align, before=0, after=0)
        return paras

    # ---- packaging
    def _package(self, last_info) -> bytes:
        final = getattr(self, "_final_sect", "")
        body = "".join(self.body) + final
        ns = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
              'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
              'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
              'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')
        document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}><w:body>{body}</w:body></w:document>'
        rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
                '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>']
        for rid, typ, target in self.media.rels:
            if target.endswith("\0external"):
                rels.append(f'<Relationship Id="{rid}" Type="{typ}" Target="{esc(target[:-9])}" TargetMode="External"/>')
            else:
                rels.append(f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>')
        if self.headers:
            rels.append('<Relationship Id="rIdHdr1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>')
        if self.footers:
            rels.append('<Relationship Id="rIdFtr1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
        rels.append("</Relationships>")
        ctypes = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                  '<Default Extension="xml" ContentType="application/xml"/>',
                  '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/>',
                  '<Default Extension="jpg" ContentType="image/jpeg"/><Default Extension="gif" ContentType="image/gif"/>',
                  '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
                  '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
                  '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>']
        if self.headers:
            ctypes.append('<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>')
        if self.footers:
            ctypes.append('<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>')
        ctypes.append("</Types>")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "".join(ctypes))
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/document.xml", document)
            z.writestr("word/_rels/document.xml.rels", "".join(rels))
            z.writestr("word/styles.xml", STYLES_XML)
            z.writestr("word/settings.xml", SETTINGS_XML)
            if self.headers:
                z.writestr("word/header1.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {ns}>{self.headers[0]}</w:hdr>')
            if self.footers:
                z.writestr("word/footer1.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {ns}>{self.footers[0]}</w:ftr>')
            for name, data, _ext in self.media.items:
                z.writestr(f"word/media/{name}", data)
        return out.getvalue()


def _cell_fills(page, placed) -> dict:
    """Background colours of table cells, from filled rectangles under them."""
    out = {}
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return out
    rects = []
    for d in drawings:
        fill = d.get("fill")
        if not fill or d.get("type") not in ("f", "fs"):
            continue
        if min(fill) > 0.97:
            continue
        r = d["rect"]
        if r.width < 4 or r.height < 4:
            continue
        rects.append((r, fill))
    for key, cell in placed.items():
        b = cell["bbox"]
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        for r, fill in reversed(rects):
            if r.x0 - 1 <= cx <= r.x1 + 1 and r.y0 - 1 <= cy <= r.y1 + 1 and r.width >= (b[2] - b[0]) * 0.6 and r.height >= (b[3] - b[1]) * 0.6:
                out[key] = "".join(f"{int(round(max(0, min(1, v)) * 255)):02X}" for v in fill[:3])
                break
    return out


STYLES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="游明朝" w:eastAsia="游明朝" w:hAnsi="游明朝" w:cs="Times New Roman"/>'
    '<w:sz w:val="21"/><w:szCs w:val="21"/><w:lang w:val="en-US" w:eastAsia="ja-JP" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:keepNext/><w:outlineLvl w:val="2"/></w:pPr></w:style>'
    '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>'
    '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:uiPriority w:val="99"/><w:semiHidden/>'
    '<w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
    '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
    '</w:styles>'
)

SETTINGS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:defaultTabStop w:val="840"/><w:characterSpacingControl w:val="compressPunctuation"/>'
    '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
    '</w:settings>'
)


def convert(doc: pymupdf.Document, mode: str = "flow", pages: list[int] | None = None) -> tuple[bytes, list[str]]:
    conv = DocxFromPdf(doc, mode=mode, pages=pages)
    data = conv.convert()
    return data, conv.warnings
