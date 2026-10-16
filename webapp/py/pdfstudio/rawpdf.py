"""Drawing a ``typeset.Sheet`` display list into a PDF by writing the page content streams directly.

Going through PyMuPDF's TextWriter and Shape for every run and rectangle costs several native calls each, and
inside the browser (WebAssembly) every such call is slow: a 20-page document took twenty seconds. Writing the
operators as text and embedding each font once takes a small fraction of that, and gives full control over
italics (a shear), bold (stroked text) and letter spacing (Tc).

Fonts are embedded as CID TrueType faces keyed by glyph id, cut down to the characters used (fontTools, when it is
available; otherwise the whole face), with a ToUnicode map so the text can be searched and copied.
"""

from __future__ import annotations

import zlib

import pymupdf

from . import shapes
from .textap import _to_unicode

ITALIC_SHEAR = 0.22


def _c(color) -> str:
    return f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f}"


def _f(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if v == v else "0"


class PdfFont:
    def __init__(self, raw: "RawDoc", face, index: int):
        self.face = face
        self.name = f"F{index}"
        self.xref = raw.doc.get_new_xref()
        raw.doc.update_object(self.xref, "<<>>")
        self.used: dict[int, str] = {}

    def embed(self, doc: pymupdf.Document, subset: bool) -> None:
        face = self.face
        font = face.font
        data = face.font_data()
        text = "".join(self.used.values())
        tag = "".join(chr(65 + (zlib.crc32(text.encode("utf-8")) >> (5 * i)) % 26) for i in range(6))
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
        widths = " ".join(f"{gid}[{face.natural(ch) * 1000:.0f}]" for gid, ch in sorted(self.used.items()))
        descendant = (
            f"<</Type/Font/Subtype/CIDFontType2/BaseFont/{name}"
            "/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
            f"/FontDescriptor {descriptor}/DW 1000/W[{widths}]/CIDToGIDMap/Identity>>"
        )
        cmap = doc.get_new_xref()
        doc.update_object(cmap, "<<>>")
        doc.update_stream(cmap, _to_unicode(self.used))
        doc.update_object(
            self.xref,
            f"<</Type/Font/Subtype/Type0/BaseFont/{name}/Encoding/Identity-H"
            f"/DescendantFonts[{descendant}]/ToUnicode {cmap} 0 R>>",
        )


class RawDoc:
    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.fonts: dict[int, PdfFont] = {}
        self.images: dict = {}
        self._n_img = 0

    def font(self, face) -> PdfFont:
        key = id(face.font)
        f = self.fonts.get(key)
        if f is None:
            f = self.fonts[key] = PdfFont(self, face, len(self.fonts) + 1)
        return f

    def image(self, data: bytes):
        key = (len(data), data[:48], data[-16:])
        if key in self.images:
            return self.images[key]
        res = self._make_image(data)
        self.images[key] = res
        return res

    def _make_image(self, data: bytes):
        doc = self.doc
        try:
            pix = pymupdf.Pixmap(data)
        except Exception:  # noqa: BLE001
            return None
        self._n_img += 1
        name = f"Im{self._n_img}"
        w, h = pix.width, pix.height
        if data[:2] == b"\xff\xd8" and pix.n in (1, 3) and not pix.alpha:
            xref = doc.get_new_xref()
            cs = "/DeviceGray" if pix.n == 1 else "/DeviceRGB"
            doc.update_object(xref, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace{cs}/BitsPerComponent 8>>")
            doc.update_stream(xref, data, compress=False)
            doc.xref_set_key(xref, "Filter", "/DCTDecode")
            return {"name": name, "xref": xref, "w": w, "h": h}
        alpha = None
        if pix.alpha:
            raw = bytes(pix.samples)
            alpha = raw[pix.n - 1::pix.n]
            pix = pymupdf.Pixmap(pix, 0)
        if pix.colorspace is not None and pix.colorspace.n == 4:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        n = pix.n
        cs = "/DeviceGray" if n == 1 else "/DeviceRGB"
        smask = ""
        if alpha is not None and any(b != 255 for b in alpha[:: max(1, len(alpha) // 4096)]):
            sx = doc.get_new_xref()
            doc.update_object(sx, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace/DeviceGray/BitsPerComponent 8>>")
            doc.update_stream(sx, alpha)
            smask = f"/SMask {sx} 0 R"
        xref = doc.get_new_xref()
        doc.update_object(xref, f"<</Type/XObject/Subtype/Image/Width {w}/Height {h}/ColorSpace{cs}/BitsPerComponent 8{smask}>>")
        doc.update_stream(xref, bytes(pix.samples))
        return {"name": name, "xref": xref, "w": w, "h": h}

    def finish(self, subset: bool = True) -> None:
        for f in self.fonts.values():
            if f.used:
                f.embed(self.doc, subset)
        if subset and self.fonts:
            # the whole face went in; MuPDF cuts it down to the glyphs used (natively, so it is fast)
            try:
                self.doc.subset_fonts()
            except Exception:  # noqa: BLE001 - the faces stay whole
                pass


def render_sheets(sheets, doc: pymupdf.Document | None, subset: bool, fields: dict, anchors: dict):
    from .typeset import fallback_face
    doc = doc or pymupdf.open()
    raw = RawDoc(doc)
    for number, sheet in enumerate(sheets, start=1):
        page = doc.new_page(width=sheet.width, height=sheet.height)
        values = {**fields, "PAGE": number, "NUMPAGES": len(sheets)}
        _page(raw, page, sheet, values, anchors or {})
    raw.finish(subset)
    return doc


def _page(raw: RawDoc, page: pymupdf.Page, sheet, fields: dict, anchors: dict) -> None:
    doc = raw.doc
    H = sheet.height
    vec: list[str] = []
    txt: list[str] = []
    fonts_used: dict[str, int] = {}
    imgs_used: dict[str, int] = {}
    links = []
    svgs = []
    for op in sheet.below + sheet.ops:
        kind = op[0]
        if kind == "rect":
            _, x0, y0, x1, y1, fill, stroke, width, dash, radius = op
            if (x1 - x0 <= 0 and y1 - y0 <= 0) or (fill is None and stroke is None):
                continue
            if radius:
                pts = [(x0 + px, y0 + py) for px, py in shapes._round_rect(x1 - x0, y1 - y0, radius)[0][0]]
                vec.append(_poly(pts, fill, stroke, width, dash, True, H))
            else:
                vec.append(_rect(x0, y0, x1, y1, fill, stroke, width, dash, H))
        elif kind == "line":
            _, x0, y0, x1, y1, color, width, dash = op
            d = f"{dash} d " if dash else ""
            vec.append(f"q {_c(color)} RG {_f(width)} w {d}{_f(x0)} {_f(H - y0)} m {_f(x1)} {_f(H - y1)} l S Q")
        elif kind == "poly":
            _, points, fill, stroke, width, closed, dash = op
            if len(points) >= 2 and not (fill is None and stroke is None):
                vec.append(_poly(points, fill, stroke, width, dash, closed, H))
        elif kind == "text":
            _, x, y, text, style = op
            _text(raw, txt, x, y, text, style, fields, H, fonts_used)
        elif kind == "image":
            _, x0, y0, x1, y1, data, clip = op
            if x1 - x0 < 0.1 or y1 - y0 < 0.1:
                continue
            if isinstance(data, tuple) and data and data[0] == "pdf":
                svgs.append((pymupdf.Rect(x0, y0, x1, y1), data[1]))
                continue
            if data[:5] in (b"<?xml", b"<svg ") or data[:200].lstrip().startswith((b"<?xml", b"<svg")):
                svgs.append((pymupdf.Rect(x0, y0, x1, y1), ("svg", data)))
                continue
            res = raw.image(data)
            if res is None:
                continue
            imgs_used[res["name"]] = res["xref"]
            vec.append(f"q {_f(x1 - x0)} 0 0 {_f(y1 - y0)} {_f(x0)} {_f(H - y1)} cm /{res['name']} Do Q")
        elif kind == "link":
            links.append(op)
    content = ("\n".join(vec) + "\n" + "\n".join(txt)).encode("latin-1", "replace")
    cx = doc.get_new_xref()
    doc.update_object(cx, "<<>>")
    doc.update_stream(cx, content)
    doc.xref_set_key(page.xref, "Contents", f"{cx} 0 R")
    font_res = "".join(f"/{name} {xref} 0 R" for name, xref in fonts_used.items())
    img_res = "".join(f"/{name} {xref} 0 R" for name, xref in imgs_used.items())
    doc.xref_set_key(page.xref, "Resources", f"<</Font<<{font_res}>>/XObject<<{img_res}>>>>")
    for rect, payload in svgs:
        try:
            if isinstance(payload, tuple) and payload[0] == "svg":
                svg = pymupdf.open(stream=payload[1], filetype="svg")
                src = pymupdf.open("pdf", svg.convert_to_pdf())
            else:
                src = pymupdf.open("pdf", payload)
            page.show_pdf_page(rect, src, 0)
            src.close()
        except Exception:  # noqa: BLE001
            continue
    for op in links:
        _, x0, y0, x1, y1, target = op
        rect = pymupdf.Rect(x0, y0, x1, y1)
        try:
            if target.startswith("uri:"):
                page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": target[4:]})
            elif target.startswith("anchor:"):
                dest = anchors.get(target[7:])
                if dest is not None:
                    page.insert_link({"kind": pymupdf.LINK_GOTO, "from": rect, "page": dest[0], "to": pymupdf.Point(0, dest[1])})
        except Exception:  # noqa: BLE001
            continue


def _paint_op(fill, stroke, closed: bool) -> str:
    if fill is not None and stroke is not None:
        return "b" if closed else "B"
    if fill is not None:
        return "f"
    return "s" if closed else "S"


def _style_ops(fill, stroke, width, dash) -> str:
    out = ""
    if fill is not None:
        out += f"{_c(fill)} rg "
    if stroke is not None:
        out += f"{_c(stroke)} RG {_f(max(width, 0.1))} w "
        if dash:
            out += f"{dash} d "
    return out


def _rect(x0, y0, x1, y1, fill, stroke, width, dash, H) -> str:
    return f"q {_style_ops(fill, stroke, width, dash)}{_f(x0)} {_f(H - y1)} {_f(x1 - x0)} {_f(y1 - y0)} re {_paint_op(fill, stroke, True)} Q"


def _poly(points, fill, stroke, width, dash, closed, H) -> str:
    first = points[0]
    path = f"{_f(first[0])} {_f(H - first[1])} m " + " ".join(f"{_f(x)} {_f(H - y)} l" for x, y in points[1:])
    if closed:
        path += " h"
    return f"q {_style_ops(fill, stroke, width, dash)}{path} {_paint_op(fill, stroke, closed)} Q"


def _text(raw: RawDoc, out: list, x: float, y: float, text: str, style, fields: dict, H: float, fonts_used: dict) -> None:
    from .typeset import fallback_face
    if "{" in text:
        for name, value in fields.items():
            text = text.replace("{" + name + "}", str(value))
    if not text.strip():
        return
    size = style.effective_size()
    base = style.face()
    spacing = style.spacing
    color = style.color
    cx = x
    chunk: list[int] = []
    chunk_face = None
    chunk_x = x
    chunk_text = []

    def put(face, gids, chars, px):
        pf = raw.font(face)
        for g, ch in zip(gids, chars):
            pf.used[g] = ch
        fonts_used[pf.name] = pf.xref
        hexs = "".join(f"{g:04X}" for g in gids)
        shear = ITALIC_SHEAR if base.fake_italic else 0.0
        extra = ""
        tc = spacing + (0.02 * size if base.fake_bold else 0.0)
        ops = f"q {_c(color)} rg "
        if base.fake_bold:
            ops += f"{_c(color)} RG {_f(max(0.2, size * 0.035))} w 2 Tr "
        if tc:
            ops += f"{tc:.3f} Tc "
        ops += f"BT /{pf.name} {_f(size)} Tf 1 0 {shear:.3f} 1 {_f(px)} {_f(H - y)} Tm <{hexs}> Tj ET Q"
        out.append(ops)

    for ch in text:
        face = base if base.has(ch) else fallback_face(base, ch)
        layout, shift, natural = face.metrics(ch)
        bold_delta = 0.02 if face.fake_bold else 0.0
        exact = abs(layout - bold_delta - natural) < 0.004 and shift == 0.0
        gid = face.gid(ch)
        if gid == 0:
            # no face has this character (an emoji, say): leave the space it would take rather than a box
            if chunk:
                put(chunk_face, chunk, chunk_text, chunk_x)
                chunk, chunk_text, chunk_face = [], [], None
            cx += layout * size + spacing
            continue
        if exact and face is chunk_face:
            chunk.append(gid)
            chunk_text.append(ch)
        else:
            if chunk:
                put(chunk_face, chunk, chunk_text, chunk_x)
                chunk, chunk_text = [], []
            if exact:
                chunk, chunk_text, chunk_face, chunk_x = [gid], [ch], face, cx
            else:
                put(face, [gid], [ch], cx + shift * size)
                chunk_face = None
        cx += layout * size + spacing
    if chunk:
        put(chunk_face, chunk, chunk_text, chunk_x)
