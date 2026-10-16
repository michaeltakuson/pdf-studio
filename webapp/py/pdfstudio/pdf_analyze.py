"""Reading the structure of a PDF page for conversion to Word, PowerPoint and Excel.

Gives each page as plain Python data: text blocks (lines of styled spans),
pictures, vector-art regions, ruled tables and the page's own size. The
converters decide what to do with it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf


@dataclass
class Span:
    text: str
    font: str
    size: float
    bold: bool
    italic: bool
    color: str            # RRGGBB
    bbox: tuple
    link: str | None = None
    sup: bool = False
    underline: bool = False


@dataclass
class Line:
    spans: list[Span]
    bbox: tuple
    vertical: bool = False


@dataclass
class Block:
    lines: list[Line]
    bbox: tuple
    kind: str = "text"      # text, table, picture, figure

    def text(self) -> str:
        return "\n".join("".join(s.text for s in ln.spans) for ln in self.lines)

    @property
    def size(self) -> float:
        sizes = [s.size for ln in self.lines for s in ln.spans if s.text.strip()]
        return max(sizes) if sizes else 10.0


@dataclass
class PageInfo:
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)       # {bbox, xref}
    tables: list[dict] = field(default_factory=list)       # {bbox, rows:[[cell...]], cells:[...], ...}
    figures: list[tuple] = field(default_factory=list)     # vector-art regions (bbox)
    scanned: bool = False
    bg_image: bool = False      # one picture covers nearly the whole page (a scan, with or without a text layer)
    links: list[dict] = field(default_factory=list)
    body_size: float = 10.0


_SUBSET = re.compile(r"^[A-Z]{6}\+")

_FONT_MAP = [
    (r"ms[-_ ]?p?mincho|ＭＳ.*明朝|msmincho", "ＭＳ Ｐ明朝", "ＭＳ 明朝"),
    (r"ms[-_ ]?p?gothic|ＭＳ.*ゴシック|msgothic", "ＭＳ Ｐゴシック", "ＭＳ ゴシック"),
    (r"yugothic|游ゴシック|yu ?gothic", "游ゴシック", None),
    (r"yumincho|游明朝|yu ?mincho", "游明朝", None),
    (r"meiryo|メイリオ", "メイリオ", None),
    (r"biz ?ud ?p?gothic|bizudpgothic", "BIZ UDPゴシック", None),
    (r"biz ?ud ?p?mincho|bizudpmincho", "BIZ UDP明朝", None),
    (r"hiragino.*(kaku|gothic)|ヒラギノ角", "ヒラギノ角ゴ ProN", None),
    (r"hiragino.*(min|mincho)|ヒラギノ明朝", "ヒラギノ明朝 ProN", None),
    (r"heiseimin|ipamincho|ipa ?p?mincho|mincho|serif.*jp|notoserifcjk|notoserifjp|min(?:-|$)|明朝", "游明朝", None),
    (r"gothic|notosans.*(cjk|jp)|sourcehansans|kozgo|goshic|kaku|ゴシック|sans.*jp|ipagothic|ipa ?p?gothic", "游ゴシック", None),
    (r"timesnewroman|times", "Times New Roman", None),
    (r"arial|helvetica", "Arial", None),
    (r"calibri", "Calibri", None),
    (r"cambria", "Cambria", None),
    (r"century", "Century", None),
    (r"courier", "Courier New", None),
    (r"consolas", "Consolas", None),
    (r"verdana", "Verdana", None),
    (r"tahoma", "Tahoma", None),
    (r"georgia", "Georgia", None),
    (r"segoe", "Segoe UI", None),
    (r"symbol", "Symbol", None),
    (r"wingdings", "Wingdings", None),
]


def clean_font(name: str) -> str:
    name = _SUBSET.sub("", name or "")
    return name


def word_font(name: str) -> str:
    """A font name Word and PowerPoint will know, from the name embedded in a PDF."""
    raw = clean_font(name)
    low = raw.lower().replace(" ", "")
    for pattern, proportional, fixed in _FONT_MAP:
        if re.search(pattern.replace(" ", ""), low):
            return proportional
    # unknown: tidy CamelCase ("NotoSansCJKjp-Regular" -> keep the first words)
    base = re.split(r"[-,]", raw)[0]
    base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    return base or "游ゴシック"


def _color_hex(value: int) -> str:
    return f"{(value >> 16) & 255:02X}{(value >> 8) & 255:02X}{value & 255:02X}"


def _style_of(span: dict) -> tuple[bool, bool]:
    name = (span.get("font") or "").lower()
    flags = span.get("flags", 0)
    bold = bool(flags & 16) or "bold" in name or "black" in name or "heavy" in name or "-w6" in name or "demi" in name
    italic = bool(flags & 2) or "italic" in name or "oblique" in name
    return bold, italic


def analyze_page(page: pymupdf.Page, table_mode: str = "lines") -> PageInfo:
    rect = page.rect
    info = PageInfo(width=rect.width, height=rect.height)
    flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP | pymupdf.TEXT_PRESERVE_WHITESPACE
    data = page.get_text("dict", flags=flags)
    links = []
    try:
        links = [lk for lk in page.get_links() if lk.get("kind") == pymupdf.LINK_URI and lk.get("uri")]
    except Exception:  # noqa: BLE001
        pass
    info.links = links
    sizes: dict[float, int] = {}
    for b in data.get("blocks", []):
        if b.get("type") == 1:
            bbox = tuple(b["bbox"])
            info.images.append({"bbox": bbox, "block": True})
            continue
        lines = []
        for ln in b.get("lines", []):
            spans = []
            for sp in ln.get("spans", []):
                text = sp.get("text", "")
                if not text:
                    continue
                bold, italic = _style_of(sp)
                size = round(sp.get("size", 10.0), 1)
                if text.strip():
                    sizes[size] = sizes.get(size, 0) + len(text.strip())
                bb = tuple(sp["bbox"])
                span = Span(text=text, font=clean_font(sp.get("font", "")), size=size, bold=bold, italic=italic,
                            color=_color_hex(sp.get("color", 0)), bbox=bb, sup=bool(sp.get("flags", 0) & 1))
                for lk in links:
                    r = lk["from"]
                    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
                    if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
                        span.link = lk["uri"]
                        break
                if spans and _same_style(spans[-1], span) and abs(spans[-1].bbox[2] - bb[0]) < span.size * 0.6:
                    prev = spans[-1]
                    prev.text += text
                    prev.bbox = (prev.bbox[0], min(prev.bbox[1], bb[1]), bb[2], max(prev.bbox[3], bb[3]))
                else:
                    spans.append(span)
            if spans:
                d = ln.get("dir", (1, 0))
                lines.append(Line(spans, tuple(ln["bbox"]), vertical=abs(d[1]) > 0.7))
        if lines:
            lines = merge_same_baseline(lines)
            info.blocks.append(Block(lines, tuple(b["bbox"])))
    if sizes:
        info.body_size = max(sizes.items(), key=lambda kv: kv[1])[0]
    # pictures with real positions (a block of type 1 above covers most; this adds xrefs)
    try:
        for im in page.get_image_info(xrefs=True):
            bbox = tuple(im["bbox"])
            if (bbox[2] - bbox[0]) < 2 or (bbox[3] - bbox[1]) < 2:
                continue
            info.images = [i for i in info.images if not i.get("block")] + [x for x in info.images if x.get("xref")] \
                if False else info.images
            info.images.append({"bbox": bbox, "xref": im.get("xref")})
    except Exception:  # noqa: BLE001
        pass
    # keep one entry per picture: those with an xref win over the plain blocks
    xrefd = [i for i in info.images if i.get("xref")]
    plain = [i for i in info.images if not i.get("xref") and not any(_overlap(i["bbox"], x["bbox"]) > 0.8 for x in xrefd)]
    info.images = xrefd + plain
    # scanned page: one picture covers (nearly) the page and there is no text
    area = rect.width * rect.height
    text_chars = sum(len(s.text.strip()) for b in info.blocks for ln in b.lines for s in ln.spans)
    big = [i for i in info.images if _area(i["bbox"]) > area * 0.6]
    info.scanned = bool(big) and text_chars < 5
    info.bg_image = any(_area(i["bbox"]) > area * 0.85 for i in info.images)
    # tables
    try:
        strategy = {"lines": "lines", "text": "text"}.get(table_mode, "lines")
        finder = page.find_tables(strategy=strategy)
        for t in finder.tables:
            if t.row_count < 2 and t.col_count < 2:
                continue
            if t.row_count * t.col_count < 4:
                continue
            cells = t.cells
            info.tables.append({"bbox": tuple(t.bbox), "rows": t.row_count, "cols": t.col_count, "cells": cells,
                                "table": t})
    except Exception:  # noqa: BLE001
        pass
    # figures: dense vector art that is not a table or a rule
    try:
        clusters = page.cluster_drawings()
        for c in clusters:
            bb = (c.x0, c.y0, c.x1, c.y1)
            w, h = c.width, c.height
            if w < 40 or h < 40 or w * h < 4000:
                continue
            if w * h > area * 0.85 and not info.blocks:
                continue
            if any(_overlap(bb, t["bbox"]) > 0.6 for t in info.tables):
                continue
            info.figures.append(bb)
    except Exception:  # noqa: BLE001
        pass
    return info


def merge_same_baseline(lines: list[Line]) -> list[Line]:
    """Lines sitting on one baseline (a label and its value, columns of a row) become one line.

    A gap wider than a character or so between two pieces is marked with a tab span whose box is the gap, so that
    the converters can put the next piece back where it was.
    """
    out: list[Line] = []
    for ln in lines:
        if ln.vertical:
            out.append(ln)
            continue
        placed = False
        for prev in out:
            if prev.vertical:
                continue
            top, bottom = max(prev.bbox[1], ln.bbox[1]), min(prev.bbox[3], ln.bbox[3])
            h = min(prev.bbox[3] - prev.bbox[1], ln.bbox[3] - ln.bbox[1])
            if h > 0 and (bottom - top) > h * 0.6:
                prev.spans = sorted(prev.spans + ln.spans, key=lambda s: s.bbox[0])
                prev.bbox = (min(prev.bbox[0], ln.bbox[0]), min(prev.bbox[1], ln.bbox[1]),
                             max(prev.bbox[2], ln.bbox[2]), max(prev.bbox[3], ln.bbox[3]))
                placed = True
                break
        if not placed:
            out.append(ln)
    for ln in out:
        if ln.vertical or len(ln.spans) < 2:
            continue
        spans: list[Span] = []
        for sp in ln.spans:
            if spans:
                prev = spans[-1]
                gap = sp.bbox[0] - prev.bbox[2]
                size = max(prev.size, sp.size)
                if gap > size * 1.5 and not prev.text.endswith(("\t",)):
                    spans.append(Span("\t", prev.font, prev.size, False, False, prev.color,
                                      (prev.bbox[2], prev.bbox[1], sp.bbox[0], prev.bbox[3])))
                elif gap > size * 0.3 and not prev.text.endswith((" ", "　", "\t")) and not sp.text.startswith((" ", "　")):
                    # a visible gap between two pieces that the PDF did not fill with a space character
                    filler = "　" if gap > size * 0.8 else " "
                    spans.append(Span(filler, prev.font, prev.size, False, False, prev.color,
                                      (prev.bbox[2], prev.bbox[1], sp.bbox[0], prev.bbox[3])))
            spans.append(sp)
        ln.spans = spans
    return out


def _same_style(a: Span, b: Span) -> bool:
    return (a.font == b.font and abs(a.size - b.size) < 0.2 and a.bold == b.bold and a.italic == b.italic
            and a.color == b.color and a.link == b.link and a.sup == b.sup)


def _area(bb) -> float:
    return max(0.0, bb[2] - bb[0]) * max(0.0, bb[3] - bb[1])


def _overlap(a, b) -> float:
    """Fraction of ``a`` covered by ``b``."""
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    area = _area(a)
    return ((x1 - x0) * (y1 - y0)) / area if area else 0.0


def inside(bb, region, thresh=0.5) -> bool:
    return _overlap(bb, region) >= thresh


def line_text(line: Line) -> str:
    return "".join(s.text for s in line.spans)


def block_text_lines(block: Block) -> list[str]:
    return [line_text(ln) for ln in block.lines]


def pix_unicolor(pix) -> tuple | None:
    """The single colour of a pixmap that has only one, else None (Pixmap.is_unicolor is far too slow)."""
    data = bytes(pix.samples)
    n = pix.n
    if not data or len(data) % n:
        return None
    first = data[:n]
    if data == first * (len(data) // n):
        return tuple(first)
    return None


def merge_row_blocks(blocks: list[Block]) -> list[Block]:
    """Single-line blocks that sit on one baseline (a list number and its text, label and value) become one block."""
    out: list[Block] = []
    for blk in sorted(blocks, key=lambda b: (round(b.bbox[1]), b.bbox[0])):
        if len(blk.lines) == 1 and not blk.lines[0].vertical:
            merged = False
            for prev in reversed(out[-8:]):
                if len(prev.lines) != 1 or prev.lines[0].vertical:
                    continue
                a, b = prev.lines[0].bbox, blk.lines[0].bbox
                top, bottom = max(a[1], b[1]), min(a[3], b[3])
                h = min(a[3] - a[1], b[3] - b[1])
                if h > 0 and bottom - top > h * 0.6 and b[0] >= a[2] - 1 and b[0] - a[2] < 160:
                    line = merge_same_baseline([Line(list(prev.lines[0].spans), a), Line(list(blk.lines[0].spans), b)])
                    prev.lines = line
                    prev.bbox = (min(prev.bbox[0], blk.bbox[0]), min(prev.bbox[1], blk.bbox[1]),
                                 max(prev.bbox[2], blk.bbox[2]), max(prev.bbox[3], blk.bbox[3]))
                    merged = True
                    break
            if merged:
                continue
        out.append(blk)
    return out


_BULLET_START = re.compile(r"^(?:[・•●○▪■◆◇★☆※\-–—‣◦▶▷]|[（(]?\d+[\.\)）．、]|[①-⑳]|[（(][0-9a-zA-Zぁ-んァ-ン一-龥０-９]+[）)])")


def merge_paragraph_blocks(blocks: list[Block]) -> list[Block]:
    """Lines that the page already wraps into one paragraph become one block.

    Many producers (Google Docs, Skia, some print drivers) write every visual line as a block of its own, so the
    converters would otherwise hand back one paragraph per line. A line joins the block above when it sits at
    line spacing, starts where that block's text starts, is in the same type, and the line above ran right up to the
    margin — a line that stops short ended its paragraph.
    """
    lines_all = [ln for b in blocks if not any(l.vertical for l in b.lines) for ln in b.lines]
    wide = [ln.bbox[2] for ln in lines_all if len(line_text(ln).strip()) >= 15]
    if not wide:
        return blocks
    edge = max(wide)
    out: list[Block] = []
    for blk in sorted(blocks, key=lambda b: (round(b.bbox[1]), b.bbox[0])):
        if blk.lines and not any(l.vertical for l in blk.lines):
            target = None
            for prev in reversed(out[-6:]):
                if any(l.vertical for l in prev.lines):
                    continue
                last = prev.lines[-1]
                first = blk.lines[0]
                gap = first.bbox[1] - last.bbox[3]
                size = prev.size
                if abs(size - blk.size) > 0.6 or not (-0.3 * size <= gap <= 0.7 * size):
                    continue
                if last.bbox[2] < edge - 1.5 * size:
                    continue                    # the line above stopped short: that paragraph is finished
                if _BULLET_START.match(line_text(first).lstrip()):
                    continue
                # where the text of the line above starts: its own left, or after a label and a tab
                starts = {round(last.bbox[0])}
                for i, sp in enumerate(last.spans):
                    if sp.text == "	" and i + 1 < len(last.spans):
                        starts.add(round(last.spans[i + 1].bbox[0]))
                # a wrapped line starts where the text above starts — or further in (a hanging or indented continuation)
                if first.bbox[0] < min(starts) - 2.0:
                    continue
                bold_a = next((sp.bold for sp in last.spans if sp.text.strip()), False)
                bold_b = next((sp.bold for sp in first.spans if sp.text.strip()), False)
                if bold_a != bold_b:
                    continue
                target = prev
                break
            if target is not None:
                target.lines = target.lines + blk.lines
                target.bbox = (min(target.bbox[0], blk.bbox[0]), min(target.bbox[1], blk.bbox[1]),
                               max(target.bbox[2], blk.bbox[2]), max(target.bbox[3], blk.bbox[3]))
                continue
        out.append(blk)
    return out


def cell_lines(blocks: list[Block], placed: dict, bbox: tuple, nearest=None) -> dict:
    """Text of a table, by cell: ``{key: [Line]}``. Each span goes to the cell its centre falls in, so a line that
    runs across several columns is split between them."""
    out: dict = {}
    for block in blocks:
        if not (block.bbox[2] > bbox[0] - 2 and block.bbox[0] < bbox[2] + 2 and block.bbox[3] > bbox[1] - 2 and block.bbox[1] < bbox[3] + 2):
            continue
        for ln in block.lines:
            groups: dict = {}
            for sp in ln.spans:
                if sp.text == "\t":
                    continue
                cx, cy = (sp.bbox[0] + sp.bbox[2]) / 2, (sp.bbox[1] + sp.bbox[3]) / 2
                if not (bbox[0] - 2 <= cx <= bbox[2] + 2 and bbox[1] - 2 <= cy <= bbox[3] + 2):
                    continue
                key = None
                for k, cell in placed.items():
                    b = cell["bbox"]
                    if b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1:
                        key = k
                        break
                if key is None and nearest is not None:
                    key = nearest(cx, cy)
                if key is None:
                    continue
                groups.setdefault(key, []).append(sp)
            for key, spans in groups.items():
                spans = sorted(spans, key=lambda s: s.bbox[0])
                while spans and not spans[0].text.strip():
                    spans.pop(0)
                while spans and not spans[-1].text.strip():
                    spans.pop()
                if not spans:
                    continue
                bb = (min(s.bbox[0] for s in spans), min(s.bbox[1] for s in spans), max(s.bbox[2] for s in spans), max(s.bbox[3] for s in spans))
                out.setdefault(key, []).append(Line(spans, bb))
    return out
