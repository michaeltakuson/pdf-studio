"""PowerPoint (.pptx) to PDF.

Each slide becomes a page of the slide's own size. Shapes, pictures, tables,
SmartArt drawings and text are drawn from the file's own formatting: theme
colours and fonts, the slide master and layout (placeholders inherit their
position and text style from them), bullets, auto-fit and so on. Text is real
text in embedded fonts.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from . import typeset as T
from .drawingml import Ctx, draw_shape, parse_fill, parse_line
from .ooxml import (NS, Package, PackageError, Theme, bullet_char, color_from_drawingml, emu, format_number, local)
from .typeset import Atom, Sheet, Style, TabStop

P = NS["p"]
A = NS["a"]
R = NS["r"]


def pq(tag):
    return "{%s}%s" % (P, tag)


def aq(tag):
    return "{%s}%s" % (A, tag)


# ============================================================================ text property parsing


def parse_drpr(el, ctx: Ctx, theme: Theme) -> dict:
    """a:defRPr / a:rPr / a:endParaRPr -> dict."""
    out: dict = {}
    if el is None:
        return out
    if el.get("sz"):
        out["sz"] = int(el.get("sz")) / 100.0
    for key in ("b", "i"):
        if el.get(key) is not None:
            out[key] = el.get(key) in ("1", "true")
    if el.get("u"):
        out["u"] = None if el.get("u") == "none" else el.get("u")
    if el.get("strike"):
        out["strike"] = el.get("strike") != "noStrike"
    if el.get("baseline"):
        try:
            b = int(el.get("baseline"))
            out["vert"] = 1 if b > 0 else (-1 if b < 0 else 0)
        except ValueError:
            pass
    if el.get("spc"):
        try:
            out["spc"] = int(el.get("spc")) / 100.0
        except ValueError:
            pass
    if el.get("cap"):
        out["caps"] = el.get("cap") == "all"
    fill = parse_fill(el, ctx)
    if fill and fill[0] == "solid":
        out["color"] = fill[1]
    for tag, key in (("latin", "latin"), ("ea", "ea"), ("cs", "cs")):
        node = el.find("a:%s" % tag, NS)
        if node is not None and node.get("typeface"):
            face = node.get("typeface")
            if face.startswith("+"):
                group = "major" if face.startswith("+mj") else "minor"
                slot = {"lt": "latin", "ea": "ea", "cs": "cs"}.get(face[-2:], "latin")
                face = theme.fonts[group].get(slot, "")
            if face:
                out[key] = face
    hl = el.find("a:highlight", NS)
    if hl is not None:
        c = color_from_drawingml(hl, ctx.theme, ctx.scheme_map)
        if c is not None:
            out["hl"] = c
    link = el.find("a:hlinkClick", NS)
    if link is not None:
        out["link_rid"] = link.get("{%s}id" % R)
    return out


def parse_spacing(el):
    if el is None:
        return None
    pct = el.find("a:spcPct", NS)
    if pct is not None:
        return ("pct", int(pct.get("val", "100000")) / 100000.0)
    pts = el.find("a:spcPts", NS)
    if pts is not None:
        return ("pts", int(pts.get("val", "0")) / 100.0)
    return None


def parse_ppr(el, ctx: Ctx, theme: Theme) -> dict:
    out: dict = {}
    if el is None:
        return out
    for key, name in (("marL", "marL"), ("marR", "marR"), ("indent", "indent")):
        if el.get(key) is not None:
            out[name] = emu(el.get(key))
    if el.get("algn"):
        out["algn"] = el.get("algn")
    if el.get("lvl"):
        out["lvl"] = int(el.get("lvl"))
    for child in el:
        tag = local(child.tag)
        if tag == "lnSpc":
            out["lnSpc"] = parse_spacing(child)
        elif tag == "spcBef":
            out["spcBef"] = parse_spacing(child)
        elif tag == "spcAft":
            out["spcAft"] = parse_spacing(child)
        elif tag == "buNone":
            out["bu"] = ("none",)
        elif tag == "buChar":
            out["bu"] = ("char", child.get("char") or "•")
        elif tag == "buAutoNum":
            out["bu"] = ("auto", child.get("type") or "arabicPeriod", int(child.get("startAt") or 1))
        elif tag == "buBlip":
            out["bu"] = ("char", "•")
        elif tag == "buClr":
            c = color_from_drawingml(child, ctx.theme, ctx.scheme_map)
            if c is not None:
                out["buClr"] = c
        elif tag == "buClrTx":
            out.pop("buClr", None)
        elif tag == "buSzPct":
            out["buSzPct"] = int(child.get("val", "100000")) / 100000.0
        elif tag == "buFont":
            out["buFont"] = child.get("typeface")
        elif tag == "tabLst":
            out["tabs"] = [(emu(t.get("pos")), t.get("algn", "l")) for t in child.findall("a:tab", NS)]
        elif tag == "defRPr":
            out["defRPr"] = parse_drpr(child, ctx, theme)
    return out


def merge_ppr(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if k == "defRPr":
            out["defRPr"] = {**base.get("defRPr", {}), **v}
        else:
            out[k] = v
    return out


def parse_lststyle(el, ctx: Ctx, theme: Theme) -> dict:
    """a:lstStyle / p:titleStyle ... -> {level: ppr dict}."""
    out = {}
    if el is None:
        return out
    for child in el:
        tag = local(child.tag)
        if tag.startswith("lvl") and tag.endswith("pPr"):
            try:
                lvl = int(tag[3]) - 1
            except ValueError:
                continue
            out[lvl] = parse_ppr(child, ctx, theme)
        elif tag == "defPPr":
            out.setdefault("def", parse_ppr(child, ctx, theme))
    return out


# ============================================================================ table styles

_BUILTIN_STYLE_COLORS = {
    # Medium Style 2 - Accent 1: header accent, bands tinted
    "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}": "accent1",
    "{21E4AEA4-8DFA-4A89-87EB-49C32662AFE0}": "accent2",
    "{F5AB1C69-6EDB-4FF4-983F-18BD219EF322}": "accent3",
    "{00A15C55-8517-42AA-B614-E9B94910E393}": "accent4",
    "{7DF18680-E054-41AD-8BC1-D1AEF772440D}": "accent5",
    "{93296810-A885-4BE3-A3E7-6D5BEEA58F35}": "accent6",
}


# ============================================================================ the converter


class PptxToPdf:
    def __init__(self, data: bytes):
        self.pkg = Package(data)
        if not self.pkg.has("ppt/presentation.xml"):
            raise PackageError("PowerPoint ファイルとして読み込めませんでした（.pptx 形式のみ対応しています。古い .ppt は PowerPoint で .pptx として保存し直してください）")
        self.pres = self.pkg.xml("ppt/presentation.xml")
        sz = self.pres.find(pq("sldSz"))
        self.W = emu(sz.get("cx")) if sz is not None else 960.0
        self.H = emu(sz.get("cy")) if sz is not None else 540.0
        self.images: dict[str, bytes | None] = {}
        self.theme_cache: dict[str, Theme] = {}
        self.table_styles = self._load_table_styles()
        self.sheets: list[Sheet] = []
        self.slide_no = 0
        self.default_text_style = None
        self.warnings: list[str] = []

    # ---------------------------------------------------------------- package access
    def _slides(self):
        out = []
        rels = self.pkg.rels("ppt/presentation.xml")
        lst = self.pres.find(pq("sldIdLst"))
        if lst is None:
            return out
        for sld in lst:
            rid = sld.get("{%s}id" % R)
            rel = rels.get(rid)
            if rel:
                out.append(rel[0])
        return out

    def _theme_for(self, master_part: str) -> Theme:
        if master_part in self.theme_cache:
            return self.theme_cache[master_part]
        theme_part = None
        for target, kind, _m in self.pkg.rels(master_part).values():
            if kind == "theme":
                theme_part = target
        theme = Theme(self.pkg.xml(theme_part) if theme_part else None)
        self.theme_cache[master_part] = theme
        return theme

    def _load_table_styles(self):
        out = {}
        root = self.pkg.xml("ppt/tableStyles.xml")
        if root is not None:
            for st in root:
                if local(st.tag) == "tblStyle":
                    out[st.get("styleId")] = st
        return out

    def image(self, rels: dict, rid: str | None) -> bytes | None:
        if not rid:
            return None
        rel = rels.get(rid)
        if not rel or rel[2] == "External":
            return None
        key = rel[0]
        if key not in self.images:
            raw = self.pkg.read(key)
            if raw is not None and not _raster(raw):
                raw = None
            self.images[key] = raw
        return self.images[key]

    # ---------------------------------------------------------------- conversion
    def convert(self) -> bytes:
        dts = self.pres.find(pq("defaultTextStyle"))
        slides = self._slides()
        if not slides:
            raise PackageError("スライドが見つかりませんでした")
        for part in slides:
            root = self.pkg.xml(part)
            if root is None:
                continue
            if root.get("show") == "0":
                continue
            self.slide_no += 1
            self._draw_slide(part, root, dts)
        doc = T.render(self.sheets)
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
        return data

    def _draw_slide(self, part: str, root: ET.Element, dts):
        rels = self.pkg.rels(part)
        layout_part = next((t for t, k, _m in rels.values() if k == "slideLayout"), None)
        layout = self.pkg.xml(layout_part) if layout_part else None
        master_part = None
        if layout_part:
            master_part = next((t for t, k, _m in self.pkg.rels(layout_part).values() if k == "slideMaster"), None)
        master = self.pkg.xml(master_part) if master_part else None
        theme = self._theme_for(master_part or "")
        scheme_map = {}
        if master is not None:
            cm = master.find(pq("clrMap"))
            if cm is not None:
                scheme_map = dict(cm.attrib)
        # a layout or slide may override the mapping
        for el in (layout, root):
            if el is not None:
                ov = el.find(pq("clrMapOvr"))
                if ov is not None:
                    m = ov.find("a:overrideClrMapping", NS)
                    if m is not None:
                        scheme_map = dict(m.attrib)
        sheet = Sheet(self.W, self.H)
        self.sheets.append(sheet)
        ctx_slide = SlideCtx(self, theme, scheme_map, rels, part)
        ctx_slide.dts = parse_lststyle(dts, ctx_slide.dc, theme) if dts is not None else {}
        if master is not None:
            ts = master.find(pq("txStyles"))
            ctx_slide.title_style = parse_lststyle(ts.find(pq("titleStyle")) if ts is not None else None, ctx_slide.dc, theme)
            ctx_slide.body_style = parse_lststyle(ts.find(pq("bodyStyle")) if ts is not None else None, ctx_slide.dc, theme)
            ctx_slide.other_style = parse_lststyle(ts.find(pq("otherStyle")) if ts is not None else None, ctx_slide.dc, theme)
        # placeholders available for inheritance
        ctx_slide.layout_ph = _placeholders(layout)
        ctx_slide.master_ph = _placeholders(master)
        # background: slide, then layout, then master
        self._background(sheet, root, layout, master, ctx_slide, rels, layout_part, master_part, theme, scheme_map)
        show_master = True
        if root.get("showMasterSp") == "0":
            show_master = False
        layout_show_master = layout is None or layout.get("showMasterSp") != "0"
        if show_master and layout_show_master and master is not None:
            mctx = SlideCtx.derive(ctx_slide, self.pkg.rels(master_part), master_part)
            self._draw_tree(sheet, master.find(pq("cSld")).find(pq("spTree")), mctx, skip_placeholders=True)
        if show_master and layout is not None:
            lctx = SlideCtx.derive(ctx_slide, self.pkg.rels(layout_part), layout_part)
            self._draw_tree(sheet, layout.find(pq("cSld")).find(pq("spTree")), lctx, skip_placeholders=True)
        self._draw_tree(sheet, root.find(pq("cSld")).find(pq("spTree")), ctx_slide)

    def _background(self, sheet, slide, layout, master, ctx, rels, layout_part, master_part, theme, scheme_map):
        for el, part in ((slide, ctx.part), (layout, layout_part), (master, master_part)):
            if el is None:
                continue
            bg = el.find(pq("cSld")).find(pq("bg"))
            if bg is None:
                continue
            r = self.pkg.rels(part) if part else {}
            dc = Ctx(theme, scheme_map, image_for=lambda rid, r=r: self.image(r, rid))
            pr = bg.find(pq("bgPr"))
            if pr is not None:
                fill = parse_fill(pr, dc)
                self._paint_bg(sheet, fill, dc)
                return
            ref = bg.find(pq("bgRef"))
            if ref is not None and len(ref):
                col = color_from_drawingml(ref[0], theme, scheme_map)
                if col is not None:
                    sheet.below.append(("rect", 0, 0, self.W, self.H, col, None, 0, None, None))
                    return

    def _paint_bg(self, sheet, fill, dc):
        if not fill or fill[0] == "none":
            return
        if fill[0] == "solid" and fill[1] is not None:
            sheet.below.append(("rect", 0, 0, self.W, self.H, fill[1], None, 0, None, None))
        elif fill[0] == "grad":
            from .drawingml import lerp_stops
            n = 40
            angle = fill[2]
            horizontal = (angle % 180) < 45 or (angle % 180) > 135
            for i in range(n):
                col = lerp_stops(fill[1], (i + 0.5) / n)
                if horizontal:
                    sheet.below.append(("rect", self.W * i / n, 0, self.W * (i + 1) / n + 0.4, self.H, col, None, 0, None, None))
                else:
                    sheet.below.append(("rect", 0, self.H * i / n, self.W, self.H * (i + 1) / n + 0.4, col, None, 0, None, None))
        elif fill[0] == "blip":
            data = dc.image_for(fill[1])
            if data is not None:
                sheet.below.append(("image", 0, 0, self.W, self.H, data, None))

    # ---------------------------------------------------------------- shape tree
    def _draw_tree(self, sheet, tree, ctx: "SlideCtx", skip_placeholders=False, xf=None):
        if tree is None:
            return
        for child in tree:
            tag = local(child.tag)
            if tag == "sp":
                if skip_placeholders and child.find(f"{pq('nvSpPr')}/{pq('nvPr')}/{pq('ph')}") is not None:
                    continue
                self._draw_sp(sheet, child, ctx, xf)
            elif tag == "pic":
                if skip_placeholders and child.find(f"{pq('nvPicPr')}/{pq('nvPr')}/{pq('ph')}") is not None:
                    continue
                self._draw_pic(sheet, child, ctx, xf)
            elif tag == "cxnSp":
                self._draw_cxn(sheet, child, ctx, xf)
            elif tag == "grpSp":
                self._draw_group(sheet, child, ctx, skip_placeholders, xf)
            elif tag == "graphicFrame":
                self._draw_frame(sheet, child, ctx, xf)
            elif tag == "AlternateContent":
                choice = child.find("{%s}Choice" % NS["mc"])
                fb = child.find("{%s}Fallback" % NS["mc"])
                # equations (a14:m) and other newer content: the fallback is a picture of it
                req = (choice.get("Requires") if choice is not None else "") or ""
                use = choice if (choice is not None and req in ("", "p14", "a14") and "a14" not in req and
                                 choice.find(".//{http://schemas.microsoft.com/office/drawing/2010/main}m") is None) else fb
                if use is None:
                    use = choice
                if use is not None:
                    self._draw_tree(sheet, use, ctx, skip_placeholders, xf)

    # ---- geometry
    def _xfrm(self, el, xf):
        """((x, y, w, h), rot, flipH, flipV) in points for an element's a:xfrm, mapped through a group transform."""
        if el is None:
            return None
        off = el.find("a:off", NS)
        ext = el.find("a:ext", NS)
        if off is None or ext is None:
            return None
        x, y = emu(off.get("x")), emu(off.get("y"))
        w, h = emu(ext.get("cx")), emu(ext.get("cy"))
        if xf:
            x = xf["dx"] + (x - xf["cx"]) * xf["sx"]
            y = xf["dy"] + (y - xf["cy"]) * xf["sy"]
            w *= xf["sx"]
            h *= xf["sy"]
        rot = int(el.get("rot", "0")) / 60000.0
        return (x, y, w, h), rot, el.get("flipH") == "1", el.get("flipV") == "1"

    def _draw_group(self, sheet, grp, ctx, skip_placeholders, xf):
        pr = grp.find(pq("grpSpPr"))
        x = pr.find("a:xfrm", NS) if pr is not None else None
        if x is None:
            self._draw_tree(sheet, grp, ctx, skip_placeholders, xf)
            return
        off, ext = x.find("a:off", NS), x.find("a:ext", NS)
        choff, chext = x.find("a:chOff", NS), x.find("a:chExt", NS)
        if None in (off, ext, choff, chext):
            self._draw_tree(sheet, grp, ctx, skip_placeholders, xf)
            return
        gx, gy, gw, gh = emu(off.get("x")), emu(off.get("y")), emu(ext.get("cx")), emu(ext.get("cy"))
        if xf:
            gx = xf["dx"] + (gx - xf["cx"]) * xf["sx"]
            gy = xf["dy"] + (gy - xf["cy"]) * xf["sy"]
            gw *= xf["sx"]
            gh *= xf["sy"]
        cw, chh = emu(chext.get("cx")), emu(chext.get("cy"))
        new = {"dx": gx, "dy": gy, "cx": emu(choff.get("x")), "cy": emu(choff.get("y")),
               "sx": (gw / cw) if cw else 1.0, "sy": (gh / chh) if chh else 1.0}
        self._draw_tree(sheet, grp, ctx, skip_placeholders, new)

    # ---- shapes
    def _draw_sp(self, sheet, sp, ctx: "SlideCtx", xf):
        sp_pr = sp.find(pq("spPr"))
        ph = sp.find(f"{pq('nvSpPr')}/{pq('nvPr')}/{pq('ph')}")
        box = self._xfrm(sp_pr.find("a:xfrm", NS) if sp_pr is not None else None, xf)
        inherited = None
        if ph is not None:
            inherited = ctx.find_ph(ph)
            if box is None and inherited is not None:
                ipr = inherited[0].find(pq("spPr")) if inherited[0] is not None else None
                box = self._xfrm(ipr.find("a:xfrm", NS) if ipr is not None else None, None)
            if box is None:
                for holder in (ctx.layout_ph, ctx.master_ph):
                    pass
        if box is None:
            return
        (x, y, w, h), rot, flip_h, flip_v = box
        style = sp.find(pq("style"))
        is_text_box = sp.find(f"{pq('nvSpPr')}/{pq('cNvSpPr')}") is not None and \
            sp.find(f"{pq('nvSpPr')}/{pq('cNvSpPr')}").get("txBox") == "1"
        eff_pr = sp_pr
        # a placeholder with no own fill/line takes the layout's (the master's) appearance
        if ph is not None and inherited is not None and sp_pr is not None:
            ipr = inherited[0].find(pq("spPr"))
            if ipr is not None and sp_pr.find("a:prstGeom", NS) is None and sp_pr.find("a:custGeom", NS) is None:
                geom = ipr.find("a:prstGeom", NS)
                if geom is not None:
                    sp_pr = _with_children(sp_pr, [geom] + [c for c in ipr if local(c.tag) in ("solidFill", "gradFill", "noFill", "ln", "blipFill")
                                                          and sp_pr.find("a:" + local(c.tag), NS) is None])
        elif ph is not None and inherited is not None and sp_pr is None:
            sp_pr = inherited[0].find(pq("spPr"))
        name = "rect"
        if sp_pr is not None or style is not None:
            has_fill = sp_pr is not None and any(local(c.tag) in ("solidFill", "gradFill", "noFill", "blipFill", "pattFill") for c in sp_pr)
            name = draw_shape(sheet, x, y, w, h, sp_pr, ctx.dc, style, rot, flip_h, flip_v,
                              image_op=True)
        tx = sp.find(pq("txBody"))
        if tx is not None:
            self._draw_text(sheet, tx, (x, y, w, h), ctx, ph, inherited, rot, is_text_box, style, name)

    def _draw_cxn(self, sheet, cxn, ctx, xf):
        sp_pr = cxn.find(pq("spPr"))
        box = self._xfrm(sp_pr.find("a:xfrm", NS) if sp_pr is not None else None, xf)
        if box is None:
            return
        (x, y, w, h), rot, fh, fv = box
        draw_shape(sheet, x, y, w, h, sp_pr, ctx.dc, cxn.find(pq("style")), rot, fh, fv, default_line=(0, 0, 0))

    def _draw_pic(self, sheet, pic, ctx, xf):
        sp_pr = pic.find(pq("spPr"))
        box = self._xfrm(sp_pr.find("a:xfrm", NS) if sp_pr is not None else None, xf)
        if box is None:
            ph = pic.find(f"{pq('nvPicPr')}/{pq('nvPr')}/{pq('ph')}")
            if ph is not None:
                inh = ctx.find_ph(ph)
                if inh is not None:
                    ipr = inh[0].find(pq("spPr"))
                    box = self._xfrm(ipr.find("a:xfrm", NS) if ipr is not None else None, None)
        if box is None:
            return
        (x, y, w, h), rot, fh, fv = box
        blip = pic.find(f"{pq('blipFill')}/a:blip", NS)
        data = self.image(ctx.rels, blip.get("{%s}embed" % R)) if blip is not None else None
        if data is not None:
            src = pic.find(f"{pq('blipFill')}/a:srcRect", NS)
            if src is not None:
                crop = {k: int(src.get(k, "0")) / 100000.0 for k in ("l", "t", "r", "b")}
                if any(crop.values()):
                    from .from_docx import _crop_image
                    data = _crop_image(data, crop) or data
            if rot in (90.0, 270.0) or abs(rot) > 0.5:
                data = _rotate_image(data, rot) or data
                if rot in (90.0, 270.0):
                    cx, cy = x + w / 2, y + h / 2
                    x, y, w, h = cx - h / 2, cy - w / 2, h, w
            sheet.image(x, y, x + w, y + h, data)
        ln = parse_line(sp_pr, ctx.dc)
        if isinstance(ln, dict) and ln.get("color") is not None:
            sheet.rect(x, y, x + w, y + h, stroke=ln["color"], width=ln["width"] or 0.75)

    # ---- graphic frames: tables, SmartArt, charts
    def _draw_frame(self, sheet, frame, ctx, xf):
        box = self._xfrm(frame.find(pq("xfrm")), xf)
        if box is None:
            return
        (x, y, w, h), rot, fh, fv = box
        gd = frame.find(".//a:graphicData", NS)
        if gd is None:
            return
        uri = gd.get("uri") or ""
        if uri.endswith("/table"):
            tbl = gd.find("a:tbl", NS)
            if tbl is not None:
                self._draw_table(sheet, tbl, x, y, w, h, ctx)
        elif uri.endswith("/diagram"):
            self._draw_smartart(sheet, gd, x, y, w, h, ctx)
        elif uri.endswith("/chart"):
            self._draw_chart(sheet, gd, x, y, w, h, ctx)
        else:
            for sub in gd:
                if local(sub.tag) in ("sp", "pic", "grpSp"):
                    self._draw_tree(sheet, gd, ctx, False, xf)
                    break

    def _draw_smartart(self, sheet, gd, x, y, w, h, ctx):
        rel = gd.find("{%s}relIds" % NS["dgm"])
        drawing_part = None
        if rel is not None:
            # the pre-rendered drawing is related to the slide by the id in dm (data model) siblings;
            # find the diagramDrawing relationship of the slide
            for target, kind, _m in ctx.rels.values():
                if kind == "diagramDrawing":
                    drawing_part = target
        if not drawing_part:
            return
        root = self.pkg.xml(drawing_part)
        if root is None:
            return
        rels = self.pkg.rels(drawing_part)
        sub = SlideCtx.derive(ctx, rels, drawing_part)
        tree = root.find("{%s}spTree" % NS["dsp"])
        if tree is None:
            return
        for sp in tree:
            if local(sp.tag) != "sp":
                continue
            spp = sp.find("{%s}spPr" % NS["dsp"])
            xfm = spp.find("a:xfrm", NS) if spp is not None else None
            b = self._xfrm(xfm, None)
            if b is None:
                continue
            (sx, sy, sw, sh), rot, fh, fv = b
            style = sp.find("{%s}style" % NS["dsp"])
            draw_shape(sheet, x + sx, y + sy, sw, sh, spp, sub.dc, style, rot, fh, fv, image_op=True)
            tx = sp.find("{%s}txBody" % NS["dsp"])
            tf = sp.find("{%s}txXfrm" % NS["dsp"])
            if tx is not None:
                tb = self._xfrm(tf, None) if tf is not None else b
                (tx_, ty_, tw_, th_) = tb[0]
                self._draw_text(sheet, tx, (x + tx_, y + ty_, tw_, th_), sub, None, None, rot, False, style, "rect",
                                font_ref=style)

    def _draw_chart(self, sheet, gd, x, y, w, h, ctx):
        chart_el = gd.find("{%s}chart" % NS["c"])
        rid = chart_el.get("{%s}id" % R) if chart_el is not None else None
        rel = ctx.rels.get(rid)
        if not rel:
            return
        root = self.pkg.xml(rel[0])
        if root is None:
            return
        from . import charts
        charts.draw_chart(sheet, root, x, y, w, h, ctx.dc)

    # ---- tables
    def _draw_table(self, sheet, tbl, x, y, w, h, ctx: "SlideCtx"):
        grid = [emu(g.get("w")) for g in tbl.findall("a:tblGrid/a:gridCol", NS)]
        pr = tbl.find("a:tblPr", NS)
        flags = {k: (pr.get(k) == "1") if pr is not None else False for k in ("firstRow", "lastRow", "firstCol", "lastCol", "bandRow", "bandCol")}
        sid_el = pr.find("a:tableStyleId", NS) if pr is not None else None
        sid = sid_el.text.strip() if sid_el is not None and sid_el.text else None
        style_el = self.table_styles.get(sid)
        accent = _BUILTIN_STYLE_COLORS.get(sid)
        rows = tbl.findall("a:tr", NS)
        nrows = len(rows)
        ncols = len(grid)
        colx = [x]
        for g in grid:
            colx.append(colx[-1] + g)
        # lay out cells to get row heights
        layouts = []
        row_h = []
        for ri, tr in enumerate(rows):
            h0 = emu(tr.get("h"))
            cells = []
            hmax = h0
            ci = 0
            for tc in tr.findall("a:tc", NS):
                span = int(tc.get("gridSpan", "1"))
                hmerge = tc.get("hMerge") == "1"
                vmerge = tc.get("vMerge") == "1"
                rowspan = int(tc.get("rowSpan", "1"))
                tcpr = tc.find("a:tcPr", NS)
                marl = emu(tcpr.get("marL", "91440")) if tcpr is not None else 7.2
                marr = emu(tcpr.get("marR", "91440")) if tcpr is not None else 7.2
                mart = emu(tcpr.get("marT", "45720")) if tcpr is not None else 3.6
                marb = emu(tcpr.get("marB", "45720")) if tcpr is not None else 3.6
                cell = {"ci": ci, "span": span, "hmerge": hmerge, "vmerge": vmerge, "rowspan": rowspan, "tc": tc,
                        "tcpr": tcpr, "mar": (marl, mart, marr, marb), "ri": ri}
                cw = colx[min(ci + span, ncols)] - colx[min(ci, ncols)]
                if not hmerge and not vmerge:
                    text_style = self._cell_text_style(style_el, flags, ri, ci, nrows, ncols, ctx, accent)
                    tx = tc.find("a:txBody", NS)
                    frame = self._layout_frame(tx, max(cw - marl - marr, 6.0), ctx, None, None, extra_style=text_style) if tx is not None else None
                    cell["frame"] = frame
                    if frame is not None and rowspan == 1:
                        hmax = max(hmax, frame["height"] + mart + marb)
                cells.append(cell)
                ci += span
            layouts.append(cells)
            row_h.append(hmax)
        total_h = sum(row_h)
        if total_h < h and False:
            pass
        rowy = [y]
        for rh in row_h:
            rowy.append(rowy[-1] + rh)
        for ri, cells in enumerate(layouts):
            for cell in cells:
                if cell["hmerge"] or cell["vmerge"]:
                    continue
                ci, span = cell["ci"], cell["span"]
                x0, x1 = colx[min(ci, ncols)], colx[min(ci + span, ncols)]
                y0 = rowy[ri]
                y1 = rowy[min(ri + cell["rowspan"], nrows)]
                fill, borders = self._cell_look(style_el, flags, ri, ci, nrows, ncols, ctx, accent, cell["tcpr"], span)
                if fill is not None:
                    sheet.rect(x0, y0, x1, y1, fill=fill)
                frame = cell.get("frame")
                if frame is not None:
                    marl, mart, marr, marb = cell["mar"]
                    inner_h = y1 - y0 - mart - marb
                    off = 0.0
                    anchor = cell["tcpr"].get("anchor") if cell["tcpr"] is not None else None
                    if anchor == "ctr":
                        off = max(0.0, (inner_h - frame["height"]) / 2)
                    elif anchor == "b":
                        off = max(0.0, inner_h - frame["height"])
                    self._emit_frame(sheet, frame, x0 + marl, y0 + mart + off)
                for side, spec in borders.items():
                    if spec is None:
                        continue
                    color, width = spec
                    if side == "T":
                        sheet.line(x0, y0, x1, y0, color, width)
                    elif side == "B":
                        sheet.line(x0, y1, x1, y1, color, width)
                    elif side == "L":
                        sheet.line(x0, y0, x0, y1, color, width)
                    elif side == "R":
                        sheet.line(x1, y0, x1, y1, color, width)

    def _cell_text_style(self, style_el, flags, ri, ci, nrows, ncols, ctx, accent):
        out = {}
        if style_el is None and accent is None:
            return out
        if accent is not None:
            if flags["firstRow"] and ri == 0:
                return {"b": True, "color": (1.0, 1.0, 1.0)}
            return {"color": (0.0, 0.0, 0.0)}
        for part in self._style_parts(flags, ri, ci, nrows, ncols):
            node = style_el.find("a:%s" % part, NS)
            if node is None:
                continue
            tx = node.find("a:tcTxStyle", NS)
            if tx is None:
                continue
            if tx.get("b") == "on":
                out["b"] = True
            if tx.get("i") == "on":
                out["i"] = True
            c = color_from_drawingml(tx, ctx.theme, ctx.scheme_map) if len(tx) else None
            for ch in tx:
                if local(ch.tag) in ("schemeClr", "srgbClr", "prstClr", "sysClr"):
                    c = color_from_drawingml(ch, ctx.theme, ctx.scheme_map)
                    out["color"] = c
        return out

    def _style_parts(self, flags, ri, ci, nrows, ncols):
        parts = ["wholeTbl"]
        if flags["bandRow"]:
            r = ri - (1 if flags["firstRow"] else 0)
            if r >= 0:
                parts.append("band1H" if r % 2 == 0 else "band2H")
        if flags["bandCol"]:
            c = ci - (1 if flags["firstCol"] else 0)
            if c >= 0:
                parts.append("band1V" if c % 2 == 0 else "band2V")
        if flags["firstCol"] and ci == 0:
            parts.append("firstCol")
        if flags["lastCol"] and ci == ncols - 1:
            parts.append("lastCol")
        if flags["firstRow"] and ri == 0:
            parts.append("firstRow")
        if flags["lastRow"] and ri == nrows - 1:
            parts.append("lastRow")
        return parts

    def _cell_look(self, style_el, flags, ri, ci, nrows, ncols, ctx, accent, tcpr, span):
        fill = None
        borders: dict = {}
        white = (1.0, 1.0, 1.0)
        if accent is not None:
            base = ctx.theme.color(accent)
            if flags["firstRow"] and ri == 0:
                fill = base
            else:
                tint = 0.4 if (ri - (1 if flags["firstRow"] else 0)) % 2 == 0 and flags["bandRow"] else 0.2
                fill = tuple(1 - (1 - c) * tint for c in base)
            for side in "TBLR":
                borders[side] = (white, 1.0)
        elif style_el is not None:
            for part in self._style_parts(flags, ri, ci, nrows, ncols):
                node = style_el.find("a:%s" % part, NS)
                if node is None:
                    continue
                tcs = node.find("a:tcStyle", NS)
                if tcs is None:
                    continue
                f = tcs.find("a:fill", NS)
                if f is not None:
                    pf = parse_fill(f, ctx.dc)
                    if pf and pf[0] == "solid":
                        fill = pf[1]
                    elif pf and pf[0] == "none":
                        fill = None
                bd = tcs.find("a:tcBdr", NS)
                if bd is not None:
                    for tag, side in (("left", "L"), ("right", "R"), ("top", "T"), ("bottom", "B"), ("insideH", "IH"), ("insideV", "IV")):
                        b = bd.find("a:%s" % tag, NS)
                        if b is None:
                            continue
                        ln = b.find("a:ln", NS)
                        if ln is None:
                            continue
                        if ln.find("a:noFill", NS) is not None:
                            borders[side] = None
                        else:
                            fl = parse_fill(ln, ctx.dc)
                            if fl and fl[0] == "solid":
                                borders[side] = (fl[1], emu(ln.get("w")) or 0.75)
            # inside borders apply to every internal edge
            if "IH" in borders:
                for side in ("T", "B"):
                    borders.setdefault(side, borders["IH"])
            if "IV" in borders:
                for side in ("L", "R"):
                    borders.setdefault(side, borders["IV"])
        if tcpr is not None:
            pf = parse_fill(tcpr, ctx.dc)
            if pf and pf[0] == "solid":
                fill = pf[1]
            elif pf and pf[0] == "none":
                fill = None
            for tag, side in (("lnL", "L"), ("lnR", "R"), ("lnT", "T"), ("lnB", "B")):
                ln = tcpr.find("a:%s" % tag, NS)
                if ln is None:
                    continue
                if ln.find("a:noFill", NS) is not None:
                    borders[side] = None
                else:
                    fl = parse_fill(ln, ctx.dc)
                    if fl and fl[0] == "solid":
                        borders[side] = (fl[1], emu(ln.get("w")) or 0.75)
        return fill, borders

    # ================================================================ text
    def _draw_text(self, sheet, tx, box, ctx: "SlideCtx", ph, inherited, rot, is_text_box, style, geom_name, font_ref=None):
        x, y, w, h = box
        body = tx.find("a:bodyPr", NS)
        inh_body = inherited[0].find(f"{pq('txBody')}/a:bodyPr", NS) if inherited is not None and inherited[0] is not None else None

        def attr(name, default):
            for b in (body, inh_body):
                if b is not None and b.get(name) is not None:
                    return b.get(name)
            return default

        l = emu(attr("lIns", "91440"))
        t = emu(attr("tIns", "45720"))
        r = emu(attr("rIns", "91440"))
        bt = emu(attr("bIns", "45720"))
        anchor = attr("anchor", "t")
        wrap = attr("wrap", "square")
        vert = attr("vert", "horz")
        scale = 1.0
        reduce_ln = 0.0
        fit = None
        for b in (body, inh_body):
            if b is not None and fit is None:
                fit = b.find("a:normAutofit", NS)
                if fit is None and b.find("a:spAutoFit", NS) is not None:
                    fit = "sp"
        if fit is not None and fit != "sp":
            if fit.get("fontScale"):
                scale = int(fit.get("fontScale")) / 100000.0
            if fit.get("lnSpcReduction"):
                reduce_ln = int(fit.get("lnSpcReduction")) / 100000.0
        inner_w = max(w - l - r, 6.0)
        inner_h = max(h - t - bt, 6.0)
        extra = {}
        if font_ref is not None and len(font_ref):
            fr = font_ref.find("a:fontRef", NS)
            if fr is not None and len(fr):
                c = color_from_drawingml(fr[0], ctx.theme, ctx.scheme_map)
                if c is not None:
                    extra["color"] = c
        elif style is not None:
            fr = style.find("a:fontRef", NS)
            if fr is not None and len(fr):
                c = color_from_drawingml(fr[0], ctx.theme, ctx.scheme_map)
                if c is not None:
                    extra["color"] = c
        frame = self._layout_frame(tx, inner_w, ctx, ph, inherited, scale=scale, reduce_ln=reduce_ln, extra_style=extra,
                                   is_text_box=is_text_box, nowrap=(wrap == "none"))
        if frame is None:
            return
        if fit is not None and fit != "sp" and frame["height"] > inner_h + 0.5 and not (fit.get("fontScale")):
            s = 0.95
            while s > 0.45:
                f2 = self._layout_frame(tx, inner_w, ctx, ph, inherited, scale=s, reduce_ln=0.1 if s < 0.8 else 0.0,
                                        extra_style=extra, is_text_box=is_text_box, nowrap=(wrap == "none"))
                frame = f2 or frame
                if f2 is None or f2["height"] <= inner_h + 0.5:
                    break
                s -= 0.05
        free = inner_h - frame["height"]
        off = 0.0
        if anchor == "ctr":
            off = free / 2
        elif anchor == "b":
            off = free
        ox = x + l
        self._emit_frame(sheet, frame, ox, y + t + off, inner_w, rot=rot, pivot=(x + w / 2, y + h / 2))

    def _para_stack(self, ctx, ph, inherited, tx, is_text_box):
        """The chain of {level: ppr} styles under a shape's own text, lowest first."""
        stack = []
        if ctx.dts:
            stack.append(ctx.dts)
        kind = None
        if ph is not None:
            kind = ph.get("type") or ("body" if ph.get("idx") else "body")
        if ph is None:
            stack.append(ctx.other_style)
        elif kind in ("title", "ctrTitle"):
            stack.append(ctx.title_style)
        elif kind in ("dt", "ftr", "sldNum"):
            stack.append(ctx.other_style)
        else:
            stack.append(ctx.body_style)
        if ph is not None:
            for holder in ctx.ph_chain(ph):
                ls = holder[0].find(f"{pq('txBody')}/a:lstStyle", NS) if holder[0] is not None else None
                if ls is not None:
                    stack.append(parse_lststyle(ls, holder[1].dc, ctx.theme))
        own = tx.find("a:lstStyle", NS)
        if own is not None:
            stack.append(parse_lststyle(own, ctx.dc, ctx.theme))
        return stack

    def _layout_frame(self, tx, width, ctx: "SlideCtx", ph, inherited, scale=1.0, reduce_ln=0.0, extra_style=None,
                      is_text_box=False, nowrap=False):
        stack = self._para_stack(ctx, ph, inherited, tx, is_text_box)
        counters: dict[int, int] = {}
        paras = []
        total = 0.0
        extra_style = extra_style or {}
        first = True
        for p in tx.findall("a:p", NS):
            ppr_el = p.find("a:pPr", NS)
            own = parse_ppr(ppr_el, ctx.dc, ctx.theme) if ppr_el is not None else {}
            lvl = own.get("lvl", 0)
            merged: dict = {}
            for st in stack:
                if "def" in st:
                    merged = merge_ppr(merged, st["def"])
                if lvl in st:
                    merged = merge_ppr(merged, st[lvl])
            merged = merge_ppr(merged, own)
            drpr = dict(merged.get("defRPr", {}))
            drpr.update({k: v for k, v in extra_style.items() if k not in drpr or k == "color" and "color" not in merged.get("defRPr", {})})
            for k, v in extra_style.items():
                if k in ("b", "i") and True:
                    drpr[k] = drpr.get(k, v) if False else v
            # items
            items = []
            first_style = None
            end_rpr = parse_drpr(p.find("a:endParaRPr", NS), ctx.dc, ctx.theme)
            for child in p:
                tag = local(child.tag)
                if tag in ("r", "fld"):
                    rpr = {**drpr, **parse_drpr(child.find("a:rPr", NS), ctx.dc, ctx.theme)}
                    t_el = child.find("a:t", NS)
                    text = (t_el.text or "") if t_el is not None else ""
                    if tag == "fld":
                        ftype = child.get("type") or ""
                        if ftype == "slidenum":
                            text = str(self.slide_no)
                    if not text:
                        continue
                    link = None
                    if rpr.get("link_rid") and ctx.rels.get(rpr["link_rid"]):
                        target = ctx.rels[rpr["link_rid"]]
                        link = ("uri:" + target[0]) if target[2] == "External" else None
                    st = self._style(rpr, text, ctx, scale, link)
                    first_style = first_style or st
                    items.append(("text", text, st))
                elif tag == "br":
                    rpr = {**drpr, **parse_drpr(child.find("a:rPr", NS), ctx.dc, ctx.theme)}
                    st = self._style(rpr, "", ctx, scale, None)
                    items.append(("br", None, st))
            mark_style = self._style({**drpr, **end_rpr}, "", ctx, scale, None)
            if items:
                mark_style = items[-1][2]       # the end of a paragraph with text takes its size from the text
            if not items and first_style is None:
                first_style = mark_style
            # bullet
            marker = None
            bu = merged.get("bu", ("none",))
            if bu[0] != "none" and items:
                bst = first_style or mark_style
                size_mult = merged.get("buSzPct", 1.0)
                bcolor = merged.get("buClr", bst.color)
                bstyle = Style(**{**bst.__dict__, "size": bst.size * size_mult, "color": bcolor, "bold": False, "italic": False,
                                  "underline": None, "strike": False, "highlight": None, "link": None})
                if bu[0] == "char":
                    marker = bullet_char(bu[1], merged.get("buFont"))
                    counters.clear() if False else None
                elif bu[0] == "auto":
                    counters[lvl] = counters.get(lvl, bu[2] - 1) + 1
                    for deeper in [k for k in counters if k > lvl]:
                        del counters[deeper]
                    marker = _auto_number(counters[lvl], bu[1])
                if bu[0] != "auto":
                    for deeper in [k for k in counters if k >= lvl]:
                        del counters[deeper]
                if marker:
                    marker = (marker, bstyle)
            else:
                for deeper in [k for k in counters if k >= lvl]:
                    del counters[deeper]
            mar_l = merged.get("marL", 0.0)
            indent = merged.get("indent", 0.0)
            atoms = []
            if marker:
                text, bstyle = marker
                atoms.append(Atom(text, bstyle, T.text_width(text, bstyle), "text"))
                atoms.append(Atom("", bstyle, 0.0, "tab"))
            atoms += T.atomize(items)
            atoms.append(Atom("", mark_style, 0.0, "mark", brk=True))
            tabs = [TabStop(mar_l, "left")] if indent < 0 or marker else []
            for pos, algn in merged.get("tabs", []):
                tabs.append(TabStop(pos, {"l": "left", "r": "right", "ctr": "center", "dec": "decimal"}.get(algn, "left")))
            algn = merged.get("algn", "l")
            align = {"l": "left", "r": "right", "ctr": "center", "just": "both", "justLow": "both", "dist": "distribute"}.get(algn, "left")
            off0 = max(mar_l + indent, 0.0)
            avail_w = 1e6 if nowrap else width

            def width_for(i, _lines, off0=off0, mar_l=mar_l, merged=merged):
                off = off0 if i == 0 else mar_l
                return off, max(avail_w - merged.get("marR", 0.0) - off, 6.0)

            lines = T.break_lines(atoms, width_for, tabs, 72.0)
            ln = merged.get("lnSpc") or ("pct", 1.0)
            rule = ("auto", max(0.2, ln[1] - reduce_ln)) if ln[0] == "pct" else ("exact", ln[1] * scale)
            heights, bases, infos = [], [], []
            for line in lines:
                if nowrap:
                    T.place_line(line, align, max(width - off0, 6.0), clamp=False)
                else:
                    T.place_line(line, align, line.avail)
                hh, bb = T.line_metrics(line, rule, 0.0, "ppt")
                heights.append(hh)
                bases.append(bb)
                infos.append({"width": line.width})
            base_size = (first_style or mark_style).size
            sb = merged.get("spcBef")
            sa = merged.get("spcAft")

            def spacing(sp):
                if not sp:
                    return 0.0
                return sp[1] * base_size * 1.2 if sp[0] == "pct" else sp[1] * scale

            before = 0.0 if first else spacing(sb)
            after = spacing(sa)
            paras.append({"lines": lines, "heights": heights, "bases": bases, "before": before, "after": after,
                          "lines_info": infos, "algn": algn})
            total += before + sum(heights) + after
            first = False
        return {"paras": paras, "height": total, "width": width}

    def _style(self, rpr: dict, text: str, ctx: "SlideCtx", scale: float, link):
        cjk = any(T.is_cjk(c) for c in text)
        theme = ctx.theme
        ea = rpr.get("ea") or theme.fonts["minor"].get("ea") or ""
        latin = rpr.get("latin") or theme.fonts["minor"].get("latin") or ""
        family = (ea or latin) if cjk else (latin or ea)
        size = rpr.get("sz", 18.0) * scale
        color = rpr.get("color", (0.0, 0.0, 0.0))
        underline = rpr.get("u")
        return Style(family=family or "Yu Gothic", size=size, bold=bool(rpr.get("b")), italic=bool(rpr.get("i")),
                     color=color, underline=underline if underline not in (None, "none") else None,
                     strike=bool(rpr.get("strike")), highlight=rpr.get("hl"), vert=rpr.get("vert", 0),
                     spacing=rpr.get("spc", 0.0), caps=bool(rpr.get("caps")), link=link)

    def _emit_frame(self, sheet, frame, x, y, width=None, rot=0.0, pivot=None):
        for para in frame["paras"]:
            y += para["before"]
            for line, h, b in zip(para["lines"], para["heights"], para["bases"]):
                if rot and pivot:
                    _emit_rotated(sheet, line, x + line.indent, y + b, y, h, rot, pivot)
                else:
                    T.emit_line(sheet, line, x + line.indent, y + b, y, h)
                y += h
            y += para["after"]


# ============================================================================ helpers


class SlideCtx:
    def __init__(self, conv: PptxToPdf, theme: Theme, scheme_map: dict, rels: dict, part: str):
        self.conv = conv
        self.theme = theme
        self.scheme_map = scheme_map
        self.rels = rels
        self.part = part
        self.dc = Ctx(theme, scheme_map, image_for=lambda rid: conv.image(rels, rid))
        self.dts = {}
        self.title_style = {}
        self.body_style = {}
        self.other_style = {}
        self.layout_ph: list = []
        self.master_ph: list = []
        self.layout_ctx: "SlideCtx | None" = None
        self.master_ctx: "SlideCtx | None" = None

    @classmethod
    def derive(cls, base: "SlideCtx", rels: dict, part: str) -> "SlideCtx":
        new = cls(base.conv, base.theme, base.scheme_map, rels, part)
        new.dts, new.title_style, new.body_style, new.other_style = base.dts, base.title_style, base.body_style, base.other_style
        new.layout_ph, new.master_ph = base.layout_ph, base.master_ph
        return new

    def find_ph(self, ph):
        """The layout's (then the master's) placeholder matching ``ph``: (element, ctx)."""
        for holder, which in ((self.layout_ph, "layout"), (self.master_ph, "master")):
            hit = _match_ph(holder, ph)
            if hit is not None:
                return hit, self
        return None

    def ph_chain(self, ph):
        """Placeholders that pass text style down to ``ph``: master first, then layout."""
        out = []
        m = _match_ph(self.master_ph, ph)
        if m is not None:
            out.append((m, self))
        l = _match_ph(self.layout_ph, ph)
        if l is not None:
            out.append((l, self))
        return out


def _placeholders(root):
    out = []
    if root is None:
        return out
    tree = root.find(pq("cSld")).find(pq("spTree"))
    for sp in tree.iter():
        if local(sp.tag) in ("sp", "pic"):
            nv = sp.find(f"{pq('nvSpPr')}/{pq('nvPr')}/{pq('ph')}")
            if nv is None:
                nv = sp.find(f"{pq('nvPicPr')}/{pq('nvPr')}/{pq('ph')}")
            if nv is not None:
                out.append((sp, nv))
    return out


def _match_ph(holder, ph):
    idx = ph.get("idx")
    kind = ph.get("type")
    if idx is not None:
        for sp, p in holder:
            if p.get("idx") == idx and (kind is None or p.get("type") in (kind, None) or True):
                return sp
    kinds = {kind} if kind else {"body"}
    if kind == "ctrTitle":
        kinds = {"ctrTitle", "title"}
    if kind in ("subTitle",):
        kinds = {"subTitle", "body"}
    for sp, p in holder:
        if (p.get("type") or "body") in kinds:
            return sp
    return None


def _with_children(sp_pr, extra):
    new = ET.Element(sp_pr.tag, sp_pr.attrib)
    for c in sp_pr:
        new.append(c)
    for c in extra:
        new.append(c)
    return new


def _raster(raw: bytes) -> bool:
    head = raw[:16]
    return (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head.startswith(b"GIF8") or head.startswith(b"BM")
            or head[:4] in (b"II*\x00", b"MM\x00*") or raw[:200].lstrip().startswith((b"<?xml", b"<svg"))
            or (head.startswith(b"RIFF") and raw[8:12] == b"WEBP"))


def _rotate_image(data: bytes, rot: float):
    try:
        import pymupdf
        pix = pymupdf.Pixmap(data)
        return None if pix is None else None
    except Exception:  # noqa: BLE001
        return None


def _auto_number(n: int, kind: str) -> str:
    table = {
        "arabicPeriod": ("decimal", "{}."), "arabicParenR": ("decimal", "{})"), "arabicParenBoth": ("decimal", "({})"),
        "arabicPlain": ("decimal", "{}"), "romanLcPeriod": ("lowerRoman", "{}."), "romanUcPeriod": ("upperRoman", "{}."),
        "alphaLcPeriod": ("lowerLetter", "{}."), "alphaUcPeriod": ("upperLetter", "{}."), "alphaLcParenR": ("lowerLetter", "{})"),
        "alphaUcParenR": ("upperLetter", "{})"), "alphaLcParenBoth": ("lowerLetter", "({})"),
        "circleNumDbPlain": ("decimalEnclosedCircle", "{}"), "circleNumWdBlackPlain": ("decimalEnclosedCircle", "{}"),
        "ea1JpnChsDbPeriod": ("japaneseCounting", "{}."), "ea1JpnKorPeriod": ("japaneseCounting", "{}."),
        "arabicDbPeriod": ("decimalFullWidth", "{}."), "arabicDbPlain": ("decimalFullWidth", "{}"),
    }
    fmt, pattern = table.get(kind, ("decimal", "{}."))
    return pattern.format(format_number(n, fmt))


def _emit_rotated(sheet, line, x, baseline, top, height, rot, pivot):
    """Rotated text is rare; draw it unrotated at its place rather than lose it."""
    T.emit_line(sheet, line, x, baseline, top, height)


def convert(data: bytes, name: str = "") -> bytes:
    return PptxToPdf(data).convert()
