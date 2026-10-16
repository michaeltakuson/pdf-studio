"""PDF to PowerPoint (.pptx).

One slide per page, the slide having the page's size. Each page's graphics
(lines, shapes, colours) become a background picture, every picture on the
page is kept as a picture of its own, and each block of text is an editable
text box at its original position, font, size and colour. The biggest text
near the top becomes the slide's title so the outline view works.

A page that is a scan (a picture with no text) stays one picture; where a scan
has a text layer from character recognition the text is added invisibly, so it
can still be searched and selected.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

import pymupdf

from .pdf_analyze import Block, PageInfo, Span, analyze_page, line_text, pix_unicolor, word_font, inside
from .to_docx import _BULLET, _first_word_width, _needs_space, _trim_trailing, esc

EMU = 12700
NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')


def emu(pt: float) -> int:
    return int(round(pt * EMU))


def block_align(block: Block) -> str:
    """'ctr' or 'r' when the lines of a block share their centre or right edge but not their left edge, else 'l'."""
    lines = block.lines
    if len(lines) < 2:
        return "l"
    lefts = [ln.bbox[0] for ln in lines]
    rights = [ln.bbox[2] for ln in lines]
    centers = [(ln.bbox[0] + ln.bbox[2]) / 2 for ln in lines]
    if max(lefts) - min(lefts) < 2.0:
        return "l"
    if max(centers) - min(centers) < 2.5:
        return "ctr"
    if max(rights) - min(rights) < 2.5:
        return "r"
    return "l"


def _items_for(block: Block, force_hard: bool = False):
    """Paragraph content of a block: [(Span|None, text)] with ``None`` text '\\n' meaning a line break."""
    lines = block.lines
    right = max(ln.bbox[2] for ln in lines)
    out: list = []
    for i, ln in enumerate(lines):
        for sp in ln.spans:
            out.append(sp)
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            this_txt = line_text(ln).rstrip()
            next_txt = line_text(nxt).lstrip()
            size = max((s.size for s in ln.spans), default=10.0)
            gap = right - ln.bbox[2]
            hard = force_hard or bool(_BULLET.match(next_txt)) or (gap >= _first_word_width(next_txt, size) + size * 0.3 and gap > size * 1.2)
            if hard:
                out.append("\n")
            elif this_txt and next_txt and _needs_space(this_txt[-1], next_txt[0]):
                out.append(" ")
    return out


class PptxFromPdf:
    def __init__(self, doc: pymupdf.Document, pages: list[int] | None = None, dpi: int = 0):
        self.doc = doc
        self.pages = pages if pages is not None else list(range(doc.page_count))
        self.slides: list[dict] = []
        self.media: list[tuple[str, bytes]] = []
        self.warnings: list[str] = []
        self.size = None

    # ------------------------------------------------------------------ images
    def _add_media(self, data: bytes, ext: str) -> str:
        name = f"image{len(self.media) + 1}.{ext}"
        self.media.append((name, data))
        return name

    def _textless(self, pno: int, remove_images: bool):
        tmp = pymupdf.open()
        tmp.insert_pdf(self.doc, from_page=pno, to_page=pno)
        page = tmp[0]
        try:
            for b in page.get_text("blocks"):
                if b[6] == 0:
                    page.add_redact_annot(pymupdf.Rect(b[:4]), fill=False)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE if remove_images else pymupdf.PDF_REDACT_IMAGE_NONE,
                                  graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        except Exception:  # noqa: BLE001
            pass
        return tmp, page

    @staticmethod
    def _dpi_for(page) -> int:
        longest = max(page.rect.width, page.rect.height)
        return int(max(96, min(200, 2200 / longest * 72)))

    @staticmethod
    def _encode(pix) -> tuple[bytes, str]:
        try:
            small = pymupdf.Pixmap(pix, max(1, pix.width // 8), max(1, pix.height // 8))
            colors = len({small.pixel(x, y) for x in range(0, small.width, 2) for y in range(0, small.height, 2)})
        except Exception:  # noqa: BLE001
            colors = 1000
        if colors > 500:
            return pix.tobytes("jpeg", jpg_quality=88), "jpeg"
        return pix.tobytes("png"), "png"

    # ------------------------------------------------------------------ pages
    def convert(self) -> bytes:
        first = self.doc[self.pages[0]].rect
        self.size = (first.width, first.height)
        for pno in self.pages:
            page = self.doc[pno]
            info = analyze_page(page)
            self.slides.append(self._slide(page, pno, info))
        return self._package()

    def _slide(self, page, pno, info: PageInfo) -> dict:
        shapes: list[str] = []
        rels: list[tuple[str, str, str]] = []     # (rid, type, target)
        next_id = [2]

        def new_id():
            next_id[0] += 1
            return next_id[0]

        def add_pic(data: bytes, ext: str, x, y, w, h, name: str) -> None:
            fname = self._add_media(data, ext)
            rid = f"rId{len(rels) + 2}"
            rels.append((rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", f"../media/{fname}"))
            sid = new_id()
            shapes.append(
                f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="{esc(name)} {sid}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
                f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
                f'<p:spPr><a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>')

        W, H = info.width, info.height
        dpi = self._dpi_for(page)
        # background: the page without text and without pictures
        bg_color = None
        tmp, tpage = self._textless(pno, remove_images=not info.bg_image)
        try:
            pix = tpage.get_pixmap(dpi=dpi, alpha=False)
            flat = pix_unicolor(pix)
            if flat is not None:
                bg_color = "".join(f"{v:02X}" for v in flat[:3])
            else:
                data, ext = self._encode(pix)
                add_pic(data, ext, 0, 0, W, H, "背景")
        finally:
            tmp.close()
        # pictures of their own
        if not info.bg_image:
            tmp2, tpage2 = self._textless(pno, remove_images=False)
            try:
                for im in info.images:
                    bb = im["bbox"]
                    w, h = bb[2] - bb[0], bb[3] - bb[1]
                    if w < 8 or h < 8 or w * h > W * H * 0.85:
                        continue
                    clip = pymupdf.Rect(bb) & tpage2.rect
                    if clip.is_empty:
                        continue
                    pix = tpage2.get_pixmap(dpi=dpi, clip=clip, alpha=False)
                    data, ext = self._encode(pix)
                    add_pic(data, ext, clip.x0, clip.y0, clip.width, clip.height, "図")
            finally:
                tmp2.close()
        # text
        blocks = [b for b in info.blocks if b.text().strip()]
        title = self._pick_title(blocks, info)
        invisible = info.bg_image
        for block in sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            shapes.append(self._text_shape(block, new_id(), block is title, invisible, rels))
        return {"shapes": "".join(shapes), "rels": rels, "bg": bg_color}

    @staticmethod
    def _pick_title(blocks: list[Block], info: PageInfo):
        best = None
        for b in blocks:
            if b.bbox[1] > info.height * 0.4 or len(b.lines) > 2 or len(b.text().strip()) > 100:
                continue
            if b.size < info.body_size * 1.2:
                continue
            if best is None or b.size > best.size or (b.size == best.size and b.bbox[1] < best.bbox[1]):
                best = b
        return best

    def _text_shape(self, block: Block, sid: int, is_title: bool, invisible: bool, rels: list) -> str:
        x0, y0, x1, y1 = block.bbox
        lines = block.lines
        align = block_align(block)
        items = _items_for(block, force_hard=align != "l")
        hard_breaks = sum(1 for i in items if i == "\n")
        multi = align == "l" and len(lines) >= 2 and (hard_breaks < len(lines) - 1 or len(lines) >= 3)
        width = (x1 - x0) * (1.05 if len(lines) > 1 else 1.0) + 4
        height = max(y1 - y0, 6)
        first_line = lines[0]
        # paragraph line spacing from the baselines
        spc = ""
        if len(lines) >= 2:
            pitch = (lines[-1].bbox[1] - lines[0].bbox[1]) / (len(lines) - 1)
            if pitch > 0:
                spc = f'<a:lnSpc><a:spcPts val="{int(pitch * 100)}"/></a:lnSpc>'
        # tabs: positions inside the box
        tabs = ""
        stops = sorted({round(sp.bbox[2] - x0, 1) for ln in lines for sp in ln.spans if sp.text == "\t"})
        if stops:
            tabs = "<a:tabLst>" + "".join(f'<a:tab pos="{emu(t)}" algn="l"/>' for t in stops) + "</a:tabLst>"
        ppr = f'<a:pPr marL="0" indent="0" algn="{align}">{spc}<a:spcBef><a:spcPts val="0"/></a:spcBef><a:spcAft><a:spcPts val="0"/></a:spcAft><a:buNone/>{tabs}</a:pPr>'
        runs = ""
        for it in items:
            if it == "\n":
                runs += f'<a:br>{self._rpr(items_first_span(items), "a:rPr", invisible)}</a:br>'
            elif isinstance(it, str):
                sp = items_first_span(items)
                runs += self._run(it, sp, invisible, rels)
            else:
                runs += self._run(it.text, it, invisible, rels)
        end = self._rpr(first_line.spans[0], "a:endParaRPr", invisible)
        para = f"<a:p>{ppr}{runs}{end}</a:p>"
        wrap = "square" if multi else "none"
        name = "タイトル" if is_title else "テキスト"
        if is_title:
            return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name} {sid}"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                    f'<p:spPr><a:xfrm><a:off x="{emu(x0)}" y="{emu(y0)}"/><a:ext cx="{emu(width)}" cy="{emu(height)}"/></a:xfrm></p:spPr>'
                    f'<p:txBody><a:bodyPr wrap="{wrap}" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>{para}</p:txBody></p:sp>')
        return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name} {sid}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="{emu(x0)}" y="{emu(y0)}"/><a:ext cx="{emu(width)}" cy="{emu(height)}"/></a:xfrm>'
                f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
                f'<p:txBody><a:bodyPr wrap="{wrap}" lIns="0" tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>{para}</p:txBody></p:sp>')

    def _rpr(self, sp: Span | None, tag: str, invisible: bool, link_rid: str | None = None) -> str:
        if sp is None:
            return f'<{tag} lang="ja-JP" dirty="0"/>'
        attrs = f'lang="ja-JP" altLang="en-US" sz="{max(100, int(round(sp.size * 100)))}"'
        if sp.bold:
            attrs += ' b="1"'
        if sp.italic:
            attrs += ' i="1"'
        if sp.sup:
            attrs += ' baseline="30000"'
        font = esc(word_font(sp.font))
        color = sp.color or "000000"
        fill = (f'<a:solidFill><a:srgbClr val="{color}"><a:alpha val="0"/></a:srgbClr></a:solidFill>' if invisible
                else f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>')
        link = f'<a:hlinkClick r:id="{link_rid}"/>' if link_rid else ""
        return f'<{tag} {attrs} dirty="0">{fill}<a:latin typeface="{font}"/><a:ea typeface="{font}"/><a:cs typeface="{font}"/>{link}</{tag}>'

    def _run(self, text: str, sp: Span | None, invisible: bool, rels: list) -> str:
        if not text:
            return ""
        out = ""
        parts = text.split("\t")
        for i, part in enumerate(parts):
            if i:
                out += f'<a:r>{self._rpr(sp, "a:rPr", invisible)}<a:t>\t</a:t></a:r>'
            if part:
                link_rid = None
                if sp is not None and sp.link:
                    link_rid = f"rId{len(rels) + 2}"
                    rels.append((link_rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", sp.link + "\0external"))
                out += f'<a:r>{self._rpr(sp, "a:rPr", invisible, link_rid)}<a:t>{esc(part)}</a:t></a:r>'
        return out

    # ------------------------------------------------------------------ packaging
    def _package(self) -> bytes:
        W, H = self.size
        n = len(self.slides)
        ctypes = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
                  '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>',
                  '<Default Extension="jpeg" ContentType="image/jpeg"/><Default Extension="jpg" ContentType="image/jpeg"/>',
                  '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
                  '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
                  '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
                  '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>']
        for i in range(n):
            ctypes.append(f'<Override PartName="/ppt/slides/slide{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
        ctypes.append("</Types>")
        pres_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>']
        sld_ids = ""
        for i in range(n):
            pres_rels.append(f'<Relationship Id="rId{i + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i + 1}.xml"/>')
            sld_ids += f'<p:sldId id="{256 + i}" r:id="rId{i + 2}"/>'
        pres_rels.append(f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>')
        pres_rels.append("</Relationships>")
        presentation = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation {NS} saveSubsetFonts="1">'
                        f'<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>{sld_ids}</p:sldIdLst>'
                        f'<p:sldSz cx="{emu(W)}" cy="{emu(H)}"/><p:notesSz cx="6858000" cy="9144000"/>'
                        '<p:defaultTextStyle><a:defPPr><a:defRPr lang="ja-JP"/></a:defPPr></p:defaultTextStyle></p:presentation>')
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "".join(ctypes))
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>')
            z.writestr("ppt/presentation.xml", presentation)
            z.writestr("ppt/_rels/presentation.xml.rels", "".join(pres_rels))
            z.writestr("ppt/slideMasters/slideMaster1.xml", MASTER_XML)
            z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>'
                       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>')
            z.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT_XML)
            z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>')
            z.writestr("ppt/theme/theme1.xml", THEME_XML)
            for i, slide in enumerate(self.slides):
                bg = f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{slide["bg"]}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>' if slide["bg"] else ""
                xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld {NS}><p:cSld>{bg}<p:spTree>'
                       '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                       '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
                       f'{slide["shapes"]}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')
                z.writestr(f"ppt/slides/slide{i + 1}.xml", xml)
                rel = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>']
                for rid, typ, target in slide["rels"]:
                    if target.endswith("\0external"):
                        rel.append(f'<Relationship Id="{rid}" Type="{typ}" Target="{esc(target[:-9])}" TargetMode="External"/>')
                    else:
                        rel.append(f'<Relationship Id="{rid}" Type="{typ}" Target="{target}"/>')
                rel.append("</Relationships>")
                z.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels", "".join(rel))
            for name, data in self.media:
                z.writestr(f"ppt/media/{name}", data)
        return out.getvalue()


def items_first_span(items):
    for it in items:
        if isinstance(it, Span):
            return it
    return None


MASTER_XML = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster {NS}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
    '<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="タイトル プレースホルダー 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
    '<p:spPr><a:xfrm><a:off x="457200" y="274638"/><a:ext cx="8229600" cy="1143000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>'
    '<p:txBody><a:bodyPr vert="horz" lIns="0" tIns="0" rIns="0" bIns="0" rtlCol="0" anchor="t"><a:noAutofit/></a:bodyPr><a:lstStyle/>'
    '<a:p><a:r><a:rPr lang="ja-JP" altLang="en-US"/><a:t>マスター タイトルの書式設定</a:t></a:r></a:p></p:txBody></p:sp>'
    '</p:spTree></p:cSld>'
    '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    '<p:txStyles><p:titleStyle><a:lvl1pPr algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc>'
    '<a:spcBef><a:spcPct val="0"/></a:spcBef><a:buNone/><a:defRPr sz="4400" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
    '<a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/><a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr></p:titleStyle>'
    '<p:bodyStyle><a:lvl1pPr marL="0" indent="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:lnSpc><a:spcPct val="100000"/></a:lnSpc>'
    '<a:spcBef><a:spcPts val="0"/></a:spcBef><a:buNone/><a:defRPr sz="1800" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
    '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:bodyStyle>'
    '<p:otherStyle><a:defPPr><a:defRPr lang="ja-JP"/></a:defPPr><a:lvl1pPr marL="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1">'
    '<a:defRPr sz="1800" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:otherStyle>'
    '</p:txStyles></p:sldMaster>'
)

LAYOUT_XML = (
    f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout {NS} type="titleOnly" preserve="1"><p:cSld name="タイトルのみ"><p:spTree>'
    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    '<p:sp><p:nvSpPr><p:cNvPr id="2" name="タイトル 1"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
    '<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="ja-JP" altLang="en-US"/><a:t>タイトルを入力</a:t></a:r></a:p></p:txBody></p:sp>'
    '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
)

THEME_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="PDF Studio"><a:themeElements>'
    '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
    '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface="游ゴシック Light"/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface="游ゴシック"/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>'
    '<a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>'
    '<a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/><a:miter lim="800000"/></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'
)


def convert(doc: pymupdf.Document, pages: list[int] | None = None) -> tuple[bytes, list[str]]:
    conv = PptxFromPdf(doc, pages)
    return conv.convert(), conv.warnings
