"""Appearance streams for text boxes, written by hand.

MuPDF can build a FreeText appearance on its own, but what it builds for
Japanese is not something to ship: short text comes out as a non-embedded
"Mincho" for kana and a *Chinese* "Song" for kanji, long text switches to a
different code path with a different font, and anything past the edge of the
box is silently clipped. The reader then sees glyph shapes that depend on
which viewer and which fonts they happen to have.

So the app draws the appearance itself. The browser decides the line breaks
(it is the one showing the text while it is typed) and sends them along as a
`layout`; this module places exactly those lines, in a font that is embedded
in the file, so every viewer shows the same Japanese the author saw.

Fonts are the open-licensed faces in vendor/fonts. They are subset with
fontTools before embedding — a whole face is 4-8 MB, a subset is a few KB.
When a face (or fontTools) is not available the text falls back to the
standard non-embedded pair, Helvetica plus a Japan1 gothic, which still
guarantees Japanese glyph forms rather than Chinese ones.
"""

from __future__ import annotations

import io
import math
import os
import zlib

import pymupdf

from .common import hex_to_rgb

# family key -> (regular file, bold file or None, serif?)
FAMILIES = {
    "gothic": ("BIZUDPGothic-Regular.ttf", "BIZUDPGothic-Bold.ttf", False),
    "mincho": ("BIZUDPMincho-Regular.ttf", None, True),
    "maru": ("ZenMaruGothic-Regular.ttf", None, False),
    "klee": ("KleeOne-Regular.ttf", None, False),
    "yomogi": ("Yomogi-Regular.ttf", None, False),
}
# Older files and other tools name the family differently; all of them mean
# "the default Japanese sans".
ALIASES = {"japan": "gothic", "Helv": "gothic", "helv": "gothic", "TiRo": "mincho", "Cour": "gothic"}

LINE_HEIGHT = 1.3     # must match the editor's CSS line-height
PAD_X = 3.0           # and its padding
PAD_Y = 2.0

_FONT_DIRS = [
    "/fonts",  # where the browser build drops the faces it has fetched
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "vendor", "fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "webapp", "vendor", "fonts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "vendor", "fonts"),
]

_bytes_cache: dict[str, bytes | None] = {}
_font_cache: dict[str, pymupdf.Font] = {}


def family_of(name: str | None) -> str:
    name = ALIASES.get(name or "", name or "gothic")
    return name if name in FAMILIES else "gothic"


def _font_bytes(filename: str) -> bytes | None:
    if filename in _bytes_cache and _bytes_cache[filename] is not None:
        return _bytes_cache[filename]
    for folder in _FONT_DIRS:
        path = os.path.join(folder, filename)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                _bytes_cache[filename] = handle.read()
            return _bytes_cache[filename]
    return None


def _winansi(ch: str) -> bool:
    if ord(ch) < 32:
        return False
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


class _Face:
    """One embedded face in one document: tracks which glyphs get used."""

    def __init__(self, doc: pymupdf.Document, filename: str, serif: bool):
        self.filename = filename
        self.serif = serif
        self.data = _font_bytes(filename)
        if filename not in _font_cache:
            _font_cache[filename] = pymupdf.Font(fontbuffer=self.data)
        self.font = _font_cache[filename]
        self.xref = doc.get_new_xref()
        doc.update_object(self.xref, "<<>>")   # filled in by finish()
        self.used: dict[int, str] = {}         # gid -> character

    def gid(self, ch: str) -> int:
        return self.font.has_glyph(ord(ch)) or 0

    def advance(self, ch: str) -> float:
        return self.font.glyph_advance(ord(ch))


class FontBook:
    """Fonts needed while writing one batch of annotations into a document."""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.faces: dict[str, _Face] = {}
        self._std: dict[str, int] = {}
        self._helv = {}

    # ------------------------------------------------------------ lookup

    def face(self, family: str, bold: bool) -> _Face | None:
        regular, heavy, serif = FAMILIES[family_of(family)]
        filename = heavy if (bold and heavy) else regular
        if filename in self.faces:
            return self.faces[filename]
        if _font_bytes(filename) is None:
            return None
        try:
            self.faces[filename] = _Face(self.doc, filename, serif)
        except Exception:
            return None
        return self.faces[filename]

    def standard(self, kind: str) -> int:
        """Non-embedded fallbacks: 'latin'/'latin-serif' and 'cjk'/'cjk-serif'."""
        if kind in self._std:
            return self._std[kind]
        doc = self.doc
        xref = doc.get_new_xref()
        if kind.startswith("latin"):
            base = "Times-Roman" if kind.endswith("serif") else "Helvetica"
            doc.update_object(
                xref,
                f"<</Type/Font/Subtype/Type1/BaseFont/{base}/Encoding/WinAnsiEncoding>>",
            )
        else:
            serif = kind.endswith("serif")
            base = "MS-Mincho" if serif else "MS-Gothic"
            # Ordering Japan1 is what makes every viewer reach for a Japanese
            # face. The half-width kana range needs its own width or it would
            # be spaced like full-width characters.
            doc.update_object(
                xref,
                f"<</Type/Font/Subtype/Type0/BaseFont/{base}/Encoding/UniJIS-UTF16-H"
                f"/DescendantFonts[<</Type/Font/Subtype/CIDFontType0/BaseFont/{base}"
                "/CIDSystemInfo<</Registry(Adobe)/Ordering(Japan1)/Supplement 6>>"
                f"/FontDescriptor<</Type/FontDescriptor/FontName/{base}"
                f"/Flags {7 if serif else 5}/FontBBox[-200 -200 1200 1000]/ItalicAngle 0"
                "/Ascent 880/Descent -120/CapHeight 740/StemV 80>>"
                "/DW 1000/W[231 632 500]>>]>>",
            )
        self._std[kind] = xref
        return xref

    def helv_advance(self, ch: str, serif: bool) -> float:
        key = "tiro" if serif else "helv"
        if key not in self._helv:
            self._helv[key] = pymupdf.Font(key)
        return self._helv[key].glyph_advance(ord(ch))

    # ------------------------------------------------------------ embedding

    def finish(self) -> None:
        """Write the font objects for every face that was actually used."""
        for face in self.faces.values():
            if face.used:
                self._embed(face)

    def _embed(self, face: _Face) -> None:
        doc = self.doc
        font = face.font
        data = _subset(face.data, "".join(face.used.values()))
        tag = "".join(chr(65 + (zlib.crc32(data) >> (5 * i)) % 26) for i in range(6))
        name = f"{tag}+{font.name.replace(' ', '')}"

        file_xref = doc.get_new_xref()
        doc.update_object(file_xref, f"<</Length1 {len(data)}>>")
        doc.update_stream(file_xref, data)

        bbox = font.bbox
        descriptor = (
            f"<</Type/FontDescriptor/FontName/{name}/Flags {6 if face.serif else 4}"
            f"/FontBBox[{bbox.x0 * 1000:.0f} {bbox.y0 * 1000:.0f} {bbox.x1 * 1000:.0f} {bbox.y1 * 1000:.0f}]"
            f"/ItalicAngle 0/Ascent {font.ascender * 1000:.0f}/Descent {font.descender * 1000:.0f}"
            f"/CapHeight {font.ascender * 800:.0f}/StemV 80/FontFile2 {file_xref} 0 R>>"
        )
        widths = " ".join(
            f"{gid}[{face.advance(ch) * 1000:.0f}]" for gid, ch in sorted(face.used.items())
        )
        descendant = (
            f"<</Type/Font/Subtype/CIDFontType2/BaseFont/{name}"
            "/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
            f"/FontDescriptor {descriptor}/DW 1000/W[{widths}]/CIDToGIDMap/Identity>>"
        )

        cmap_xref = doc.get_new_xref()
        doc.update_object(cmap_xref, "<<>>")
        doc.update_stream(cmap_xref, _to_unicode(face.used))

        doc.update_object(
            face.xref,
            f"<</Type/Font/Subtype/Type0/BaseFont/{name}/Encoding/Identity-H"
            f"/DescendantFonts[{descendant}]/ToUnicode {cmap_xref} 0 R>>",
        )


def _subset(data: bytes, text: str) -> bytes:
    """Cut a face down to the glyphs in `text`, keeping glyph ids unchanged."""
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except Exception:
        return data  # no fontTools: embed the whole face rather than fail
    try:
        options = subset.Options()
        options.retain_gids = True
        options.notdef_outline = True
        options.glyph_names = False
        options.layout_features = []
        options.name_IDs = [1, 2, 6]
        options.hinting = False
        options.drop_tables += ["GSUB", "GPOS", "GDEF", "BASE", "vhea", "vmtx", "DSIG", "meta", "STAT"]
        font = TTFont(io.BytesIO(data), lazy=True)
        worker = subset.Subsetter(options)
        worker.populate(text=text)
        worker.subset(font)
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()
    except Exception:
        return data


def _to_unicode(used: dict[int, str]) -> bytes:
    rows = [f"<{gid:04x}> <{ch.encode('utf-16-be').hex()}>" for gid, ch in sorted(used.items())]
    parts = [
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <ffff>\nendcodespacerange\n"
    ]
    for start in range(0, len(rows), 100):
        chunk = rows[start : start + 100]
        parts.append(f"{len(chunk)} beginbfchar\n" + "\n".join(chunk) + "\nendbfchar\n")
    parts.append("endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n")
    return "".join(parts).encode("ascii")


# ---------------------------------------------------------------- layout


def char_advance(book: FontBook, face: _Face | None, serif: bool, ch: str) -> float:
    """Advance of one character in ems, in whichever font will draw it."""
    if face is not None and face.gid(ch):
        return face.advance(ch)
    if _winansi(ch):
        return book.helv_advance(ch, serif)
    return 0.5 if 0xFF61 <= ord(ch) <= 0xFF9F else 1.0


def fallback_layout(book: FontBook, item: dict, font: dict, valign: str = "top") -> dict:
    """Break lines here when the browser did not send any.

    Used for text that never passed through the editor: imported XFDF, the
    take-off legend, annotations made by other tools. It wraps per character,
    which is the right default for Japanese and acceptable for short Latin.
    """
    x0, y0, x1, y1 = item["rect"]
    size = float(font.get("size") or 12)
    family = family_of(font.get("family"))
    face = book.face(family, bool(font.get("bold")))
    serif = FAMILIES[family][2]
    room = max(size, (x1 - x0) - 2 * PAD_X)
    text = (item.get("text") or item.get("contents") or "").replace("\r\n", "\n").replace("\r", "\n")

    rows: list[tuple[str, float]] = []
    for paragraph in text.split("\n"):
        line, width, last_space = "", 0.0, -1
        for ch in paragraph.replace("\t", "    "):
            advance = char_advance(book, face, serif, ch) * size
            if line and width + advance > room:
                if ch != " " and last_space > 0 and _winansi(ch):
                    # Break at the last space so a Latin word stays whole.
                    head, tail = line[:last_space], line[last_space + 1 :]
                    rows.append((head, _width(book, face, serif, head, size)))
                    line, width = tail, _width(book, face, serif, tail, size)
                else:
                    rows.append((line, width))
                    line, width = "", 0.0
                last_space = -1
                if ch == " " and not line:
                    continue
            if ch == " ":
                last_space = len(line)
            line += ch
            width += advance
        rows.append((line, width))

    ascent = face.font.ascender if face else 0.88
    descent = -face.font.descender if face else 0.12
    step = size * LINE_HEIGHT
    first = PAD_Y + (step - (ascent + descent) * size) / 2 + ascent * size
    if valign == "middle":
        first += max(0.0, ((y1 - y0) - 2 * PAD_Y - step * len(rows)) / 2)
    align = font.get("align") or "left"
    lines = []
    for index, (line, width) in enumerate(rows):
        if not line:
            continue
        if align == "center":
            x = PAD_X + (room - width) / 2
        elif align == "right":
            x = PAD_X + room - width
        else:
            x = PAD_X
        lines.append({"x": x, "y": first + index * step, "t": line})
    return {"lines": lines}


def _width(book, face, serif, text, size) -> float:
    return sum(char_advance(book, face, serif, ch) for ch in text) * size


# ---------------------------------------------------------------- drawing


def _rgb(value, default=(0, 0, 0)) -> str:
    rgb = hex_to_rgb(value) or default
    return " ".join(f"{c:.4g}" for c in rgb)


def _n(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _text_ops(book: FontBook, resources: dict, layout: dict, origin, font: dict) -> list[str]:
    size = float(font.get("size") or 12)
    family = family_of(font.get("family"))
    serif = FAMILIES[family][2]
    face = book.face(family, bool(font.get("bold")))
    ox, oy = origin

    def resource(kind: str) -> str:
        if kind == "face":
            name, xref = f"F{face.xref}", face.xref
        else:
            xref = book.standard(kind)
            name = f"S{xref}"
        resources[name] = xref
        return name

    ops = ["BT", f"{_rgb(font.get('color'))} rg"]
    for line in layout.get("lines") or []:
        x = ox + float(line.get("x", 0))
        y = oy + float(line.get("y", 0))
        run_kind, run_hex, run_x = None, [], x
        cursor = x

        def flush():
            if run_kind and run_hex:
                ops.append(f"/{resource(run_kind)} {_n(size)} Tf")
                # The page space this is drawn in has y pointing down, so the
                # text matrix flips it back upright.
                ops.append(f"1 0 0 -1 {_n(run_x)} {_n(y)} Tm")
                ops.append(f"<{''.join(run_hex)}> Tj")

        for ch in (line.get("t") or "").replace("\t", "    "):
            if ch in "\r\n":
                continue
            gid = face.gid(ch) if face is not None else 0
            if gid:
                kind, code = "face", f"{gid:04x}"
                face.used[gid] = ch
                advance = face.advance(ch)
            elif _winansi(ch):
                kind = "latin-serif" if serif else "latin"
                code = ch.encode("cp1252").hex()
                advance = book.helv_advance(ch, serif)
            else:
                kind = "cjk-serif" if serif else "cjk"
                code = ch.encode("utf-16-be").hex()
                advance = 0.5 if 0xFF61 <= ord(ch) <= 0xFF9F else 1.0
            if kind != run_kind:
                flush()
                run_kind, run_hex, run_x = kind, [], cursor
            run_hex.append(code)
            cursor += advance * size
        flush()
    ops.append("ET")
    return ops


def write_appearance(page: pymupdf.Page, annot: pymupdf.Annot, view_item: dict,
                     style: dict, font: dict, book: FontBook) -> None:
    """Replace a FreeText annotation's appearance with one drawn here.

    `view_item` is the annotation as the browser sees it (view space, y down).
    Everything is drawn in that space; one matrix at the top of the stream maps
    it onto the page, which also takes care of rotated pages.
    """
    doc = page.parent
    kind, raw = doc.xref_get_key(annot.xref, "Rect")
    if kind != "array":
        return
    bbox = [float(v) for v in raw.strip("[]").split()]

    x0, y0, x1, y1 = [float(v) for v in view_item["rect"]]
    matrix = ~page.transformation_matrix
    if page.rotation:
        matrix = page.derotation_matrix * matrix

    resources: dict[str, int] = {}
    ops = ["q", f"{' '.join(_n(v) for v in matrix)} cm"]
    opacity = float(style.get("opacity", 1) or 1)
    if opacity < 0.999:
        ops.append("/GS0 gs")

    ink = _rgb(font.get("color"))
    width = float(style.get("width") or 0)
    if style.get("fill"):
        ops.append(f"{_rgb(style['fill'], (1, 1, 1))} rg {_n(x0)} {_n(y0)} {_n(x1 - x0)} {_n(y1 - y0)} re f")
    if width > 0:
        dash = style.get("dash") or []
        ops.append(f"{ink} RG {_n(width)} w [{' '.join(_n(float(d)) for d in dash)}] 0 d")
        ops.append(f"{_n(x0)} {_n(y0)} {_n(x1 - x0)} {_n(y1 - y0)} re S")

    callout = view_item.get("callout") or []
    if len(callout) >= 2:
        line_width = width or 1
        path = " ".join(
            f"{_n(p[0])} {_n(p[1])} {'m' if i == 0 else 'l'}" for i, p in enumerate(callout)
        )
        ops.append(f"{ink} RG {_n(line_width)} w [] 0 d {path} S")
        tip, back = callout[0], callout[1]
        angle = math.atan2(tip[1] - back[1], tip[0] - back[0])
        length = 8 + line_width
        wings = [
            (tip[0] - math.cos(angle - s) * length, tip[1] - math.sin(angle - s) * length)
            for s in (0.45, -0.45)
        ]
        ops.append(
            f"{_n(wings[0][0])} {_n(wings[0][1])} m {_n(tip[0])} {_n(tip[1])} l "
            f"{_n(wings[1][0])} {_n(wings[1][1])} l S"
        )

    layout = view_item.get("layout")
    if not layout or not layout.get("lines"):
        valign = "middle" if view_item.get("tool") == "stamp" else "top"
        layout = fallback_layout(book, view_item, font, valign)
    ops += _text_ops(book, resources, layout, (x0, y0), font)
    ops.append("Q")

    fonts = "".join(f"/{name} {xref} 0 R" for name, xref in resources.items())
    gstate = f"/ExtGState<</GS0<</ca {opacity:.3f}/CA {opacity:.3f}>>>>" if opacity < 0.999 else ""
    form = doc.get_new_xref()
    doc.update_object(
        form,
        f"<</Type/XObject/Subtype/Form/BBox[{' '.join(_n(v) for v in bbox)}]"
        f"/Resources<</Font<<{fonts}>>{gstate}>>>>",
    )
    doc.update_stream(form, "\n".join(ops).encode("ascii"))
    doc.xref_set_key(annot.xref, "AP", f"<</N {form} 0 R>>")
