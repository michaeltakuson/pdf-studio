"""Word (.docx) to PDF.

Reads the document's own formatting — styles, numbering, tables, sections,
headers and footers, pictures — and lays it out with the small typesetter in
``typeset.py``. Everything is drawn as real text in embedded fonts, so the
result can be searched, selected and edited like any other PDF.

What is not reproduced exactly: the original fonts (the bundled BIZ UD faces
stand in, serif or sans by the original's kind), and rarely used features such
as text wrapping around pictures in tight shapes, equations, charts and
SmartArt.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import pymupdf

from . import typeset as T
from .drawingml import Ctx, draw_shape
from .ooxml import (NS, Package, PackageError, Theme, WORD_HIGHLIGHTS, attr, bullet_char, emu, format_number, local, on,
                    twips)
from .typeset import Atom, Line, Sheet, Style, TabStop, hex_color

W = NS["w"]


def wq(tag: str) -> str:
    return "{%s}%s" % (W, tag)


def wattr(el, name, default=None):
    return el.get(wq(name), default) if el is not None else default


# ============================================================================ properties


def parse_border(el):
    """(style, width pt, colour, space pt) or None when there is no line."""
    if el is None:
        return None
    style = wattr(el, "val", "single")
    if style in ("nil", "none"):
        return None
    sz = wattr(el, "sz")
    width = (float(sz) / 8.0) if sz else 0.5
    color = hex_color(wattr(el, "color"), (0, 0, 0)) or (0, 0, 0)
    space = float(wattr(el, "space", "0") or 0)
    return (style, max(0.25, width), color, space)


def _theme_color(el, theme: Theme):
    name = wattr(el, "themeColor")
    if name:
        return theme.color(name, wattr(el, "themeTint"), wattr(el, "themeShade"))
    return None


def parse_rpr(el, theme: Theme) -> dict:
    out: dict = {}
    if el is None:
        return out
    for c in el:
        t = local(c.tag)
        v = wattr(c, "val")
        if t == "rFonts":
            for key in ("ascii", "hAnsi", "eastAsia", "cs"):
                name = wattr(c, key)
                if name:
                    out["f_" + key] = name
            for key, slot in (("asciiTheme", "latin"), ("hAnsiTheme", "latin"), ("eastAsiaTheme", "ea"), ("cstheme", "cs")):
                ref = wattr(c, key)
                if ref:
                    group = "major" if ref.startswith("major") else "minor"
                    name = theme.fonts[group].get(slot, "")
                    if name:
                        out["f_" + {"asciiTheme": "ascii", "hAnsiTheme": "hAnsi", "eastAsiaTheme": "eastAsia", "cstheme": "cs"}[key]] = name
        elif t == "sz" and v:
            out["sz"] = float(v) / 2.0
        elif t in ("b", "i", "strike", "dstrike", "caps", "smallCaps", "vanish"):
            out[t] = on(c)
        elif t == "u":
            out["u"] = None if v in ("none",) else (v or "single")
        elif t == "color":
            color = _theme_color(c, theme) or hex_color(v)
            if color is not None:
                out["color"] = color
            elif v == "auto":
                out["color"] = None
        elif t == "highlight" and v:
            out["hl"] = WORD_HIGHLIGHTS.get(v)
        elif t == "shd":
            fill = wattr(c, "fill")
            if fill and fill != "auto":
                out["hl"] = hex_color(fill)
        elif t == "vertAlign":
            out["vert"] = {"superscript": 1, "subscript": -1}.get(v, 0)
        elif t == "spacing" and v:
            out["spacing"] = twips(v)
        elif t == "rStyle" and v:
            out["rstyle"] = v
    return out


def parse_ppr(el, theme: Theme) -> dict:
    out: dict = {}
    if el is None:
        return out
    for c in el:
        t = local(c.tag)
        v = wattr(c, "val")
        if t == "jc":
            out["jc"] = v
        elif t == "ind":
            for src, dst in (("left", "left"), ("start", "left"), ("right", "right"), ("end", "right"),
                             ("firstLine", "first"), ("hanging", "hanging")):
                if wattr(c, src) is not None:
                    out[dst] = twips(wattr(c, src))
            for src, dst in (("leftChars", "left_chars"), ("startChars", "left_chars"), ("rightChars", "right_chars"),
                             ("firstLineChars", "first_chars"), ("hangingChars", "hanging_chars")):
                if wattr(c, src) is not None:
                    out[dst] = float(wattr(c, src)) / 100.0
        elif t == "spacing":
            if wattr(c, "before") is not None:
                out["before"] = twips(wattr(c, "before"))
            if wattr(c, "after") is not None:
                out["after"] = twips(wattr(c, "after"))
            if wattr(c, "beforeLines") is not None:
                out["before_lines"] = float(wattr(c, "beforeLines")) / 100.0
            if wattr(c, "afterLines") is not None:
                out["after_lines"] = float(wattr(c, "afterLines")) / 100.0
            if wattr(c, "beforeAutospacing") == "1":
                out["before"] = 14.0
            if wattr(c, "afterAutospacing") == "1":
                out["after"] = 14.0
            if wattr(c, "line") is not None:
                rule = wattr(c, "lineRule", "auto")
                val = float(wattr(c, "line"))
                out["line"] = ("auto", val / 240.0) if rule == "auto" else (rule, val / 20.0)
        elif t in ("keepNext", "keepLines", "pageBreakBefore", "widowControl", "contextualSpacing", "snapToGrid", "wordWrap"):
            out[t] = on(c)
        elif t == "numPr":
            nid = c.find(wq("numId"))
            lvl = c.find(wq("ilvl"))
            if nid is not None:
                out["numId"] = wattr(nid, "val")
            if lvl is not None:
                out["ilvl"] = int(wattr(lvl, "val", "0") or 0)
        elif t == "shd":
            fill = wattr(c, "fill")
            if fill and fill != "auto":
                out["shd"] = hex_color(fill)
            elif wattr(c, "themeFill"):
                out["shd"] = theme.color(wattr(c, "themeFill"), wattr(c, "themeFillTint"), wattr(c, "themeFillShade"))
        elif t == "pBdr":
            out["pbdr"] = {local(b.tag): parse_border(b) for b in c}
        elif t == "tabs":
            tabs = []
            for tab in c:
                pos = twips(wattr(tab, "pos", "0"))
                tabs.append((wattr(tab, "val", "left"), pos, wattr(tab, "leader")))
            out["tabs"] = tabs
        elif t == "outlineLvl" and v is not None:
            out["outline"] = int(v)
        elif t == "pStyle":
            out["pstyle"] = v
        elif t == "rPr":
            out["mark_rpr"] = parse_rpr(c, theme)
        elif t == "sectPr":
            out["sectPr"] = c
    return out


# ============================================================================ styles


class Styles:
    def __init__(self, root, theme: Theme):
        self.theme = theme
        self.items: dict[str, dict] = {}
        self.default_para = None
        self.default_char = None
        self.default_table = None
        self.defaults_rpr: dict = {}
        self.defaults_ppr: dict = {}
        self._cache: dict = {}
        if root is None:
            return
        dd = root.find(wq("docDefaults"))
        if dd is not None:
            self.defaults_rpr = parse_rpr(dd.find(f"{wq('rPrDefault')}/{wq('rPr')}"), theme)
            self.defaults_ppr = parse_ppr(dd.find(f"{wq('pPrDefault')}/{wq('pPr')}"), theme)
        for st in root.findall(wq("style")):
            sid = wattr(st, "styleId")
            if not sid:
                continue
            item = {
                "type": wattr(st, "type"), "based": wattr(st.find(wq("basedOn")), "val"),
                "default": wattr(st, "default") in ("1", "true"),
                "ppr": parse_ppr(st.find(wq("pPr")), theme), "rpr": parse_rpr(st.find(wq("rPr")), theme),
                "tblpr": st.find(wq("tblPr")), "tcpr": st.find(wq("tcPr")), "trpr": st.find(wq("trPr")),
                "conds": {wattr(x, "type"): x for x in st.findall(wq("tblStylePr"))},
                "name": wattr(st.find(wq("name")), "val", ""),
            }
            self.items[sid] = item
            if item["default"]:
                if item["type"] == "paragraph":
                    self.default_para = sid
                elif item["type"] == "character":
                    self.default_char = sid
                elif item["type"] == "table":
                    self.default_table = sid

    def chain(self, sid: str | None) -> list[dict]:
        out = []
        seen = set()
        while sid and sid in self.items and sid not in seen:
            seen.add(sid)
            out.append(self.items[sid])
            sid = self.items[sid]["based"]
        out.reverse()
        return out

    def para_props(self, sid: str | None) -> tuple[dict, dict]:
        key = ("p", sid or self.default_para)
        if key in self._cache:
            return self._cache[key]
        ppr = dict(self.defaults_ppr)
        rpr = dict(self.defaults_rpr)
        for item in self.chain(sid or self.default_para):
            ppr.update({k: v for k, v in item["ppr"].items() if k != "pstyle"})
            rpr.update(item["rpr"])
        self._cache[key] = (ppr, rpr)
        return ppr, rpr

    def char_props(self, sid: str | None) -> dict:
        out: dict = {}
        for item in self.chain(sid):
            out.update(item["rpr"])
        return out

    def outline_level(self, sid):
        return self.para_props(sid)[0].get("outline")


# ============================================================================ numbering


class Numbering:
    def __init__(self, root, theme: Theme):
        self.abstract: dict[str, dict] = {}
        self.nums: dict[str, dict] = {}
        self.counters: dict[str, list[int]] = {}
        self.seen: set[str] = set()
        self.theme = theme
        if root is None:
            return
        for an in root.findall(wq("abstractNum")):
            aid = wattr(an, "abstractNumId")
            levels = {}
            for lvl in an.findall(wq("lvl")):
                ilvl = int(wattr(lvl, "ilvl", "0"))
                levels[ilvl] = {
                    "fmt": wattr(lvl.find(wq("numFmt")), "val", "decimal"),
                    "text": wattr(lvl.find(wq("lvlText")), "val", ""),
                    "start": int(wattr(lvl.find(wq("start")), "val", "1") or 1),
                    "suff": wattr(lvl.find(wq("suff")), "val", "tab"),
                    "jc": wattr(lvl.find(wq("lvlJc")), "val", "left"),
                    "ppr": parse_ppr(lvl.find(wq("pPr")), theme),
                    "rpr": parse_rpr(lvl.find(wq("rPr")), theme),
                    "legal": lvl.find(wq("isLgl")) is not None,
                    "restart": wattr(lvl.find(wq("lvlRestart")), "val"),
                    "pstyle": wattr(lvl.find(wq("pStyle")), "val"),
                }
            self.abstract[aid] = levels
        for num in root.findall(wq("num")):
            nid = wattr(num, "numId")
            aid = wattr(num.find(wq("abstractNumId")), "val")
            overrides = {}
            for ov in num.findall(wq("lvlOverride")):
                ilvl = int(wattr(ov, "ilvl", "0"))
                start = ov.find(wq("startOverride"))
                lvl = ov.find(wq("lvl"))
                overrides[ilvl] = {"start": int(wattr(start, "val")) if start is not None else None, "lvl": lvl}
            self.nums[nid] = {"abstract": aid, "overrides": overrides}

    def level(self, num_id: str, ilvl: int) -> dict | None:
        num = self.nums.get(num_id)
        if not num:
            return None
        levels = self.abstract.get(num["abstract"], {})
        lvl = levels.get(ilvl)
        return lvl

    def marker(self, num_id: str, ilvl: int):
        """Advance the counters for a paragraph and return (level info, marker text)."""
        num = self.nums.get(num_id)
        if not num:
            return None, ""
        levels = self.abstract.get(num["abstract"], {})
        lvl = levels.get(ilvl)
        if lvl is None:
            return None, ""
        key = num["abstract"]
        counters = self.counters.setdefault(key, [0] * 9)
        if num_id not in self.seen:
            self.seen.add(num_id)
            for lv, ov in num["overrides"].items():
                if ov["start"] is not None and lv < 9:
                    counters[lv] = ov["start"] - 1
        start = lvl["start"]
        ov = num["overrides"].get(ilvl)
        if counters[ilvl] == 0:
            counters[ilvl] = (ov["start"] if ov and ov["start"] is not None else start)
        else:
            counters[ilvl] += 1
        for deeper in range(ilvl + 1, 9):
            dl = levels.get(deeper)
            counters[deeper] = (dl["start"] - 1) if dl else 0
        fmt = lvl["fmt"]
        if fmt == "bullet":
            return lvl, bullet_char(lvl["text"], lvl["rpr"].get("f_ascii"))
        text = lvl["text"]
        out = ""
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "%" and i + 1 < len(text) and text[i + 1].isdigit():
                n = int(text[i + 1]) - 1
                ref = levels.get(n)
                value = counters[n] if counters[n] else (ref["start"] if ref else 1)
                f = "decimal" if lvl["legal"] and n != ilvl else (ref["fmt"] if ref else "decimal")
                if lvl["legal"]:
                    f = "decimal"
                out += format_number(value, f)
                i += 2
            else:
                out += ch
                i += 1
        return lvl, out


# ============================================================================ layout objects


@dataclass
class ParaSrc:
    items: list
    ppr: dict
    mark_style: Style
    marker: tuple | None = None          # (text, style, suff)
    anchors: list = field(default_factory=list)
    bookmarks: list = field(default_factory=list)
    footnotes: list = field(default_factory=list)
    style_id: str | None = None
    base_rpr: dict = field(default_factory=dict)
    outline: int | None = None
    hr: tuple | None = None


@dataclass
class ParaBox:
    lines: list
    heights: list
    bases: list
    before: float
    after: float
    left: float
    right: float
    width: float
    align: str
    keep_next: bool = False
    keep_lines: bool = False
    page_break_before: bool = False
    shd: tuple | None = None
    pbdr: dict | None = None
    anchors: list = field(default_factory=list)
    bookmarks: list = field(default_factory=list)
    footnotes: list = field(default_factory=list)
    contextual: bool = False
    style_id: str | None = None
    widow: bool = True
    outline: int | None = None
    page_breaks: set = field(default_factory=set)   # line indexes after which a page break forces
    hr: tuple | None = None

    def height(self) -> float:
        return sum(self.heights)


@dataclass
class CellBox:
    col: int
    span: int
    x: float
    width: float
    blocks: list
    margins: tuple
    shd: tuple | None
    borders: dict
    valign: str
    vmerge: str | None
    content_h: float = 0.0
    rowspan: int = 1
    skip: bool = False


@dataclass
class RowBox:
    cells: list
    height: float
    header: bool = False
    cant_split: bool = False
    min_h: float = 0.0
    exact: bool = False


@dataclass
class TableBox:
    rows: list
    width: float
    x: float
    colx: list
    borders: dict
    jc: str = "left"

    def height(self) -> float:
        return sum(r.height for r in self.rows)


# ============================================================================ the converter


class DocxToPdf:
    def __init__(self, data: bytes):
        self.pkg = Package(data)
        if not self.pkg.has("word/document.xml"):
            raise PackageError("Word 文書として読み込めませんでした（.docx 形式のみ対応しています。古い .doc は Word で .docx として保存し直してください）")
        self.main = "word/document.xml"
        self.theme = Theme(self._first_related("theme"))
        self.styles = Styles(self.pkg.xml(self._part("styles")), self.theme)
        self.numbering = Numbering(self.pkg.xml(self._part("numbering")), self.theme)
        self.settings = self.pkg.xml(self._part("settings"))
        self.default_tab = 36.0
        self.even_odd = False
        if self.settings is not None:
            dts = self.settings.find(wq("defaultTabStop"))
            if dts is not None:
                self.default_tab = twips(wattr(dts, "val", "720")) or 36.0
            self.even_odd = self.settings.find(wq("evenAndOddHeaders")) is not None
        self.doc = self.pkg.xml(self.main)
        self.body = self.doc.find(wq("body"))
        self.rels = self.pkg.rels(self.main)
        self.grid = 0.0
        self.fields: list[dict] = []
        self.anchors: dict[str, tuple] = {}
        self.footnote_parts = self._load_notes("footnotes", "footnote")
        self.style_cache: dict = {}
        self.sheets: list[Sheet] = []
        self.images: dict[str, bytes | None] = {}
        self.page_bg = None
        bg = self.doc.find(wq("background"))
        if bg is not None:
            self.page_bg = hex_color(wattr(bg, "color"))
        self.warnings: set[str] = set()
        self.flow = None

    # ---- package helpers
    def _part(self, kind: str) -> str:
        for target, k, _mode in self.rels_for_main():
            if k == kind:
                return target
        return {"styles": "word/styles.xml", "numbering": "word/numbering.xml", "settings": "word/settings.xml"}.get(kind, "")

    def rels_for_main(self):
        return list(self.pkg.rels(self.main).values())

    def _first_related(self, kind: str):
        for target, k, _mode in self.rels_for_main():
            if k == kind:
                return self.pkg.xml(target)
        if kind == "theme":
            return self.pkg.xml("word/theme/theme1.xml")
        return None

    def _load_notes(self, kind: str, tag: str) -> dict:
        part = self._part(kind)
        root = self.pkg.xml(part) if part else None
        out = {}
        if root is not None:
            for note in root.findall(wq(tag)):
                nid = wattr(note, "id")
                if nid is not None and wattr(note, "type") not in ("separator", "continuationSeparator", "continuationNotice"):
                    out[nid] = note
        return out

    # ---- styles of runs
    def style_for(self, rpr: dict, text: str = "", link: str | None = None) -> Style:
        cjk = any(T.is_cjk(c) for c in text)
        key = (tuple(sorted((k, str(v)) for k, v in rpr.items())), cjk, link)
        hit = self.style_cache.get(key)
        if hit is not None:
            return hit
        ea = rpr.get("f_eastAsia")
        latin = rpr.get("f_ascii") or rpr.get("f_hAnsi")
        family = (ea or latin or "") if cjk else (latin or ea or "")
        if not family:
            family = self.theme.fonts["minor"].get("ea") or self.theme.fonts["minor"].get("latin") or "Yu Gothic"
        size = rpr.get("sz", 10.5)
        underline = rpr.get("u")
        if rpr.get("dstrike"):
            strike = True
        else:
            strike = bool(rpr.get("strike"))
        color = rpr.get("color")
        if color is None:
            color = (0.0, 0.0, 0.0)
        style = Style(family=family, size=size, bold=bool(rpr.get("b")), italic=bool(rpr.get("i")), color=color,
                      underline=underline if underline not in (None, "none") else None, strike=strike,
                      highlight=rpr.get("hl"), vert=rpr.get("vert", 0), spacing=rpr.get("spacing", 0.0),
                      caps=bool(rpr.get("caps")), link=link)
        if rpr.get("smallCaps") and not rpr.get("caps"):
            style = Style(**{**style.__dict__, "caps": True, "size": size * 0.8})
        self.style_cache[key] = style
        return style

    # ---- pictures
    def image_bytes(self, part_rels: dict, rid: str) -> bytes | None:
        rel = part_rels.get(rid)
        if not rel:
            return None
        target = rel[0]
        if target in self.images:
            return self.images[target]
        raw = self.pkg.read(target) if rel[2] != "External" else None
        if raw is not None and not _is_raster_or_svg(raw):
            raw = _convert_vector_image(raw)
        self.images[target] = raw
        return raw

    # ============================================================== parsing of content

    def iter_blocks(self, parent):
        for child in parent:
            tag = local(child.tag)
            if tag in ("p", "tbl"):
                yield child
            elif tag in ("sdt", "sdtContent", "customXml", "ins", "moveTo"):
                inner = child.find(wq("sdtContent")) if tag == "sdt" else child
                if inner is not None:
                    yield from self.iter_blocks(inner)
            elif tag == "AlternateContent":
                choice = child.find("{%s}Choice" % NS["mc"])
                if choice is not None:
                    yield from self.iter_blocks(choice)

    def make_para(self, p, ctx) -> ParaSrc:
        ppr = parse_ppr(p.find(wq("pPr")), self.theme)
        sid = ppr.get("pstyle") or self.styles.default_para
        style_ppr, style_rpr = self.styles.para_props(ppr.get("pstyle"))
        merged = dict(ctx.get("tbl_ppr") or {})
        merged.update({k: v for k, v in style_ppr.items()})
        merged.update({k: v for k, v in ppr.items()})
        base_rpr = dict(self.styles.defaults_rpr)
        base_rpr.update(ctx.get("tbl_rpr") or {})
        base_rpr.update(style_rpr)
        mark_rpr = dict(base_rpr)
        mark_rpr.update(self.styles.char_props(ppr.get("mark_rpr", {}).get("rstyle")))
        mark_rpr.update(ppr.get("mark_rpr", {}))
        src = ParaSrc(items=[], ppr=merged, mark_style=self.style_for(mark_rpr, ""), style_id=sid, base_rpr=base_rpr,
                      outline=merged.get("outline"))
        self.collect_inline(p, base_rpr, src, None)
        # numbering
        num_id = merged.get("numId")
        if num_id and num_id != "0":
            ilvl = merged.get("ilvl", 0)
            lvl, text = self.numbering.marker(num_id, ilvl)
            if lvl is not None:
                rpr = dict(mark_rpr)
                rpr.update(lvl["rpr"])
                if lvl["fmt"] == "bullet":
                    rpr.pop("f_eastAsia", None) if False else None
                style = self.style_for(rpr, text)
                src.marker = (text, style, lvl["suff"], lvl)
                # indentation from the numbering level, unless the paragraph sets its own
                for key in ("left", "hanging", "first", "tabs"):
                    if key in lvl["ppr"] and key not in ppr:
                        src.ppr[key] = lvl["ppr"][key]
                if "left" in lvl["ppr"] and "left" not in ppr and "first" not in ppr and "hanging" not in lvl["ppr"]:
                    src.ppr.pop("hanging", None)
                if "first" in lvl["ppr"] and "first" not in ppr:
                    src.ppr.pop("hanging", None)
                if "hanging" in lvl["ppr"] and "hanging" not in ppr:
                    src.ppr.pop("first", None)
        return src

    def collect_inline(self, parent, base_rpr, src: ParaSrc, link):
        for child in parent:
            tag = local(child.tag)
            if tag == "r":
                self.collect_run(child, base_rpr, src, link)
            elif tag == "hyperlink":
                rid = child.get("{%s}id" % NS["r"])
                anchor = wattr(child, "anchor")
                target = None
                if rid and rid in self.rels:
                    target = "uri:" + self.rels[rid][0]
                elif anchor:
                    target = "anchor:" + anchor
                self.collect_inline(child, base_rpr, src, target or link)
            elif tag in ("ins", "smartTag", "sdt", "sdtContent", "customXml", "moveTo", "fldSimple", "bdo", "dir"):
                if tag == "fldSimple":
                    instr = (wattr(child, "instr") or "").strip().upper()
                    if instr.startswith("PAGE") or instr.startswith("NUMPAGES"):
                        name = "NUMPAGES" if instr.startswith("NUMPAGES") else "PAGE"
                        rpr = dict(base_rpr)
                        run = child.find(wq("r"))
                        if run is not None:
                            rpr.update(self.styles.char_props(parse_rpr(run.find(wq("rPr")), self.theme).get("rstyle")))
                            rpr.update(parse_rpr(run.find(wq("rPr")), self.theme))
                        src.items.append(("field", (name, "00"), self.style_for(rpr, "0", link)))
                        continue
                inner = child.find(wq("sdtContent")) if tag == "sdt" else child
                if inner is not None:
                    self.collect_inline(inner, base_rpr, src, link)
            elif tag == "bookmarkStart":
                name = wattr(child, "name")
                if name:
                    src.bookmarks.append(name)
            elif tag == "AlternateContent":
                choice = child.find("{%s}Choice" % NS["mc"])
                fb = child.find("{%s}Fallback" % NS["mc"])
                pick = choice if choice is not None else fb
                if pick is not None:
                    self.collect_inline(pick, base_rpr, src, link)

    def collect_run(self, r, base_rpr, src: ParaSrc, link):
        rpr = dict(base_rpr)
        direct = parse_rpr(r.find(wq("rPr")), self.theme)
        if direct.get("rstyle"):
            rpr.update(self.styles.char_props(direct["rstyle"]))
        elif self.styles.default_char:
            pass
        rpr.update(direct)
        if rpr.get("vanish"):
            return
        items = src.items
        for c in r:
            t = local(c.tag)
            if t == "fldChar":
                ft = wattr(c, "fldCharType")
                if ft == "begin":
                    self.fields.append({"instr": "", "phase": "instr", "suppress": False, "link": None})
                elif ft == "separate" and self.fields:
                    f = self.fields[-1]
                    f["phase"] = "result"
                    instr = f["instr"].strip()
                    word = instr.split()[0].upper() if instr.split() else ""
                    if word in ("PAGE", "NUMPAGES", "SECTIONPAGES"):
                        name = "NUMPAGES" if word != "PAGE" else "PAGE"
                        items.append(("field", (name, "00"), self.style_for(rpr, "0", link)))
                        f["suppress"] = True
                    elif word == "PAGEREF" and len(instr.split()) > 1:
                        items.append(("field", ("PAGEREF:" + instr.split()[1], "0"), self.style_for(rpr, "0", link)))
                        f["suppress"] = True
                    elif word == "HYPERLINK":
                        parts = instr.split('"')
                        if "\\l" in instr and len(parts) > 1:
                            f["link"] = "anchor:" + parts[1]
                        elif len(parts) > 1:
                            f["link"] = "uri:" + parts[1]
                elif ft == "end" and self.fields:
                    self.fields.pop()
                continue
            if t == "instrText":
                if self.fields and self.fields[-1]["phase"] == "instr":
                    self.fields[-1]["instr"] += c.text or ""
                continue
            if self.fields and (self.fields[-1]["phase"] == "instr" or any(f["suppress"] for f in self.fields)):
                continue
            eff_link = link
            for f in self.fields:
                if f["link"]:
                    eff_link = f["link"]
            if t == "t":
                text = c.text or ""
                if text:
                    items.append(("text", text, self.style_for(rpr, text, eff_link)))
            elif t == "tab" or t == "ptab":
                items.append(("tab", None, self.style_for(rpr, "", eff_link)))
            elif t in ("br", "cr"):
                kind = wattr(c, "type", "textWrapping")
                items.append(("br", "page" if kind == "page" else ("column" if kind == "column" else None), self.style_for(rpr, "", eff_link)))
            elif t == "noBreakHyphen":
                items.append(("text", "-", self.style_for(rpr, "-", eff_link)))
            elif t == "sym":
                code = wattr(c, "char") or ""
                try:
                    ch = chr(int(code, 16))
                except ValueError:
                    ch = ""
                if ch:
                    ch = bullet_char(ch, wattr(c, "font"))
                    items.append(("text", ch, self.style_for(rpr, ch, eff_link)))
            elif t == "footnoteReference":
                nid = wattr(c, "id")
                if nid in self.footnote_parts:
                    src.footnotes.append(nid)
                    vrpr = dict(rpr)
                    vrpr["vert"] = 1
                    mark = str(len(self._footnote_numbers) + 1) if nid not in self._footnote_numbers else str(self._footnote_numbers[nid])
                    self._footnote_numbers.setdefault(nid, int(mark))
                    items.append(("text", mark, self.style_for(vrpr, mark, eff_link)))
            elif t in ("drawing", "pict", "object"):
                self.collect_drawing(c, rpr, src, eff_link)
            elif t == "AlternateContent":
                choice = c.find("{%s}Choice" % NS["mc"])
                fb = c.find("{%s}Fallback" % NS["mc"])
                pick = choice if choice is not None else fb
                if pick is not None:
                    fake = ET.Element(wq("r"))
                    for sub in pick:
                        fake.append(sub)
                    self.collect_run(fake, base_rpr, src, link)

    _footnote_numbers: dict = {}

    # ---- drawings
    def collect_drawing(self, c, rpr, src: ParaSrc, link):
        style = self.style_for(rpr, "", link)
        for node in c.iter():
            tag = local(node.tag)
            if tag in ("inline", "anchor"):
                info = self.read_picture(node)
                if info is None:
                    info = self.read_shapes(node)
                if info is None:
                    continue
                if tag == "inline":
                    payload = info["data"] if info.get("data") is not None else _Drawable(info["ops"])
                    src.items.append(("image", (info["w"], info["h"], payload), style))
                else:
                    src.anchors.append(info)
        # VML pictures and shapes: <w:pict> / <w:object>
        if local(c.tag) in ("pict", "object"):
            self.collect_vml(c, rpr, src, style)

    def anchor_info(self, node, info):
        info["behind"] = node.get("behindDoc") == "1"
        for pos, key in (("positionH", "h"), ("positionV", "v")):
            p = node.find("{%s}%s" % (NS["wp"], pos))
            if p is None:
                continue
            info[key + "_rel"] = p.get("relativeFrom")
            off = p.find("{%s}posOffset" % NS["wp"])
            al = p.find("{%s}align" % NS["wp"])
            info[key + "_off"] = emu(off.text) if off is not None and off.text else 0.0
            info[key + "_align"] = al.text if al is not None else None
        info["wrap"] = "none"
        for wrap in ("wrapSquare", "wrapTight", "wrapThrough", "wrapTopAndBottom", "wrapNone"):
            wn = node.find("{%s}%s" % (NS["wp"], wrap))
            if wn is not None:
                info["wrap"] = wrap
                info["dist"] = tuple(emu(wn.get(k)) for k in ("distL", "distT", "distR", "distB"))
                info["wrap_text"] = wn.get("wrapText", "bothSides")
        if "dist" not in info:
            info["dist"] = tuple(emu(node.get(k)) for k in ("distL", "distT", "distR", "distB"))

    # ---- shapes and text boxes inside a drawing
    def read_shapes(self, node):
        extent = node.find("{%s}extent" % NS["wp"])
        if extent is None:
            return None
        w, h = emu(extent.get("cx")), emu(extent.get("cy"))
        gd = node.find(".//a:graphicData", NS)
        if gd is None or not len(gd):
            return None
        child = gd[0]
        ops = []
        tag = local(child.tag)
        if tag == "wsp":
            ops.append(self.shape_drawable(child, 0.0, 0.0, w, h))
        elif tag in ("wgp", "grpSp"):
            ops.extend(self.group_drawables(child, 0.0, 0.0, 1.0, 1.0, w, h))
        else:
            return None
        ops = [o for o in ops if o is not None]
        if not ops:
            return None
        info = {"w": w, "h": h, "data": None, "ops": ops, "inline": local(node.tag) == "inline"}
        if local(node.tag) == "anchor":
            self.anchor_info(node, info)
        return info

    def group_drawables(self, grp, dx, dy, sx, sy, w, h):
        out = []
        pr = grp.find("wpg:grpSpPr", NS)
        xf = pr.find("a:xfrm", NS) if pr is not None else None
        ch_off = (0.0, 0.0)
        ch_ext = None
        ext = None
        if xf is not None:
            co = xf.find("a:chOff", NS)
            ce = xf.find("a:chExt", NS)
            ex = xf.find("a:ext", NS)
            if co is not None:
                ch_off = (emu(co.get("x")), emu(co.get("y")))
            if ce is not None:
                ch_ext = (emu(ce.get("cx")), emu(ce.get("cy")))
            if ex is not None:
                ext = (emu(ex.get("cx")), emu(ex.get("cy")))
        gx = (ext[0] / ch_ext[0]) if ext and ch_ext and ch_ext[0] else 1.0
        gy = (ext[1] / ch_ext[1]) if ext and ch_ext and ch_ext[1] else 1.0
        for child in grp:
            tag = local(child.tag)
            if tag in ("wsp", "pic"):
                spp = child.find("wps:spPr", NS) if tag == "wsp" else child.find("pic:spPr", NS)
                cxf = spp.find("a:xfrm", NS) if spp is not None else None
                if cxf is None:
                    continue
                off = cxf.find("a:off", NS)
                ex = cxf.find("a:ext", NS)
                if off is None or ex is None:
                    continue
                cx = dx + (emu(off.get("x")) - ch_off[0]) * gx
                cy = dy + (emu(off.get("y")) - ch_off[1]) * gy
                cw = emu(ex.get("cx")) * gx
                chh = emu(ex.get("cy")) * gy
                if tag == "wsp":
                    out.append(self.shape_drawable(child, cx, cy, cw, chh))
                else:
                    blip = child.find(".//a:blip", NS)
                    rid = blip.get("{%s}embed" % NS["r"]) if blip is not None else None
                    data = self.image_bytes(self.rels, rid) if rid else None
                    if data is not None:
                        out.append(lambda sheet, x, y, d=data, cx=cx, cy=cy, cw=cw, ch=chh: sheet.image(x + cx, y + cy, x + cx + cw, y + cy + ch, d))
            elif tag in ("grpSp", "wgp"):
                out.extend(self.group_drawables(child, dx, dy, gx, gy, w, h))
        return out

    def shape_drawable(self, wsp, dx, dy, w, h):
        sp_pr = wsp.find("wps:spPr", NS)
        style = wsp.find("wps:style", NS)
        rot = 0.0
        flip_h = flip_v = False
        xf = sp_pr.find("a:xfrm", NS) if sp_pr is not None else None
        if xf is not None:
            rot = float(xf.get("rot", "0")) / 60000.0
            flip_h = xf.get("flipH") == "1"
            flip_v = xf.get("flipV") == "1"
        txbx = wsp.find("wps:txbx", NS)
        body = wsp.find("wps:bodyPr", NS)
        l = emu(body.get("lIns", "91440")) if body is not None else 7.2
        t = emu(body.get("tIns", "45720")) if body is not None else 3.6
        r = emu(body.get("rIns", "91440")) if body is not None else 7.2
        bt = emu(body.get("bIns", "45720")) if body is not None else 3.6
        anchor = body.get("anchor", "t") if body is not None else "t"
        blocks = None
        content_h = 0.0
        if txbx is not None:
            content = txbx.find("w:txbxContent", NS)
            if content is not None:
                tctx = {}
                font_ref = style.find("a:fontRef", NS) if style is not None else None
                if font_ref is not None and len(font_ref):
                    col = color_from_drawingml_safe(font_ref[0], self)
                    if col is not None:
                        tctx["tbl_rpr"] = {"color": col}
                saved = self.fields
                self.fields = []
                try:
                    blocks = self.layout_blocks(content, max(w - l - r, 12.0), tctx)
                finally:
                    self.fields = saved
                content_h = _blocks_height(blocks)
        ctx = Ctx(self.theme, None, image_for=lambda rid: self.image_bytes(self.rels, rid))
        conv = self

        def draw(sheet, x, y):
            if sp_pr is not None or style is not None:
                draw_shape(sheet, x + dx, y + dy, w, h, sp_pr, ctx, style, rot, flip_h, flip_v, image_op=True)
            if blocks:
                free = h - t - bt - content_h
                voff = free / 2 if anchor == "ctr" else (free if anchor == "b" else 0.0)
                conv.flow.emit_static_blocks(sheet, blocks, x + dx + l, y + dy + t + max(voff, 0.0))
        return draw

    # ---- text frames (<w:framePr>): a paragraph that sits at a fixed place on the page
    @staticmethod
    def is_frame(el) -> bool:
        if local(el.tag) != "p":
            return False
        fr = el.find(f"{wq('pPr')}/{wq('framePr')}")
        if fr is None or wattr(fr, "dropCap"):
            return False
        return (wattr(fr, "x") is not None or wattr(fr, "xAlign")) and (wattr(fr, "y") is not None or wattr(fr, "yAlign"))

    def frame_anchor(self, p) -> dict:
        """A framed paragraph as a floating box, positioned the way a drawing is."""
        import copy

        fr = p.find(f"{wq('pPr')}/{wq('framePr')}")
        col_w = self.flow.cols[self.flow.col][1] if getattr(self, "flow", None) else 400.0
        w = twips(wattr(fr, "w")) if wattr(fr, "w") else col_w
        h_attr = twips(wattr(fr, "h")) if wattr(fr, "h") else 0.0
        inner = copy.deepcopy(p)
        ppr = inner.find(wq("pPr"))
        if ppr is not None:
            for node in ppr.findall(wq("framePr")):
                ppr.remove(node)
        holder = ET.Element(wq("txbxContent"))
        holder.append(inner)
        saved = self.fields
        self.fields = []
        try:
            blocks = self.layout_blocks(holder, max(w, 12.0), {})
        finally:
            self.fields = saved
        content_h = _blocks_height(blocks)
        rule = wattr(fr, "hRule")
        h = h_attr if rule == "exact" and h_attr else max(h_attr, content_h)
        conv = self

        def draw(sheet, x, y):
            conv.flow.emit_static_blocks(sheet, blocks, x, y)

        h_rel = {"page": "page", "margin": "margin", "text": "column"}.get(wattr(fr, "hAnchor") or "text", "column")
        v_rel = {"page": "page", "margin": "margin", "text": "paragraph"}.get(wattr(fr, "vAnchor") or "text", "paragraph")
        info = {"w": w, "h": h, "data": None, "ops": [draw], "inline": False, "behind": False, "wrap": "wrapNone",
                "dist": (0, 0, 0, 0), "h_rel": h_rel, "v_rel": v_rel,
                "h_off": twips(wattr(fr, "x", "0")) if wattr(fr, "x") is not None else 0.0,
                "v_off": twips(wattr(fr, "y", "0")) if wattr(fr, "y") is not None else 0.0}
        xa, ya = wattr(fr, "xAlign"), wattr(fr, "yAlign")
        if xa in ("left", "center", "right", "inside", "outside"):
            info["h_align"] = xa
        if ya in ("top", "center", "bottom", "inside", "outside", "inline"):
            info["v_align"] = ya if ya != "inline" else "top"
        wrap = wattr(fr, "wrap") or "auto"
        if wrap in ("around", "tight", "through", "auto"):
            info["wrap"] = "wrapSquare"
            info["dist"] = (twips(wattr(fr, "hSpace", "0")), twips(wattr(fr, "vSpace", "0")),
                            twips(wattr(fr, "hSpace", "0")), twips(wattr(fr, "vSpace", "0")))
        elif wrap == "notBeside":
            info["wrap"] = "wrapTopAndBottom"
        return info

    # ---- VML (older Word drawings)
    def collect_vml(self, c, rpr, src: ParaSrc, style):
        v = NS["v"]
        o = NS["o"]
        for shape in c.iter():
            tag = local(shape.tag)
            if tag not in ("rect", "roundrect", "oval", "line", "shape", "polyline", "group") or not shape.tag.startswith("{%s}" % v):
                continue
            if tag == "group":
                continue
            style_attr = shape.get("style") or ""
            props = {}
            for part in style_attr.split(";"):
                if ":" in part:
                    k, val = part.split(":", 1)
                    props[k.strip()] = val.strip()
            w, h = _vml_size(style_attr)
            # horizontal rule
            if shape.get("{%s}hr" % o) == "t":
                col = _vml_color(shape.get("fillcolor")) or (0.63, 0.63, 0.63)
                src.hr = (col, max(h, 0.75))
                continue
            imagedata = shape.find("{%s}imagedata" % v)
            if imagedata is not None:
                rid = imagedata.get("{%s}id" % o) or imagedata.get("{%s}id" % NS["r"])
                data = self.image_bytes(self.rels, rid) if rid else None
                if data is not None and w and h:
                    if props.get("position") == "absolute":
                        info = self.vml_anchor(props, w, h)
                        info["data"] = data
                        src.anchors.append(info)
                    else:
                        src.items.append(("image", (w, h, data), style))
                continue
            if not (w and h):
                continue
            fill = None if shape.get("filled") == "f" else (_vml_color(shape.get("fillcolor")) or (1.0, 1.0, 1.0))
            stroke = None if shape.get("stroked") == "f" else (_vml_color(shape.get("strokecolor")) or (0.0, 0.0, 0.0))
            weight = _vml_len(shape.get("strokeweight")) if shape.get("strokeweight") else 0.75
            textbox = shape.find("{%s}textbox" % v)
            blocks = None
            content_h = 0.0
            inset = (7.2, 3.6, 7.2, 3.6)
            if textbox is not None:
                content = textbox.find("w:txbxContent", NS)
                if textbox.get("inset"):
                    vals = [_vml_len(x) for x in textbox.get("inset").split(",")]
                    while len(vals) < 4:
                        vals.append(0.0)
                    inset = tuple(vals[:4])
                if content is not None:
                    saved = self.fields
                    self.fields = []
                    try:
                        blocks = self.layout_blocks(content, max(w - inset[0] - inset[2], 12.0), {})
                    finally:
                        self.fields = saved
                    content_h = _blocks_height(blocks)
            conv = self
            kind = tag

            def draw(sheet, x, y, w=w, h=h, fill=fill, stroke=stroke, weight=weight, blocks=blocks, inset=inset, kind=kind, shape=shape):
                if kind == "line":
                    fr = (shape.get("from") or "0,0").split(",")
                    to = (shape.get("to") or "0,0").split(",")
                    try:
                        x0, y0 = _vml_len(fr[0]), _vml_len(fr[1])
                        x1, y1 = _vml_len(to[0]), _vml_len(to[1])
                        sheet.line(x + x0, y + y0, x + x1, y + y1, stroke or (0, 0, 0), weight)
                    except (IndexError, ValueError):
                        pass
                elif kind == "oval":
                    from . import shapes as _sh
                    pts = _sh.outline("ellipse", w, h)[0][0]
                    sheet.poly([(x + px, y + py) for px, py in pts], fill=fill, stroke=stroke, width=weight, closed=True)
                else:
                    radius = min(w, h) * 0.16 if kind == "roundrect" else None
                    sheet.rect(x, y, x + w, y + h, fill=fill, stroke=stroke, width=weight, radius=radius)
                if blocks:
                    conv.flow.emit_static_blocks(sheet, blocks, x + inset[0], y + inset[1])

            if props.get("position") == "absolute" or True:
                info = self.vml_anchor(props, w, h)
                info["ops"] = [draw]
                src.anchors.append(info)

    def vml_anchor(self, props, w, h):
        def rel(value, horizontal):
            value = (value or "").lower()
            if horizontal:
                return {"page": "page", "margin": "margin", "text": "column", "char": "column"}.get(value, "column")
            return {"page": "page", "margin": "margin", "text": "paragraph", "line": "paragraph"}.get(value, "paragraph")
        info = {"w": w, "h": h, "data": None, "ops": [], "inline": False, "behind": False, "wrap": "wrapNone",
                "dist": (0, 0, 0, 0)}
        info["h_rel"] = rel(props.get("mso-position-horizontal-relative"), True)
        info["v_rel"] = rel(props.get("mso-position-vertical-relative"), False)
        info["h_off"] = _vml_len(props.get("margin-left", props.get("left", "0")))
        info["v_off"] = _vml_len(props.get("margin-top", props.get("top", "0")))
        ha = props.get("mso-position-horizontal")
        va = props.get("mso-position-vertical")
        if ha in ("left", "center", "right", "inside", "outside"):
            info["h_align"] = ha
        if va in ("top", "center", "bottom", "inside", "outside"):
            info["v_align"] = va
        wrap = props.get("mso-wrap-style")
        zi = props.get("z-index", "0")
        info["behind"] = zi.startswith("-")
        return info

    def read_picture(self, node):
        extent = node.find("{%s}extent" % NS["wp"])
        if extent is None:
            return None
        w = emu(extent.get("cx"))
        h = emu(extent.get("cy"))
        blip = None
        crop = None
        for sub in node.iter():
            t = local(sub.tag)
            if t == "blip":
                blip = sub
            elif t == "srcRect" and crop is None:
                crop = {k: float(sub.get(k, "0")) / 100000.0 for k in ("l", "t", "r", "b")}
        if blip is None:
            return None
        rid = blip.get("{%s}embed" % NS["r"])
        data = self.image_bytes(self.rels, rid) if rid else None
        if data is None:
            return None
        if crop and any(crop.values()):
            data = _crop_image(data, crop) or data
        info = {"w": w, "h": h, "data": data, "inline": local(node.tag) == "inline"}
        if local(node.tag) == "anchor":
            self.anchor_info(node, info)
        return info

    # ============================================================== layout of paragraphs

    def layout_para(self, src: ParaSrc, width: float, flow=None) -> ParaBox:
        ppr = src.ppr
        size = src.mark_style.size
        left = ppr.get("left", 0.0)
        if "left_chars" in ppr and "left" not in ppr:
            left = ppr["left_chars"] * size
        right = ppr.get("right", 0.0)
        first = 0.0
        if "first" in ppr:
            first = ppr["first"]
        elif "first_chars" in ppr:
            first = ppr["first_chars"] * size
        if "hanging" in ppr:
            first = -ppr["hanging"]
        elif "hanging_chars" in ppr:
            first = -ppr["hanging_chars"] * size
        jc = ppr.get("jc") or "left"
        align = {"start": "left", "end": "right", "justify": "both"}.get(jc, jc)
        if align not in ("left", "center", "right", "both", "distribute"):
            align = "left"

        items = list(src.items)
        marker_atoms: list[Atom] = []
        if src.marker:
            text, mstyle, suff, lvl = src.marker
            if text:
                marker_atoms.append(Atom(text, mstyle, T.text_width(text, mstyle), "text", brk=False))
                if suff == "space":
                    marker_atoms.append(Atom(" ", mstyle, T.char_width(" ", mstyle), "space", brk=False))
                elif suff == "tab":
                    marker_atoms.append(Atom("", mstyle, 0.0, "tab"))
        atoms = marker_atoms + T.atomize(items)
        atoms.append(Atom("", src.mark_style, 0.0, "mark", brk=True))

        tabs = []
        for kind, pos, leader in ppr.get("tabs", []):
            if kind == "clear":
                continue
            kind = {"start": "left", "end": "right", "num": "decimal"}.get(kind, kind)
            if kind == "bar":
                continue
            tabs.append(TabStop(pos, kind, leader if leader not in (None, "none") else None))
        if first < 0 and left > 0 and not any(abs(t.pos - left) < 0.5 for t in tabs):
            tabs.append(TabStop(left, "left"))
        if first == 0 and marker_atoms and left > 0:
            tabs.append(TabStop(left, "left"))

        line_off0 = left + first
        line_off0 = max(line_off0, 0.0) if first < 0 and left + first < 0 else line_off0

        grid = self.grid if ppr.get("snapToGrid", True) else 0.0
        rule = ppr.get("line")
        before = ppr.get("before", 0.0)
        if "before_lines" in ppr and self.grid:
            before = ppr["before_lines"] * self.grid
        wrapping = None
        if flow is not None:
            wrapping = flow.wrap_context(src, width, before, size * T.line_factor(src.mark_style.family))

        gaps: list[float] = []

        def width_for(i, lines):
            off = line_off0 if i == 0 else left
            if wrapping is None:
                return off, max(width - right - off, 8.0)
            if i > 0:
                h, _b = T.line_metrics(lines[-1], rule, grid)
                wrapping.advance(gaps[-1] + h)
            dx, end_rel, gap = wrapping.line_box(off, width - right)
            gaps.append(gap)
            return off + dx, max(end_rel - off - dx, 8.0), gap

        lines = T.break_lines(atoms, width_for, tabs, self.default_tab)
        heights, bases = [], []
        page_breaks = set()
        for i, line in enumerate(lines):
            avail = line.avail
            T.place_line(line, align, avail)
            h, b = T.line_metrics(line, rule, grid)
            heights.append(h + line.gap)
            bases.append(b + line.gap)
            if any(a.kind == "br" and a.data == "page" for a in line.atoms):
                page_breaks.add(i)
        before = ppr.get("before", 0.0)
        after = ppr.get("after", 0.0)
        if "before_lines" in ppr and self.grid:
            before = ppr["before_lines"] * self.grid
        if "after_lines" in ppr and self.grid:
            after = ppr["after_lines"] * self.grid
        return ParaBox(lines=lines, heights=heights, bases=bases, before=before, after=after, left=left, right=right,
                       width=width, align=align, keep_next=ppr.get("keepNext", False), keep_lines=ppr.get("keepLines", False),
                       page_break_before=ppr.get("pageBreakBefore", False), shd=ppr.get("shd"), pbdr=ppr.get("pbdr"),
                       anchors=src.anchors, bookmarks=src.bookmarks, footnotes=src.footnotes,
                       contextual=ppr.get("contextualSpacing", False), style_id=src.style_id,
                       widow=ppr.get("widowControl", True), outline=src.outline, page_breaks=page_breaks, hr=src.hr)

    # ============================================================== tables

    def make_table(self, tbl, width: float, ctx) -> TableBox:
        pr = tbl.find(wq("tblPr"))
        sid = wattr(pr.find(wq("tblStyle")) if pr is not None else None, "val") or self.styles.default_table
        chain = self.styles.chain(sid)
        borders: dict = {}
        cell_mar = {"top": 0.0, "left": 5.4, "bottom": 0.0, "right": 5.4}

        def read_tblpr(el):
            nonlocal borders
            if el is None:
                return
            b = el.find(wq("tblBorders"))
            if b is not None:
                for side in b:
                    borders[local(side.tag)] = parse_border(side)
            m = el.find(wq("tblCellMar"))
            if m is not None:
                for side in m:
                    name = {"start": "left", "end": "right"}.get(local(side.tag), local(side.tag))
                    if name in cell_mar:
                        cell_mar[name] = twips(wattr(side, "w", "0"))

        for item in chain:
            read_tblpr(item["tblpr"])
            whole = item["conds"].get("wholeTbl")
            if whole is not None:
                read_tblpr(whole.find(wq("tblPr")))
        read_tblpr(pr)
        look = pr.find(wq("tblLook")) if pr is not None else None
        flags = {"firstRow": True, "lastRow": False, "firstColumn": True, "lastColumn": False, "noHBand": False, "noVBand": True}
        if look is not None:
            val = wattr(look, "val")
            if val:
                try:
                    n = int(val, 16)
                    flags = {"firstRow": bool(n & 0x20), "lastRow": bool(n & 0x40), "firstColumn": bool(n & 0x80),
                             "lastColumn": bool(n & 0x100), "noHBand": bool(n & 0x200), "noVBand": bool(n & 0x400)}
                except ValueError:
                    pass
            for key in flags:
                v = wattr(look, key)
                if v is not None:
                    flags[key] = v in ("1", "true")
        band_size_h = 1
        band_size_v = 1
        for item in chain:
            tp = item["tblpr"]
            if tp is not None:
                rb = tp.find(wq("tblStyleRowBandSize"))
                cb = tp.find(wq("tblStyleColBandSize"))
                if rb is not None:
                    band_size_h = int(wattr(rb, "val", "1"))
                if cb is not None:
                    band_size_v = int(wattr(cb, "val", "1"))
        # override cell margins by direct mar in tblPr already via read_tblpr
        grid_cols = [twips(wattr(g, "w", "0")) for g in tbl.findall(f"{wq('tblGrid')}/{wq('gridCol')}")]
        rows_xml = [tr for tr in self._iter_rows(tbl)]
        if not grid_cols:
            first = rows_xml[0] if rows_xml else None
            if first is not None:
                for tc in first.findall(wq("tc")):
                    w = twips(wattr(tc.find(f"{wq('tcPr')}/{wq('tcW')}"), "w", "0"))
                    span = int(wattr(tc.find(f"{wq('tcPr')}/{wq('gridSpan')}"), "val", "1"))
                    grid_cols.extend([w / span] * span)
        ncols = len(grid_cols)
        # a row can have more grid columns than the grid declares
        for tr in rows_xml:
            count = 0
            for tc in tr.findall(wq("tc")):
                count += int(wattr(tc.find(f"{wq('tcPr')}/{wq('gridSpan')}"), "val", "1"))
            count += int(wattr(tr.find(f"{wq('trPr')}/{wq('gridBefore')}"), "val", "0"))
            while count > len(grid_cols):
                grid_cols.append(grid_cols[-1] if grid_cols else 50.0)
        ncols = len(grid_cols)
        tw = pr.find(wq("tblW")) if pr is not None else None
        total = sum(grid_cols)
        if tw is not None:
            kind = wattr(tw, "type", "dxa")
            val = float(wattr(tw, "w", "0") or 0)
            if kind == "dxa" and val > 0 and total > 0 and abs(total - val / 20.0) > 1.0:
                total_target = val / 20.0
                grid_cols = [g * total_target / total for g in grid_cols]
            elif kind == "pct" and val > 0:
                target = width * val / 5000.0
                if total > 0:
                    grid_cols = [g * target / total for g in grid_cols]
        total = sum(grid_cols)
        if total > width * 1.02 and total > 0:
            grid_cols = [g * width / total for g in grid_cols]
            total = width
        ind = twips(wattr(pr.find(wq("tblInd")), "w", "0")) if pr is not None and pr.find(wq("tblInd")) is not None else 0.0
        jc = wattr(pr.find(wq("jc")), "val", "left") if pr is not None and pr.find(wq("jc")) is not None else "left"
        x0 = ind
        if jc == "center":
            x0 = (width - total) / 2
        elif jc in ("right", "end"):
            x0 = width - total
        elif ind == 0:
            x0 = -cell_mar["left"] if False else 0.0
        colx = [x0]
        for g in grid_cols:
            colx.append(colx[-1] + g)

        rows: list[RowBox] = []
        occupied: dict[tuple, CellBox] = {}
        ri = 0
        nrows = len(rows_xml)
        last_data_row = nrows - 1
        for tr in rows_xml:
            trpr = tr.find(wq("trPr"))
            height = 0.0
            exact = False
            if trpr is not None:
                th = trpr.find(wq("trHeight"))
                if th is not None:
                    height = twips(wattr(th, "val", "0"))
                    exact = wattr(th, "hRule") == "exact"
            header = trpr is not None and trpr.find(wq("tblHeader")) is not None
            cant = trpr is not None and trpr.find(wq("cantSplit")) is not None
            ci = int(wattr(trpr.find(wq("gridBefore")), "val", "0")) if trpr is not None and trpr.find(wq("gridBefore")) is not None else 0
            cells: list[CellBox] = []
            for tc in tr.findall(wq("tc")):
                tcpr = tc.find(wq("tcPr"))
                span = int(wattr(tcpr.find(wq("gridSpan")), "val", "1")) if tcpr is not None and tcpr.find(wq("gridSpan")) is not None else 1
                vm = tcpr.find(wq("vMerge")) if tcpr is not None else None
                vmerge = None
                if vm is not None:
                    vmerge = "restart" if wattr(vm, "val") == "restart" else "continue"
                span = max(1, min(span, ncols - ci)) if ci < ncols else 1
                x = colx[min(ci, ncols)]
                cw = colx[min(ci + span, ncols)] - x
                # conditional formatting
                cond_rpr: dict = {}
                cond_ppr: dict = {}
                cell_shd = None
                cell_borders: dict = {}
                kinds = ["wholeTbl"]
                if not flags["noHBand"]:
                    data_row = ri - (1 if flags["firstRow"] else 0)
                    if data_row >= 0 and not (flags["lastRow"] and ri == last_data_row):
                        kinds.append("band1Horz" if (data_row // max(band_size_h, 1)) % 2 == 0 else "band2Horz")
                if not flags["noVBand"]:
                    dc = ci - (1 if flags["firstColumn"] else 0)
                    if dc >= 0:
                        kinds.append("band1Vert" if (dc // max(band_size_v, 1)) % 2 == 0 else "band2Vert")
                if flags["firstColumn"] and ci == 0:
                    kinds.append("firstCol")
                if flags["lastColumn"] and ci + span >= ncols:
                    kinds.append("lastCol")
                if flags["firstRow"] and ri == 0:
                    kinds.append("firstRow")
                if flags["lastRow"] and ri == last_data_row:
                    kinds.append("lastRow")
                if flags["firstRow"] and ri == 0 and flags["firstColumn"] and ci == 0:
                    kinds.append("nwCell")
                for item in chain:
                    for kind in kinds:
                        cond = item["conds"].get(kind)
                        if cond is None:
                            continue
                        cond_rpr.update(parse_rpr(cond.find(wq("rPr")), self.theme))
                        cond_ppr.update(parse_ppr(cond.find(wq("pPr")), self.theme))
                        ctcpr = cond.find(wq("tcPr"))
                        if ctcpr is not None:
                            self._read_tcpr(ctcpr, cell_borders, None)
                            shd = ctcpr.find(wq("shd"))
                            if shd is not None:
                                fill = self._shd_fill(shd)
                                if fill is not None:
                                    cell_shd = fill
                for item in chain:
                    if item["tcpr"] is not None:
                        shd = item["tcpr"].find(wq("shd"))
                        if shd is not None and cell_shd is None:
                            f = self._shd_fill(shd)
                            if f is not None:
                                cell_shd = f
                margins = [cell_mar["top"], cell_mar["right"], cell_mar["bottom"], cell_mar["left"]]
                valign = "top"
                if tcpr is not None:
                    self._read_tcpr(tcpr, cell_borders, None)
                    shd = tcpr.find(wq("shd"))
                    if shd is not None:
                        fill = self._shd_fill(shd)
                        cell_shd = fill if fill is not None else (None if wattr(shd, "fill") == "auto" else cell_shd)
                    va = tcpr.find(wq("vAlign"))
                    if va is not None:
                        valign = wattr(va, "val", "top")
                    mar = tcpr.find(wq("tcMar"))
                    if mar is not None:
                        for side in mar:
                            name = {"start": "left", "end": "right"}.get(local(side.tag), local(side.tag))
                            idx = {"top": 0, "right": 1, "bottom": 2, "left": 3}.get(name)
                            if idx is not None:
                                margins[idx] = twips(wattr(side, "w", "0"))
                cell = CellBox(col=ci, span=span, x=x, width=cw, blocks=[], margins=tuple(margins), shd=cell_shd,
                               borders=cell_borders, valign=valign, vmerge=vmerge)
                if vmerge == "continue":
                    cell.skip = True
                else:
                    inner = max(cw - margins[1] - margins[3], 6.0)
                    cctx = {"tbl_rpr": {**cond_rpr}, "tbl_ppr": {**cond_ppr}, "in_table": True}
                    # table style text properties of the whole table
                    for item in chain:
                        whole = item["conds"].get("wholeTbl")
                        base_r = dict(item["rpr"])
                        base_p = dict(item["ppr"])
                        if whole is not None:
                            base_r.update(parse_rpr(whole.find(wq("rPr")), self.theme))
                            base_p.update(parse_ppr(whole.find(wq("pPr")), self.theme))
                        cctx["tbl_rpr"] = {**base_r, **cctx["tbl_rpr"]}
                        cctx["tbl_ppr"] = {**base_p, **cctx["tbl_ppr"]}
                    cell.blocks = self.layout_blocks(tc, inner, cctx)
                    cell.content_h = _blocks_height(cell.blocks)
                cells.append(cell)
                ci += span
            row = RowBox(cells=cells, height=0.0, header=header, cant_split=cant, min_h=height, exact=exact)
            rows.append(row)
            ri += 1
        # vertical merges: attach continuation cells to the cell above them
        starts: dict[int, tuple[int, CellBox]] = {}
        for r_index, row in enumerate(rows):
            for cell in row.cells:
                if cell.vmerge == "continue":
                    src = starts.get(cell.col)
                    if src is not None:
                        src[1].rowspan += 1
                elif cell.vmerge == "restart":
                    starts[cell.col] = (r_index, cell)
                else:
                    starts.pop(cell.col, None)
        # row heights: single-row cells first, then spans
        for row in rows:
            h = 0.0
            for cell in row.cells:
                if cell.skip or cell.rowspan > 1:
                    continue
                h = max(h, cell.content_h + cell.margins[0] + cell.margins[2])
            if row.exact:
                h = row.min_h
            else:
                h = max(h, row.min_h)
            row.height = h
        for r_index, row in enumerate(rows):
            for cell in row.cells:
                if cell.rowspan > 1 and not cell.skip:
                    need = cell.content_h + cell.margins[0] + cell.margins[2]
                    have = sum(rows[k].height for k in range(r_index, min(r_index + cell.rowspan, len(rows))))
                    if need > have:
                        rows[min(r_index + cell.rowspan, len(rows)) - 1].height += need - have
        return TableBox(rows=rows, width=total, x=x0, colx=colx, borders=borders, jc=jc)

    def _iter_rows(self, tbl):
        for child in tbl:
            tag = local(child.tag)
            if tag == "tr":
                yield child
            elif tag in ("sdt", "sdtContent", "customXml"):
                inner = child.find(wq("sdtContent")) if tag == "sdt" else child
                if inner is not None:
                    yield from self._iter_rows(inner)

    def _shd_fill(self, shd):
        fill = wattr(shd, "fill")
        if wattr(shd, "themeFill"):
            return self.theme.color(wattr(shd, "themeFill"), wattr(shd, "themeFillTint"), wattr(shd, "themeFillShade"))
        if fill and fill != "auto":
            return hex_color(fill)
        val = wattr(shd, "val")
        if val and val.startswith("pct") and fill in (None, "auto"):
            color = hex_color(wattr(shd, "color"), (0, 0, 0)) or (0, 0, 0)
            try:
                p = int(val[3:]) / 100.0
            except ValueError:
                return None
            return tuple(1 - (1 - c) * p for c in color)
        return None

    def _read_tcpr(self, tcpr, borders: dict, _unused):
        b = tcpr.find(wq("tcBorders"))
        if b is not None:
            for side in b:
                name = {"start": "left", "end": "right"}.get(local(side.tag), local(side.tag))
                if name in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    borders[name] = parse_border(side) if wattr(side, "val") not in (None,) else None
                    if wattr(side, "val") in ("nil", "none"):
                        borders[name] = "none"

    # ============================================================== block lists

    def layout_blocks(self, parent, width: float, ctx) -> list:
        out = []
        for child in self.iter_blocks(parent):
            if local(child.tag) == "p":
                src = self.make_para(child, ctx)
                out.append(self.layout_para(src, width))
            else:
                out.append(self.make_table(child, width, ctx))
        return out

    # ============================================================== document flow

    def convert(self) -> bytes:
        body = self.body
        sect_groups = []      # (blocks xml, sectPr)
        current = []
        for child in self.iter_blocks(body):
            current.append(child)
            if local(child.tag) == "p":
                ppr = child.find(wq("pPr"))
                sect = ppr.find(wq("sectPr")) if ppr is not None else None
                if sect is not None:
                    sect_groups.append((current, sect))
                    current = []
        final = body.find(wq("sectPr"))
        if current or not sect_groups:
            sect_groups.append((current, final if final is not None else ET.Element(wq("sectPr"))))
        if final is not None:
            grid = final.find(wq("docGrid"))
            if grid is not None and wattr(grid, "type") in ("lines", "linesAndChars", "snapToChars"):
                pitch = wattr(grid, "linePitch")
                if pitch and wattr(grid, "type") != "snapToChars":
                    self.grid = twips(pitch)
        self._footnote_numbers = {}
        flow = Flow(self)
        self.flow = flow
        previous = None
        for blocks, sect in sect_groups:
            flow.start_section(sect, previous)
            previous = sect
            flow.place_blocks(blocks)
        flow.finish()
        doc = T.render(self.sheets, fields=flow.field_values(), anchors=self.anchors)
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
        return data


def _blocks_height(blocks) -> float:
    total = 0.0
    prev_after = 0.0
    for i, box in enumerate(blocks):
        if isinstance(box, ParaBox):
            before = box.before
            if box.contextual and i > 0 and isinstance(blocks[i - 1], ParaBox) and blocks[i - 1].style_id == box.style_id:
                before = 0.0
                total -= prev_after
            total += before + box.height() + box.after
            prev_after = box.after
        else:
            total += box.height()
            prev_after = 0.0
    return total


# ============================================================================ the flow of pages


class WrapContext:
    """Where the lines of one paragraph can go, given pictures that text flows around."""

    def __init__(self, flow: "Flow", col_x: float, y0: float, est: float):
        self.flow = flow
        self.col_x = col_x
        self.y = y0
        self.est = max(est, 6.0)
        self.crossed = False

    def advance(self, h: float):
        self.y += h

    def line_box(self, off: float, right_limit: float):
        flow = self.flow
        x_start = self.col_x + off
        x_end = self.col_x + right_limit
        y = self.y
        gap = 0.0
        bottom = flow.body_bottom - flow.page_reserve
        if not self.crossed and y + self.est > bottom and y > flow.body_top + 1:
            self.crossed = True
            self.y = y = flow.body_top
        if self.crossed:
            return 0.0, right_limit, 0.0
        for _ in range(12):
            moved = False
            for ex in flow.excl:
                if ex["mode"] == "tb" and ex["y0"] < y + self.est and ex["y1"] > y and ex["x0"] < x_end and ex["x1"] > x_start:
                    gap += ex["y1"] - y
                    y = ex["y1"]
                    moved = True
            if moved:
                continue
            free = [(x_start, x_end)]
            hits = [ex for ex in flow.excl if ex["mode"] == "side" and ex["y0"] < y + self.est and ex["y1"] > y]
            for ex in hits:
                nxt = []
                for a0, a1 in free:
                    if ex["x1"] <= a0 or ex["x0"] >= a1:
                        nxt.append((a0, a1))
                        continue
                    if ex["x0"] - a0 > 1:
                        nxt.append((a0, ex["x0"]))
                    if a1 - ex["x1"] > 1:
                        nxt.append((ex["x1"], a1))
                free = nxt
            seg = max(free, key=lambda f: (f[1] - f[0], -f[0])) if free else None
            if hits and (seg is None or seg[1] - seg[0] < 24):
                bottom_y = max(ex["y1"] for ex in hits)
                gap += bottom_y - y
                y = bottom_y
                continue
            if seg is None:
                seg = (x_start, x_end)
            return seg[0] - x_start, seg[1] - self.col_x, gap
        return 0.0, right_limit, gap


class Flow:
    def __init__(self, conv: DocxToPdf):
        self.c = conv
        self.sheet: Sheet | None = None
        self.y = 0.0
        self.sect = None
        self.page_no_in_section = 0
        self.first_page_of_doc = True
        self.col = 0
        self.at_top = True
        self.hf_cache: dict = {}
        self.header_refs: dict = {}
        self.footer_refs: dict = {}
        self.cfg: dict = {}
        self.excl: list = []
        self.page_reserve = 0.0

    def field_values(self):
        values = {}
        for name, (page, _y) in self.c.anchors.items():
            values["PAGEREF:" + name] = page + 1
        return values

    # ---- sections
    def start_section(self, sect, previous):
        c = self.c
        pg = sect.find(wq("pgSz"))
        mar = sect.find(wq("pgMar"))
        w = twips(wattr(pg, "w", "11906")) if pg is not None else 595.3
        h = twips(wattr(pg, "h", "16838")) if pg is not None else 841.9
        if pg is not None and wattr(pg, "orient") == "landscape" and w < h:
            w, h = h, w
        cfg = {
            "w": w, "h": h,
            "top": twips(wattr(mar, "top", "1440")) if mar is not None else 72.0,
            "bottom": twips(wattr(mar, "bottom", "1440")) if mar is not None else 72.0,
            "left": twips(wattr(mar, "left", "1440")) if mar is not None else 72.0,
            "right": twips(wattr(mar, "right", "1440")) if mar is not None else 72.0,
            "header": twips(wattr(mar, "header", "720")) if mar is not None else 36.0,
            "footer": twips(wattr(mar, "footer", "720")) if mar is not None else 36.0,
            "gutter": twips(wattr(mar, "gutter", "0")) if mar is not None else 0.0,
        }
        cfg["left"] += cfg["gutter"]
        cols = sect.find(wq("cols"))
        ncols = int(wattr(cols, "num", "1") or 1) if cols is not None else 1
        text_w = cfg["w"] - cfg["left"] - cfg["right"]
        col_list = []
        if ncols > 1 and cols is not None:
            col_elems = cols.findall(wq("col"))
            space = twips(wattr(cols, "space", "720"))
            if col_elems and wattr(cols, "equalWidth") == "0":
                x = cfg["left"]
                for ce in col_elems:
                    cw = twips(wattr(ce, "w", "0"))
                    col_list.append((x, cw))
                    x += cw + twips(wattr(ce, "space", "0"))
            else:
                cw = (text_w - space * (ncols - 1)) / ncols
                col_list = [(cfg["left"] + i * (cw + space), cw) for i in range(ncols)]
        else:
            col_list = [(cfg["left"], text_w)]
        cfg["cols"] = col_list
        cfg["title_pg"] = sect.find(wq("titlePg")) is not None
        kind = wattr(sect.find(wq("type")), "val", "nextPage")
        cfg["type"] = kind
        # headers and footers: references fall back to the previous section's
        for kind_name, store, tag in (("header", self.header_refs, "headerReference"), ("footer", self.footer_refs, "footerReference")):
            refs = dict(store)
            for ref in sect.findall(wq(tag)):
                rid = ref.get("{%s}id" % NS["r"])
                rel = c.rels.get(rid)
                if rel:
                    refs[wattr(ref, "type", "default")] = rel[0]
            store.clear()
            store.update(refs)
        pg_num = sect.find(wq("pgNumType"))
        cfg["page_start"] = int(wattr(pg_num, "start")) if pg_num is not None and wattr(pg_num, "start") else None
        cfg["header_refs"] = dict(self.header_refs)
        cfg["footer_refs"] = dict(self.footer_refs)
        continuous = previous is not None and kind == "continuous" and self.sheet is not None \
            and abs(cfg["w"] - self.cfg.get("w", 0)) < 1 and abs(cfg["h"] - self.cfg.get("h", 0)) < 1
        self.cfg = cfg
        self.page_no_in_section = 0
        if continuous:
            self.col = 0
            self.body_cfg_update()
            return
        if previous is not None or self.sheet is None:
            if self.sheet is not None and not (self.at_top and self.col == 0 and not self.sheet.ops and False):
                pass
            self.new_page(force_new=True)
            if kind in ("oddPage", "evenPage"):
                want_odd = kind == "oddPage"
                if (len(self.c.sheets) % 2 == 1) != want_odd:
                    self.new_page(force_new=True)

    def body_cfg_update(self):
        top, bottom = self._body_extent()
        self.body_top = top
        self.body_bottom = bottom
        self.cols = self.cfg["cols"]

    def _body_extent(self):
        cfg = self.cfg
        which = self._hf_kind()
        header_h = self._hf_height("header", which)
        footer_h = self._hf_height("footer", which)
        top = max(cfg["top"], cfg["header"] + header_h) if header_h else cfg["top"]
        bottom = cfg["h"] - (max(cfg["bottom"], cfg["footer"] + footer_h) if footer_h else cfg["bottom"])
        return top, bottom

    def _hf_kind(self):
        n = self.page_no_in_section
        if self.cfg.get("title_pg") and n == 0:
            return "first"
        if self.c.even_odd and (len(self.c.sheets) % 2 == 0):
            return "even"
        return "default"

    def _hf_blocks(self, which: str, kind: str):
        refs = self.cfg["header_refs"] if which == "header" else self.cfg["footer_refs"]
        part = refs.get(kind) or (refs.get("default") if kind == "even" and False else None)
        if part is None and kind == "first" and self.cfg.get("title_pg"):
            return None
        part = part or refs.get("default")
        return part

    def _hf_layout(self, which: str, kind: str):
        part = self._hf_blocks(which, kind)
        if not part:
            return None
        text_w = self.cfg["w"] - self.cfg["left"] - self.cfg["right"]
        key = (part, round(text_w, 1))
        if key in self.hf_cache:
            return self.hf_cache[key]
        root = self.c.pkg.xml(part)
        result = None
        if root is not None:
            saved_rels = self.c.rels
            self.c.rels = self.c.pkg.rels(part)
            saved_fields = self.c.fields
            self.c.fields = []
            try:
                blocks = self.c.layout_blocks(root, text_w, {})
            finally:
                self.c.rels = saved_rels
                self.c.fields = saved_fields
            result = (blocks, _blocks_height(blocks))
        self.hf_cache[key] = result
        return result

    def _hf_height(self, which, kind):
        lay = self._hf_layout(which, kind)
        return lay[1] if lay else 0.0

    # ---- pages
    def new_page(self, force_new=False):
        c = self.c
        cfg = self.cfg
        self.page_no_in_section += 0
        sheet = Sheet(cfg["w"], cfg["h"])
        if c.page_bg is not None:
            sheet.below.append(("rect", 0, 0, cfg["w"], cfg["h"], c.page_bg, None, 0, None, None))
        c.sheets.append(sheet)
        self.sheet = sheet
        kind = self._hf_kind()
        for which in ("header", "footer"):
            lay = self._hf_layout(which, kind)
            if not lay:
                continue
            blocks, height = lay
            if which == "header":
                top = cfg["header"]
            else:
                top = cfg["h"] - cfg["footer"] - height
            tmp = Sheet(cfg["w"], cfg["h"])
            self.emit_blocks_static(tmp, blocks, cfg["left"], top)
            sheet.ops.extend(tmp.ops)
        self.body_top, self.body_bottom = self._body_extent()
        self.cols = cfg["cols"]
        self.col = 0
        self.y = self.body_top
        self.at_top = True
        self.page_no_in_section += 1
        self.page_footnotes = []
        self.page_reserve = 0.0
        self.excl = []

    def finish(self):
        pass

    def next_column_or_page(self):
        if self.col + 1 < len(self.cols):
            self.col += 1
            self.y = self.body_top
            self.at_top = True
        else:
            self.new_page()

    # ---- placing
    def place_blocks(self, blocks_xml):
        c = self.c
        # Framed paragraphs are not part of the flow: each belongs to the next ordinary block, like a floating drawing
        frames_for: dict[int, list] = {}
        filtered = []
        pending = []
        for el in blocks_xml:
            if c.is_frame(el):
                pending.append(el)
                continue
            if pending:
                frames_for[len(filtered)] = pending
                pending = []
            filtered.append(el)
        trailing = pending
        blocks_xml = filtered
        # lay out lazily so a paragraph is measured at the width of the column it lands in
        ctx = {}
        boxes_cache: dict[int, object] = {}

        def box_at(i):
            if i >= len(blocks_xml):
                return None
            if i not in boxes_cache:
                el = blocks_xml[i]
                width = self.cols[self.col][1]
                if local(el.tag) == "p":
                    src = c.make_para(el, ctx)
                    for fr in frames_for.get(i, ()):
                        src.anchors.append(c.frame_anchor(fr))
                    boxes_cache[i] = c.layout_para(src, width, flow=self)
                else:
                    boxes_cache[i] = c.make_table(el, width, ctx)
            return boxes_cache[i]

        prev = None
        for i in range(len(blocks_xml)):
            box = box_at(i)
            if isinstance(box, ParaBox):
                nxt = box_at(i + 1) if box.keep_next else None
                self.place_para(box, prev, nxt)
            else:
                for fr in frames_for.get(i, ()):
                    a = c.frame_anchor(fr)
                    x, y = self.anchor_rect(a, self.y)
                    if self.sheet is not None:
                        self.draw_anchor(self.sheet, a, x, y)
                self.place_table(box)
            prev = box
            boxes_cache.pop(i - 2, None)
        for fr in trailing:
            if self.sheet is not None:
                a = c.frame_anchor(fr)
                x, y = self.anchor_rect(a, self.y)
                self.draw_anchor(self.sheet, a, x, y)

    def remaining(self):
        return self.body_bottom - self.page_reserve - self.y

    def place_para(self, box: ParaBox, prev, nxt):
        sheet_before = self.sheet
        n = len(box.lines)
        if box.page_break_before and not self.at_top:
            self.new_page()
        before = box.before
        if box.contextual and isinstance(prev, ParaBox) and prev.style_id == box.style_id:
            before = 0.0
            self.y -= prev.after if self.y - prev.after >= self.body_top else 0.0
        # keep with next: need this paragraph plus the first lines of the next to fit
        if box.keep_next and nxt is not None and not self.at_top:
            need = before + box.height() + box.after
            if isinstance(nxt, ParaBox):
                need += nxt.before + sum(nxt.heights[:2])
            elif isinstance(nxt, TableBox) and nxt.rows:
                need += nxt.rows[0].height
            if need > self.remaining() and need < (self.body_bottom - self.body_top):
                self.next_column_or_page()
        if box.keep_lines and not self.at_top and box.height() + before > self.remaining() \
                and box.height() < (self.body_bottom - self.body_top):
            self.next_column_or_page()
        first_chunk = True
        i = 0
        if self.at_top and len(self.c.sheets) > 1:
            before = 0.0      # space before is dropped at the top of a page after the first
        self.y += before
        width = self.cols[self.col][1]
        while i < n:
            avail = self.remaining()
            k = 0
            used = 0.0
            while i + k < n and used + box.heights[i + k] <= avail + 0.01:
                used += box.heights[i + k]
                k += 1
                if (i + k - 1) in box.page_breaks:
                    break
            if k == 0:
                if self.at_top:
                    k = 1   # nothing would ever fit: place one line anyway
                else:
                    self.next_column_or_page()
                    continue
            # widow / orphan control
            if i + k < n and box.widow and n >= 3:
                if i == 0 and k == 1 and not self.at_top:
                    self.next_column_or_page()
                    continue
                if n - (i + k) == 1 and k >= 3:
                    k -= 1
            self.emit_para(box, i, i + k, first_chunk, i + k >= n)
            first_chunk = False
            i += k
            if i < n:
                self.next_column_or_page()
            elif (i - 1) in box.page_breaks:
                self.next_column_or_page()
        self.y += box.after

    def emit_para(self, box: ParaBox, i0: int, i1: int, first: bool, last: bool):
        sheet = self.sheet
        x_left = self.cols[self.col][0]
        top = self.y
        height = sum(box.heights[i0:i1])
        self.at_top = False
        if first:
            for name in box.bookmarks:
                self.c.anchors.setdefault(name, (len(self.c.sheets) - 1, top))
        self.draw_para_decor(sheet, box, x_left, top, height, first, last)
        if box.hr:
            ymid = top + height / 2
            sheet.line(x_left + box.left, ymid, x_left + box.width - box.right, ymid, box.hr[0], max(box.hr[1], 0.5))
        y = top
        for k in range(i0, i1):
            line = box.lines[k]
            T.emit_line(sheet, line, x_left + line.indent, y + box.bases[k], y, box.heights[k])
            y += box.heights[k]
        for anchor in box.anchors:
            if first:
                self.place_anchor(anchor, x_left, top, box)
        self.y = y

    def draw_para_decor(self, sheet, box: ParaBox, x_left, top, height, first, last):
        left_edge = x_left + min(box.left, box.left + min(0.0, 0.0))
        x0 = x_left + box.left
        if box.lines and box.lines[0].indent < box.left:
            x0 = x_left + box.lines[0].indent
        x1 = x_left + box.width - box.right
        if box.shd is not None:
            sheet.rect(x0, top, x1, top + height, fill=box.shd)
        if box.pbdr:
            for side, spec in box.pbdr.items():
                if not spec:
                    continue
                _style, width, color, space = spec
                if side == "top" and first:
                    sheet.line(x0 - 1, top - space, x1 + 1, top - space, color, width)
                elif side == "bottom" and last:
                    sheet.line(x0 - 1, top + height + space, x1 + 1, top + height + space, color, width)
                elif side == "left":
                    sheet.line(x0 - space - width / 2, top - (space if first else 0), x0 - space - width / 2,
                               top + height + (space if last else 0), color, width)
                elif side == "right":
                    sheet.line(x1 + space + width / 2, top - (space if first else 0), x1 + space + width / 2,
                               top + height + (space if last else 0), color, width)

    def anchor_rect(self, a, para_top):
        cfg = self.cfg
        w, h = a["w"], a["h"]
        rel_h = a.get("h_rel") or "column"
        rel_v = a.get("v_rel") or "paragraph"
        col_x, col_w = self.cols[self.col]
        if rel_h == "page":
            base_x, span_w = 0.0, cfg["w"]
        elif rel_h in ("margin", "leftMargin", "insideMargin", "rightMargin", "outsideMargin"):
            base_x, span_w = cfg["left"], cfg["w"] - cfg["left"] - cfg["right"]
        else:
            base_x, span_w = col_x, col_w
        if a.get("h_align"):
            al = a["h_align"]
            x = base_x if al in ("left", "inside") else (base_x + span_w - w if al in ("right", "outside") else base_x + (span_w - w) / 2)
        else:
            x = base_x + a.get("h_off", 0.0)
        if rel_v == "page":
            base_y, span_h = 0.0, cfg["h"]
        elif rel_v in ("margin", "topMargin", "bottomMargin", "insideMargin", "outsideMargin"):
            base_y, span_h = cfg["top"], cfg["h"] - cfg["top"] - cfg["bottom"]
            if rel_v == "bottomMargin":
                base_y, span_h = cfg["h"] - cfg["bottom"], cfg["bottom"]
        else:
            base_y, span_h = para_top, h
        if a.get("v_align"):
            al = a["v_align"]
            y = base_y if al in ("top", "inside") else (base_y + span_h - h if al in ("bottom", "outside") else base_y + (span_h - h) / 2)
        else:
            y = base_y + a.get("v_off", 0.0)
        return x, y

    def place_anchor(self, a, x_left, para_top, box):
        x, y = self.anchor_rect(a, para_top)
        self.draw_anchor(self.sheet, a, x, y)

    def draw_anchor(self, sheet, a, x, y):
        if a.get("data") is not None:
            sheet.image(x, y, x + a["w"], y + a["h"], a["data"])
        for op in a.get("ops", ()):
            op(sheet, x, y)

    def wrap_context(self, src, width, before, est_h):
        if self.sheet is None:
            return None
        wrapping_anchors = [a for a in src.anchors if a.get("wrap") in ("wrapSquare", "wrapTight", "wrapThrough", "wrapTopAndBottom")]
        if not wrapping_anchors and not self.excl:
            return None
        y0 = self.y + (0.0 if self.at_top and len(self.c.sheets) > 1 else before)
        for a in wrapping_anchors:
            x, y = self.anchor_rect(a, y0)
            dl, dt, dr, db = a.get("dist", (0, 0, 0, 0))
            self.excl.append({"x0": x - dl, "y0": y - dt, "x1": x + a["w"] + dr, "y1": y + a["h"] + db,
                              "mode": "tb" if a["wrap"] == "wrapTopAndBottom" else "side"})
        if not self.excl:
            return None
        return WrapContext(self, self.cols[self.col][0], y0, est_h)

    def place_table(self, table: TableBox):
        x_left = self.cols[self.col][0]
        rows = table.rows
        header_rows = []
        for r in rows:
            if r.header:
                header_rows.append(r)
            else:
                break
        i = 0
        while i < len(rows):
            row = rows[i]
            if row.height > self.remaining() + 0.01 and not self.at_top:
                if row.height <= self.body_bottom - self.body_top or True:
                    self.next_column_or_page()
                    if header_rows and not row.header:
                        for hr in header_rows:
                            self.emit_row(table, hr, x_left, -1)
            if row.height > self.remaining() + 0.01 and self.at_top:
                self.emit_row_split(table, rows, i, x_left)
                i += 1
                continue
            self.emit_row(table, row, x_left, i)
            i += 1
        self.at_top = False

    def emit_row_split(self, table, rows, i, x_left):
        """A row taller than a whole page: draw what fits, continue on the next pages."""
        row = rows[i]
        # simplest faithful approach: scale nothing, cut the row into page-sized slices
        offset = 0.0
        total = row.height
        while offset < total - 0.01:
            avail = self.remaining()
            part = min(avail, total - offset)
            self.emit_row(table, row, x_left, i, clip=(offset, part))
            offset += part
            if offset < total - 0.01:
                self.next_column_or_page()
            else:
                self.y += 0

    def emit_row(self, table: TableBox, row: RowBox, x_left, index, clip=None):
        sheet = self.sheet
        top = self.y
        height = row.height if clip is None else clip[1]
        offset = 0.0 if clip is None else clip[0]
        for cell in row.cells:
            if cell.skip:
                continue
            span_h = row.height
            if cell.rowspan > 1 and index >= 0:
                r_index = table.rows.index(row)
                span_h = sum(table.rows[k].height for k in range(r_index, min(r_index + cell.rowspan, len(table.rows))))
                if clip is None:
                    # a merged cell drawn with its first row covers the rows below; they are skipped when drawn
                    pass
            x0 = x_left + cell.x
            x1 = x0 + cell.width
            if cell.shd is not None:
                sheet.rect(x0, top, x1, top + (height if cell.rowspan == 1 else span_h), fill=cell.shd)
            # content
            inner_x = x0 + cell.margins[3]
            content_y = top - offset + cell.margins[0]
            if cell.valign in ("center", "bottom") and cell.content_h > 0:
                free = span_h - cell.margins[0] - cell.margins[2] - cell.content_h
                if free > 0:
                    content_y += free / 2 if cell.valign == "center" else free
            self.emit_static_blocks(sheet, cell.blocks, inner_x, content_y, clip=(top, top + height) if clip else None)
        self.draw_row_borders(table, row, x_left, top, height, index)
        self.y = top + height

    def draw_row_borders(self, table: TableBox, row: RowBox, x_left, top, height, index):
        sheet = self.sheet
        rows = table.rows
        last_index = len(rows) - 1
        tb = table.borders
        for cell in row.cells:
            if cell.skip:
                continue
            x0 = x_left + cell.x
            x1 = x0 + cell.width
            bottom = top + height
            span_rows = cell.rowspan if cell.rowspan > 1 else 1
            if span_rows > 1:
                r_index = rows.index(row)
                bottom = top + sum(rows[k].height for k in range(r_index, min(r_index + span_rows, len(rows))))
            r_index = rows.index(row) if index >= 0 else 0
            first_row = r_index == 0
            last_row = r_index + span_rows - 1 >= last_index
            first_col = cell.col == 0
            last_col = cell.col + cell.span >= len(table.colx) - 1

            def pick(side, outer, inner_name, is_outer):
                spec = cell.borders.get(side)
                if spec == "none":
                    return None
                if spec:
                    return spec
                return tb.get(side if is_outer else inner_name)

            top_b = pick("top", True, "insideH", first_row)
            bottom_b = pick("bottom", True, "insideH", last_row)
            left_b = pick("left", True, "insideV", first_col)
            right_b = pick("right", True, "insideV", last_col)
            # a border drawn by the neighbour above/left is not drawn twice
            if top_b:
                self._line(sheet, x0, top, x1, top, top_b)
            if bottom_b:
                self._line(sheet, x0, bottom, x1, bottom, bottom_b)
            if left_b:
                self._line(sheet, x0, top, x0, bottom, left_b)
            if right_b:
                self._line(sheet, x1, top, x1, bottom, right_b)

    @staticmethod
    def _line(sheet, x0, y0, x1, y1, spec):
        style, width, color, _space = spec
        dash = None
        if style in ("dotted",):
            dash = "[1 2] 0"
        elif style in ("dashed", "dashSmallGap", "dashDotStroked"):
            dash = "[3 2] 0"
        sheet.line(x0, y0, x1, y1, color, width, dash)
        if style in ("double", "thinThickSmallGap", "thickThinSmallGap"):
            if y0 == y1:
                sheet.line(x0, y0 + width * 2, x1, y1 + width * 2, color, width)
            else:
                sheet.line(x0 + width * 2, y0, x1 + width * 2, y1, color, width)

    # ---- static blocks (cells, headers, footers): placed at fixed positions
    def emit_blocks_static(self, sheet, blocks, x, y):
        self.emit_static_blocks(sheet, blocks, x, y)

    def emit_static_blocks(self, sheet, blocks, x, y, clip=None):
        prev = None
        for box in blocks:
            if isinstance(box, ParaBox):
                before = box.before
                if box.contextual and isinstance(prev, ParaBox) and prev.style_id == box.style_id:
                    before = 0.0
                    y -= prev.after
                y += before
                top = y
                visible = clip is None or (y + box.height() > clip[0] and y < clip[1])
                if visible:
                    self.draw_para_decor(sheet, box, x, top, box.height(), True, True)
                    if box.hr:
                        ymid = top + box.height() / 2
                        sheet.line(x + box.left, ymid, x + box.width - box.right, ymid, box.hr[0], max(box.hr[1], 0.5))
                    for k, line in enumerate(box.lines):
                        ly = y
                        if clip is None or (ly + box.heights[k] > clip[0] and ly < clip[1]):
                            T.emit_line(sheet, line, x + line.indent, ly + box.bases[k], ly, box.heights[k])
                        y += box.heights[k]
                    for anchor in box.anchors:
                        self.place_anchor_static(sheet, anchor, x, top)
                else:
                    y += box.height()
                y += box.after
                prev = box
            else:
                for row in box.rows:
                    self._static_row(sheet, box, row, x, y)
                    y += row.height
                prev = box

    def place_anchor_static(self, sheet, a, x, top):
        cfg = self.cfg
        w, h = a["w"], a["h"]
        rel_h = a.get("h_rel") or "column"
        if rel_h == "page":
            base_x, span_w = 0.0, cfg["w"]
        elif rel_h == "column":
            base_x, span_w = x, cfg["w"] - cfg["left"] - cfg["right"]
        else:
            base_x, span_w = cfg["left"], cfg["w"] - cfg["left"] - cfg["right"]
        if a.get("h_align"):
            al = a["h_align"]
            ax = base_x if al in ("left", "inside") else (base_x + span_w - w if al in ("right", "outside") else base_x + (span_w - w) / 2)
        else:
            ax = base_x + a.get("h_off", 0.0)
        rel_v = a.get("v_rel") or "paragraph"
        if rel_v == "page":
            base_y, span_h = 0.0, cfg["h"]
        elif rel_v in ("margin", "topMargin"):
            base_y, span_h = cfg["top"], cfg["h"] - cfg["top"] - cfg["bottom"]
        else:
            base_y, span_h = top, h
        if a.get("v_align"):
            al = a["v_align"]
            ay = base_y if al in ("top", "inside") else (base_y + span_h - h if al in ("bottom", "outside") else base_y + (span_h - h) / 2)
        else:
            ay = base_y + a.get("v_off", 0.0)
        self.draw_anchor(sheet, a, ax, ay)

    def _static_row(self, sheet, table, row, x_left, top):
        for cell in row.cells:
            if cell.skip:
                continue
            x0 = x_left + cell.x
            if cell.shd is not None:
                sheet.rect(x0, top, x0 + cell.width, top + row.height, fill=cell.shd)
            self.emit_static_blocks(sheet, cell.blocks, x0 + cell.margins[3], top + cell.margins[0])
        # borders reuse the page-flow routine through a temporary sheet swap
        saved = self.sheet
        self.sheet = sheet
        try:
            self.draw_row_borders(table, row, x_left, top, row.height, table.rows.index(row))
        finally:
            self.sheet = saved


# ============================================================================ helpers


def _is_raster_or_svg(raw: bytes) -> bool:
    head = raw[:16]
    return (head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8") or head.startswith(b"GIF8") or head.startswith(b"BM")
            or head[:4] in (b"II*\x00", b"MM\x00*") or raw[:200].lstrip().startswith((b"<?xml", b"<svg"))
            or (head.startswith(b"RIFF") and raw[8:12] == b"WEBP"))


def _convert_vector_image(raw: bytes) -> bytes | None:
    """EMF/WMF cannot be drawn by MuPDF; nothing sensible to do here."""
    return None


def _crop_image(data: bytes, crop: dict) -> bytes | None:
    try:
        pix = pymupdf.Pixmap(data)
        if pix.alpha == 0 and pix.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        w, h = pix.width, pix.height
        x0 = int(w * max(crop["l"], 0))
        y0 = int(h * max(crop["t"], 0))
        x1 = int(w * (1 - max(crop["r"], 0)))
        y1 = int(h * (1 - max(crop["b"], 0)))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return None
        out = pymupdf.Pixmap(pix, x1 - x0, y1 - y0, pymupdf.IRect(x0, y0, x1, y1)) if False else None
        # Pixmap(source, w, h, clip) rescales; copy the rectangle by hand instead
        irect = pymupdf.IRect(x0, y0, x1, y1)
        cropped = pymupdf.Pixmap(pix.colorspace, irect, pix.alpha)
        cropped.copy(pix, irect)
        return cropped.tobytes("png")
    except Exception:  # noqa: BLE001
        return None


def _vml_size(style: str):
    w = h = 0.0
    for part in style.split(";"):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        k, v = k.strip(), v.strip()
        if k in ("width", "height"):
            try:
                if v.endswith("pt"):
                    val = float(v[:-2])
                elif v.endswith("in"):
                    val = float(v[:-2]) * 72
                elif v.endswith("cm"):
                    val = float(v[:-2]) * 28.3465
                elif v.endswith("mm"):
                    val = float(v[:-2]) * 2.83465
                elif v.endswith("px"):
                    val = float(v[:-2]) * 0.75
                else:
                    val = float(v)
            except ValueError:
                continue
            if k == "width":
                w = val
            else:
                h = val
    return w, h


def convert(data: bytes, name: str = "") -> bytes:
    """Convert a .docx file's bytes into PDF bytes."""
    return DocxToPdf(data).convert()


class _Drawable:
    """An inline shape: drawn by calling its operations at the place the line puts it."""

    def __init__(self, ops):
        self.ops = ops

    def __call__(self, sheet, x, y):
        for op in self.ops:
            op(sheet, x, y)


def color_from_drawingml_safe(el, conv):
    from .ooxml import color_from_drawingml
    try:
        return color_from_drawingml(el, conv.theme)
    except Exception:  # noqa: BLE001
        return None


_VML_COLORS = {"white": (1, 1, 1), "black": (0, 0, 0), "red": (1, 0, 0), "blue": (0, 0, 1), "green": (0, 0.5, 0), "yellow": (1, 1, 0),
               "gray": (0.5, 0.5, 0.5), "silver": (0.75, 0.75, 0.75), "orange": (1, 0.65, 0), "lime": (0, 1, 0), "aqua": (0, 1, 1),
               "fuchsia": (1, 0, 1), "navy": (0, 0, 0.5), "teal": (0, 0.5, 0.5), "maroon": (0.5, 0, 0), "purple": (0.5, 0, 0.5), "olive": (0.5, 0.5, 0)}


def _vml_color(value):
    if not value:
        return None
    value = value.strip().split(" ")[0]
    if value.startswith("#"):
        return hex_color(value)
    return _VML_COLORS.get(value.lower())


def _vml_len(value, default=0.0):
    if value is None:
        return default
    value = str(value).strip()
    try:
        if value.endswith("pt"):
            return float(value[:-2])
        if value.endswith("in"):
            return float(value[:-2]) * 72
        if value.endswith("cm"):
            return float(value[:-2]) * 28.3465
        if value.endswith("mm"):
            return float(value[:-2]) * 2.83465
        if value.endswith("px"):
            return float(value[:-2]) * 0.75
        return float(value) * 0.75 if value not in ("", "0") else 0.0
    except ValueError:
        return default
