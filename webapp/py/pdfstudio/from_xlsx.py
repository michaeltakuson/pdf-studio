"""Excel (.xlsx) to PDF.

Prints each sheet the way Excel would: the used range (or the print area),
column widths and row heights, number formats, fonts, fills, borders, merged
cells, text overflow and wrapping, page setup (paper, orientation, scale,
fit to width, margins, repeated title rows, manual breaks, headers/footers),
pictures and charts.

Very large sheets are read in a streaming fashion and capped (see ``MAX_CELLS``
and ``MAX_PAGES``) so that the browser's memory is never exhausted; the cap is
reported in ``warnings``.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET

from . import typeset as T
from .numfmt import BUILTIN, format_value
from .ooxml import NS, Package, PackageError, Theme, apply_mods, emu, local
from .typeset import Atom, Sheet, Style, hex_color

S = NS["s"]
MAX_CELLS = 600_000
MAX_PAGES = 1500


def sq(tag):
    return "{%s}%s" % (S, tag)


INDEXED = [
    "000000", "FFFFFF", "FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF", "00FFFF", "000000", "FFFFFF", "FF0000", "00FF00", "0000FF",
    "FFFF00", "FF00FF", "00FFFF", "800000", "008000", "000080", "808000", "800080", "008080", "C0C0C0", "808080", "9999FF", "993366",
    "FFFFCC", "CCFFFF", "660066", "FF8080", "0066CC", "CCCCFF", "000080", "FF00FF", "FFFF00", "00FFFF", "800080", "800000", "008080",
    "0000FF", "00CCFF", "CCFFFF", "CCFFCC", "FFFF99", "99CCFF", "FF99CC", "CC99FF", "FFCC99", "3366FF", "33CCCC", "99CC00", "FFCC00",
    "FF9900", "FF6600", "666699", "969696", "003366", "339966", "003300", "333300", "993300", "993366", "333399", "333333",
]
THEME_INDEX = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"]

PAPERS = {1: (612, 792), 5: (612, 1008), 8: (841.9, 1190.6), 9: (595.3, 841.9), 11: (419.5, 595.3), 12: (728.5, 1031.8),
          13: (515.9, 728.5), 7: (522, 756), 3: (792, 1224), 10: (595.3, 841.9)}


def col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + ord(ch.upper()) - 64
        else:
            break
    return n - 1


_CELL_RE = re.compile(r"^\$?([A-Za-z]+)\$?(\d+)$")


def parse_ref(ref: str):
    m = _CELL_RE.match(ref.strip())
    if not m:
        return None
    return int(m.group(2)) - 1, col_index(m.group(1))


def parse_range(text: str):
    text = text.strip().replace("$", "")
    if ":" in text:
        a, b = text.split(":", 1)
    else:
        a = b = text
    pa, pb = parse_ref(a), parse_ref(b)
    if pa and pb:
        return pa[0], pa[1], pb[0], pb[1]
    # whole rows / columns: 1:3, A:C
    ma = re.match(r"^(\d+)$", a)
    mb = re.match(r"^(\d+)$", b)
    if ma and mb:
        return int(a) - 1, 0, int(b) - 1, 16383
    if re.match(r"^[A-Za-z]+$", a) and re.match(r"^[A-Za-z]+$", b):
        return 0, col_index(a), 1048575, col_index(b)
    return None


class Styles:
    def __init__(self, root, theme: Theme):
        self.theme = theme
        self.numfmts: dict[int, str] = {}
        self.fonts: list[dict] = []
        self.fills: list = []
        self.borders: list[dict] = []
        self.xfs: list[dict] = []
        self.dxfs: list[dict] = []
        if root is None:
            self.xfs = [{"numFmtId": 0, "fontId": 0, "fillId": 0, "borderId": 0, "align": {}}]
            self.fonts = [{"name": "Calibri", "size": 11.0, "b": False, "i": False, "u": None, "strike": False, "color": (0, 0, 0)}]
            return
        for nf in root.findall(f"{sq('numFmts')}/{sq('numFmt')}"):
            self.numfmts[int(nf.get("numFmtId"))] = nf.get("formatCode") or "General"
        for f in root.findall(f"{sq('fonts')}/{sq('font')}"):
            self.fonts.append(self._font(f))
        for f in root.findall(f"{sq('fills')}/{sq('fill')}"):
            self.fills.append(self._fill(f))
        for b in root.findall(f"{sq('borders')}/{sq('border')}"):
            self.borders.append(self._border(b))
        for xf in root.findall(f"{sq('cellXfs')}/{sq('xf')}"):
            al = xf.find(sq("alignment"))
            align = dict(al.attrib) if al is not None else {}
            self.xfs.append({"numFmtId": int(xf.get("numFmtId", "0")), "fontId": int(xf.get("fontId", "0")),
                             "fillId": int(xf.get("fillId", "0")), "borderId": int(xf.get("borderId", "0")), "align": align})
        for d in root.findall(f"{sq('dxfs')}/{sq('dxf')}"):
            font = d.find(sq("font"))
            fill = d.find(sq("fill"))
            self.dxfs.append({"font": self._font(font) if font is not None else None,
                              "fill": self._fill(fill) if fill is not None else None})
        if not self.xfs:
            self.xfs = [{"numFmtId": 0, "fontId": 0, "fillId": 0, "borderId": 0, "align": {}}]
        if not self.fonts:
            self.fonts = [{"name": "Calibri", "size": 11.0, "b": False, "i": False, "u": None, "strike": False, "color": (0, 0, 0)}]

    def color(self, el, default=None):
        if el is None:
            return default
        if el.get("rgb"):
            return hex_color(el.get("rgb"), default)
        if el.get("theme") is not None:
            try:
                name = THEME_INDEX[int(el.get("theme"))]
            except (ValueError, IndexError):
                return default
            tint = float(el.get("tint", "0") or 0)
            base = self.theme.color(name)
            return _tint(base, tint)
        if el.get("indexed") is not None:
            try:
                idx = int(el.get("indexed"))
            except ValueError:
                return default
            if idx == 64:
                return default
            if 0 <= idx < len(INDEXED):
                return _tint(hex_color(INDEXED[idx]), float(el.get("tint", "0") or 0))
        if el.get("auto") == "1":
            return default
        return default

    def _font(self, f):
        if f is None:
            return None
        name = f.find(sq("name"))
        sz = f.find(sq("sz"))
        u = f.find(sq("u"))
        return {
            "name": name.get("val") if name is not None else "Calibri",
            "size": float(sz.get("val")) if sz is not None else 11.0,
            "b": f.find(sq("b")) is not None and f.find(sq("b")).get("val") not in ("0", "false"),
            "i": f.find(sq("i")) is not None and f.find(sq("i")).get("val") not in ("0", "false"),
            "u": (u.get("val") or "single") if u is not None else None,
            "strike": f.find(sq("strike")) is not None and f.find(sq("strike")).get("val") not in ("0", "false"),
            "color": self.color(f.find(sq("color")), (0.0, 0.0, 0.0)),
        }

    def _fill(self, f):
        pf = f.find(sq("patternFill"))
        if pf is None:
            gf = f.find(sq("gradFill"))
            if gf is not None:
                stops = gf.findall(sq("stop"))
                if stops:
                    c = self.color(stops[0].find(sq("color")))
                    return c
            return None
        ptype = pf.get("patternType")
        if ptype in (None, "none"):
            return None
        fg = self.color(pf.find(sq("fgColor")), (0.0, 0.0, 0.0) if ptype == "solid" else None)
        bg = self.color(pf.find(sq("bgColor")))
        if ptype == "solid":
            return fg
        if fg is not None and bg is not None:
            return tuple((a + b) / 2 for a, b in zip(fg, bg))
        return fg

    def _border(self, b):
        out = {}
        for side in ("left", "right", "top", "bottom"):
            el = b.find(sq(side))
            if el is None and side in ("left", "right"):
                el = b.find(sq("start" if side == "left" else "end"))
            if el is not None and el.get("style"):
                out[side] = (el.get("style"), self.color(el.find(sq("color")), (0.0, 0.0, 0.0)))
        return out


def _tint(rgb, tint: float):
    if not tint:
        return rgb
    import colorsys
    h, l, s = colorsys.rgb_to_hls(*rgb)
    l = l * (1 + tint) if tint < 0 else l * (1 - tint) + (1 - (1 - tint))
    return colorsys.hls_to_rgb(h, min(1.0, max(0.0, l)), s)


BORDER_WIDTH = {"thin": 0.5, "hair": 0.25, "medium": 1.0, "thick": 1.75, "dashed": 0.5, "dotted": 0.5, "double": 0.5,
                "mediumDashed": 1.0, "dashDot": 0.5, "mediumDashDot": 1.0, "dashDotDot": 0.5, "mediumDashDotDot": 1.0,
                "slantDashDot": 1.0}
BORDER_DASH = {"dashed": "[3 2] 0", "dotted": "[1 1.5] 0", "mediumDashed": "[3 2] 0", "dashDot": "[3 1 1 1] 0",
               "mediumDashDot": "[3 1 1 1] 0", "dashDotDot": "[3 1 1 1 1 1] 0", "hair": "[1 1] 0"}


class SheetData:
    def __init__(self):
        self.cells: dict[int, dict[int, tuple]] = {}      # row -> col -> (kind, value, style)
        self.row_h: dict[int, float] = {}
        self.row_custom: set[int] = set()
        self.row_hidden: set[int] = set()
        self.col_w: dict[int, float] = {}
        self.col_hidden: set[int] = set()
        self.merges: list[tuple[int, int, int, int]] = []
        self.default_row_h = 15.0
        self.default_col_w = None
        self.base_col_w = None
        self.page = {}
        self.margins = {"left": 0.7, "right": 0.7, "top": 0.75, "bottom": 0.75, "header": 0.3, "footer": 0.3}
        self.fit = False
        self.print_opts = {}
        self.header_footer = {}
        self.row_breaks: list[int] = []
        self.col_breaks: list[int] = []
        self.hyperlinks: list[tuple] = []
        self.drawing_part: str | None = None
        self.cond: list = []
        self.truncated = False
        self.max_row = -1
        self.max_col = -1


class XlsxToPdf:
    def __init__(self, data: bytes):
        self.pkg = Package(data)
        if not self.pkg.has("xl/workbook.xml"):
            raise PackageError("Excel ブックとして読み込めませんでした（.xlsx 形式のみ対応しています。古い .xls は Excel で .xlsx として保存し直してください）")
        self.wb = self.pkg.xml("xl/workbook.xml")
        self.theme = Theme(self._rel_xml("theme") or self.pkg.xml("xl/theme/theme1.xml"))
        self.styles = Styles(self.pkg.xml(self._rel_target("styles") or "xl/styles.xml"), self.theme)
        self.shared = self._load_shared()
        wp = self.wb.find(sq("workbookPr"))
        self.date1904 = wp is not None and wp.get("date1904") in ("1", "true")
        self.warnings: list[str] = []
        self.sheets_out: list[Sheet] = []
        self.sheet_name = ""
        self.defined = self._defined_names()
        self.file_name = ""
        self._images: dict[str, bytes | None] = {}

    # ---- package helpers
    def _rel_target(self, kind):
        for target, k, _m in self.pkg.rels("xl/workbook.xml").values():
            if k == kind:
                return target
        return None

    def _rel_xml(self, kind):
        t = self._rel_target(kind)
        return self.pkg.xml(t) if t else None

    def _load_shared(self):
        part = self._rel_target("sharedStrings") or "xl/sharedStrings.xml"
        out = []
        if not self.pkg.has(part):
            return out
        raw = self.pkg.read(part)
        try:
            for ev, el in ET.iterparse(io.BytesIO(raw), events=("end",)):
                if el.tag == sq("si"):
                    out.append("".join(t.text or "" for t in el.iter(sq("t")) if not _in_phonetic(el, t)))
                    el.clear()
        except ET.ParseError:
            pass
        return out

    def _defined_names(self):
        out = {}
        dn = self.wb.find(sq("definedNames"))
        if dn is not None:
            for d in dn.findall(sq("definedName")):
                out[(d.get("name"), d.get("localSheetId"))] = d.text or ""
        return out

    # ---- converting
    def convert(self) -> bytes:
        sheets = self.wb.find(sq("sheets"))
        rels = self.pkg.rels("xl/workbook.xml")
        count = 0
        for idx, sh in enumerate(sheets):
            if sh.get("state") in ("hidden", "veryHidden"):
                continue
            rid = sh.get("{%s}id" % NS["r"])
            rel = rels.get(rid)
            if not rel or rel[1] != "worksheet":
                continue
            self.sheet_name = sh.get("name") or f"Sheet{idx + 1}"
            data = self._read_sheet(rel[0])
            self._print_sheet(data, idx, rel[0])
            count += 1
            if len(self.sheets_out) >= MAX_PAGES:
                self.warnings.append(f"ページ数が非常に多いため、{MAX_PAGES} ページまでで打ち切りました")
                break
        if not self.sheets_out:
            blank = Sheet(595.3, 841.9)
            self.sheets_out.append(blank)
        doc = T.render(self.sheets_out, fields={})
        data = doc.tobytes(garbage=3, deflate=True)
        doc.close()
        return data

    # ---------------------------------------------------------------- reading a sheet
    def _read_sheet(self, part: str) -> SheetData:
        sd = SheetData()
        raw = self.pkg.read(part)
        if raw is None:
            return sd
        cells_seen = 0
        row_idx = -1
        in_data = False
        stack_row = None
        try:
            it = ET.iterparse(io.BytesIO(raw), events=("start", "end"))
            cell = None
            for ev, el in it:
                tag = el.tag
                if ev == "start":
                    if tag == sq("row"):
                        r = el.get("r")
                        row_idx = int(r) - 1 if r else row_idx + 1
                        if el.get("ht"):
                            try:
                                sd.row_h[row_idx] = float(el.get("ht"))
                                if el.get("customHeight") in ("1", "true"):
                                    sd.row_custom.add(row_idx)
                            except ValueError:
                                pass
                        if el.get("hidden") in ("1", "true"):
                            sd.row_hidden.add(row_idx)
                    continue
                # end events
                if tag == sq("c"):
                    cells_seen += 1
                    ref = el.get("r")
                    col = col_index(ref) if ref else (sd.max_col + 1)
                    t = el.get("t") or "n"
                    s = int(el.get("s", "0") or 0)
                    v = el.find(sq("v"))
                    value = None
                    kind = "n"
                    if t == "s" and v is not None and v.text is not None:
                        try:
                            value = self.shared[int(v.text)]
                            kind = "s"
                        except (ValueError, IndexError):
                            value = None
                    elif t == "inlineStr":
                        is_el = el.find(sq("is"))
                        if is_el is not None:
                            value = "".join(x.text or "" for x in is_el.iter(sq("t")))
                            kind = "s"
                    elif t == "str" and v is not None:
                        value = v.text or ""
                        kind = "s"
                    elif t == "b" and v is not None:
                        value = "TRUE" if v.text == "1" else "FALSE"
                        kind = "b"
                    elif t == "e" and v is not None:
                        value = v.text or "#N/A"
                        kind = "e"
                    elif v is not None and v.text not in (None, ""):
                        try:
                            value = float(v.text)
                        except ValueError:
                            value = v.text
                            kind = "s"
                    if value is not None or s:
                        sd.cells.setdefault(row_idx, {})[col] = (kind, value, s)
                        if value is not None or self._has_visual(s):
                            sd.max_row = max(sd.max_row, row_idx)
                            sd.max_col = max(sd.max_col, col)
                    el.clear()
                    if cells_seen >= MAX_CELLS:
                        sd.truncated = True
                        self.warnings.append(f"シート「{self.sheet_name}」は非常に大きいため、先頭の約 {row_idx + 1:,} 行までを変換しました")
                        break
                elif tag == sq("row"):
                    el.clear() if False else None
                elif tag == sq("col"):
                    lo, hi = int(el.get("min", "1")) - 1, int(el.get("max", "1")) - 1
                    width = el.get("width")
                    for c in range(lo, min(hi, lo + 2000) + 1):
                        if width:
                            sd.col_w[c] = float(width)
                        if el.get("hidden") in ("1", "true"):
                            sd.col_hidden.add(c)
                elif tag == sq("sheetFormatPr"):
                    if el.get("defaultRowHeight"):
                        sd.default_row_h = float(el.get("defaultRowHeight"))
                    if el.get("defaultColWidth"):
                        sd.default_col_w = float(el.get("defaultColWidth"))
                    if el.get("baseColWidth"):
                        sd.base_col_w = float(el.get("baseColWidth"))
                elif tag == sq("mergeCell"):
                    rng = parse_range(el.get("ref") or "")
                    if rng:
                        sd.merges.append(rng)
                elif tag == sq("pageSetup"):
                    sd.page = dict(el.attrib)
                elif tag == sq("pageMargins"):
                    for k in sd.margins:
                        if el.get(k):
                            sd.margins[k] = float(el.get(k))
                elif tag == sq("pageSetUpPr"):
                    sd.fit = el.get("fitToPage") in ("1", "true")
                elif tag == sq("printOptions"):
                    sd.print_opts = dict(el.attrib)
                elif tag == sq("headerFooter"):
                    for child in el:
                        sd.header_footer[local(child.tag)] = child.text or ""
                elif tag == sq("brk"):
                    pass
                elif tag == sq("rowBreaks"):
                    sd.row_breaks = sorted(int(b.get("id", "0")) for b in el.findall(sq("brk")))
                elif tag == sq("colBreaks"):
                    sd.col_breaks = sorted(int(b.get("id", "0")) for b in el.findall(sq("brk")))
                elif tag == sq("hyperlink"):
                    sd.hyperlinks.append((el.get("ref"), el.get("{%s}id" % NS["r"]), el.get("location")))
                elif tag == sq("drawing"):
                    sd.drawing_rid = el.get("{%s}id" % NS["r"])
                elif tag == sq("conditionalFormatting"):
                    sd.cond.append((el.get("sqref"), [dict(r.attrib, formula=[(f.text or "") for f in r.findall(sq("formula"))]) for r in el.findall(sq("cfRule"))]))
        except ET.ParseError:
            self.warnings.append(f"シート「{self.sheet_name}」の一部を読み込めませんでした")
        sd.rels = self.pkg.rels(part)
        sd.part = part
        return sd

    def _has_visual(self, s: int) -> bool:
        if s <= 0 or s >= len(self.styles.xfs):
            return False
        xf = self.styles.xfs[s]
        return bool(self.styles.borders[xf["borderId"]] if xf["borderId"] < len(self.styles.borders) else False) or \
            bool(self.styles.fills[xf["fillId"]] if xf["fillId"] < len(self.styles.fills) else False)

    # ---------------------------------------------------------------- geometry
    def _col_width_pt(self, sd: SheetData, c: int) -> float:
        if c in sd.col_hidden:
            return 0.0
        w = sd.col_w.get(c)
        if w is None:
            w = sd.default_col_w if sd.default_col_w is not None else ((sd.base_col_w or 8.0) + 0.7109375)
        px = int(w * 7 + 5) if w > 0 else 0
        return px * 0.75

    def _text_for(self, sd: SheetData, cell):
        kind, value, s = cell
        xf = self.styles.xfs[s] if s < len(self.styles.xfs) else self.styles.xfs[0]
        code = self.styles.numfmts.get(xf["numFmtId"]) or BUILTIN.get(xf["numFmtId"], "General")
        if value is None:
            return "", None, False, xf
        if kind == "n":
            text, color, numeric = format_value(value, "n", code, self.date1904)
            return text, color, numeric, xf
        if kind == "b":
            return value, None, False, xf
        return str(value), None, False, xf

    def _cell_style(self, xf, color=None, size_scale=1.0):
        font = self.styles.fonts[xf["fontId"]] if xf["fontId"] < len(self.styles.fonts) else self.styles.fonts[0]
        return Style(family=font["name"], size=font["size"] * size_scale, bold=font["b"], italic=font["i"],
                     color=color or font["color"] or (0, 0, 0), underline=font["u"] if font["u"] not in (None, "none") else None,
                     strike=font["strike"])

    def _wrapped_height(self, sd, text, xf, width_pt):
        font = self.styles.fonts[xf["fontId"]] if xf["fontId"] < len(self.styles.fonts) else self.styles.fonts[0]
        st = self._cell_style(xf)
        atoms = T.atomize([("text", text, st)]) + [Atom("", st, 0.0, "mark", brk=True)]
        lines = T.break_lines(atoms, lambda i, l: (0.0, max(width_pt - 4.0, 6.0)), [], 36.0)
        total = 0.0
        for ln in lines:
            h, _b = T.line_metrics(ln, None, 0.0, "ppt")
            total += h
        return total + 3.0

    def _row_height(self, sd: SheetData, r: int, merged_skip: set) -> float:
        if r in sd.row_hidden:
            return 0.0
        if r in sd.row_h:
            return sd.row_h[r]
        return sd.default_row_h

    # ---------------------------------------------------------------- printing a sheet
    def _print_sheet(self, sd: SheetData, sheet_index: int, part: str):
        st = self.styles
        # print area
        area = None
        for (name, local_id), ref in self.defined.items():
            if name == "_xlnm.Print_Area" and local_id == str(sheet_index):
                first = ref.split(",")[0]
                if "!" in first:
                    first = first.rsplit("!", 1)[1]
                area = parse_range(first)
        titles_rows = None
        titles_cols = None
        for (name, local_id), ref in self.defined.items():
            if name == "_xlnm.Print_Titles" and local_id == str(sheet_index):
                for piece in ref.split(","):
                    rng = piece.rsplit("!", 1)[1] if "!" in piece else piece
                    pr = parse_range(rng)
                    if pr and pr[1] == 0 and pr[3] == 16383:
                        titles_rows = (pr[0], pr[2])
                    elif pr and pr[0] == 0 and pr[2] == 1048575:
                        titles_cols = (pr[1], pr[3])
        if area is None:
            if sd.max_row < 0:
                return
            area = (0, 0, sd.max_row, sd.max_col)
        # drawings extend the printed area
        drawings = self._read_drawings(sd)
        r0, c0, r1, c1 = area
        for d in drawings:
            r1 = max(r1, d["row1"]) if not area_is_explicit(area, sd) else r1
            c1 = max(c1, d["col1"]) if not area_is_explicit(area, sd) else c1
        r1 = min(r1, max(r0, sd.max_row + 400)) if not area_is_explicit(area, sd) else r1
        # merged cells map
        merged_top: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        merged_skip: set[tuple[int, int]] = set()
        for m in sd.merges:
            merged_top[(m[0], m[1])] = m
            if (m[2] - m[0] + 1) * (m[3] - m[1] + 1) < 5000:
                for rr in range(m[0], m[2] + 1):
                    for cc in range(m[1], m[3] + 1):
                        if (rr, cc) != (m[0], m[1]):
                            merged_skip.add((rr, cc))
        # sizes
        cols = [c for c in range(c0, c1 + 1)]
        col_w = {c: self._col_width_pt(sd, c) for c in cols}
        rows = [r for r in range(r0, r1 + 1) if r not in sd.row_hidden]
        row_h: dict[int, float] = {}
        for r in rows:
            h = sd.row_h.get(r, sd.default_row_h)
            if r not in sd.row_custom and r in sd.cells:
                for c, cell in sd.cells[r].items():
                    if c < c0 or c > c1 or (r, c) in merged_skip or (r, c) in merged_top:
                        continue
                    xf = st.xfs[cell[2]] if cell[2] < len(st.xfs) else st.xfs[0]
                    if xf["align"].get("wrapText") in ("1", "true") and cell[1] is not None:
                        text, _col, _num, _xf = self._text_for(sd, cell)
                        h = max(h, self._wrapped_height(sd, text, xf, col_w.get(c, 48.0)))
            row_h[r] = h
        total_w = sum(col_w[c] for c in cols if c not in sd.col_hidden)
        total_h = sum(row_h.values())
        # page geometry
        pg = sd.page
        paper = PAPERS.get(int(pg.get("paperSize", "9") or 9), PAPERS[9])
        landscape = pg.get("orientation") == "landscape"
        pw, ph = (paper[1], paper[0]) if landscape else paper
        m = sd.margins
        ml, mr, mt, mb = m["left"] * 72, m["right"] * 72, m["top"] * 72, m["bottom"] * 72
        avail_w, avail_h = pw - ml - mr, ph - mt - mb
        scale = int(pg.get("scale", "100") or 100) / 100.0
        if sd.fit:
            fw = int(pg.get("fitToWidth", "1") or 0) if pg.get("fitToWidth") != "0" else 0
            fh = int(pg.get("fitToHeight", "1") or 0) if pg.get("fitToHeight") != "0" else 0
            if pg.get("fitToWidth") is None:
                fw = 1
            if pg.get("fitToHeight") is None:
                fh = 1
            candidates = [1.0]
            if fw and total_w > 0:
                candidates.append(avail_w * fw / total_w)
            if fh and total_h > 0:
                candidates.append(avail_h * fh / total_h)
            scale = max(0.1, min(candidates))
        # repeated rows/cols
        repeat_rows = []
        if titles_rows:
            repeat_rows = [r for r in range(titles_rows[0], titles_rows[1] + 1) if r in row_h or r not in sd.row_hidden]
        repeat_h = sum(row_h.get(r, sd.row_h.get(r, sd.default_row_h)) for r in repeat_rows)
        # split into page chunks
        visible_cols = [c for c in cols if col_w[c] > 0 or True]
        col_chunks = self._chunk(visible_cols, lambda c: col_w[c], avail_w / scale, sd.col_breaks)
        row_chunks = self._chunk(rows, lambda r: row_h[r], avail_h / scale, [b for b in sd.row_breaks], repeat_h, repeat_rows)
        header = sd.header_footer
        pages_total_hint = len(col_chunks) * len(row_chunks)
        order_over_then_down = pg.get("pageOrder") == "overThenDown"
        chunks = [(rc, cc) for cc in col_chunks for rc in row_chunks] if not order_over_then_down else [(rc, cc) for rc in row_chunks for cc in col_chunks]
        for rc, cc in chunks:
            if len(self.sheets_out) >= MAX_PAGES:
                break
            # skip empty trailing chunks (no value and no format)
            if not self._chunk_has_content(sd, rc, cc):
                continue
            sheet = Sheet(pw, ph)
            self.sheets_out.append(sheet)
            self._draw_chunk(sheet, sd, rc, cc, col_w, row_h, scale, ml, mt, avail_w, avail_h, merged_top, merged_skip,
                             repeat_rows, drawings, area, len(self.sheets_out), sd.print_opts)
            self._header_footer(sheet, sd, pw, ph, ml, mr, mt, mb, m)

    @staticmethod
    def _chunk(items, size_of, limit, breaks, repeat_h=0.0, repeat_items=None):
        chunks = []
        cur = []
        used = 0.0
        break_set = set(breaks)
        for it in items:
            sz = size_of(it)
            if cur and used + sz > limit - (repeat_h if chunks or True else 0) + 0.01 and repeat_h == 0.0 or \
                    (cur and repeat_h and used + sz > limit - (repeat_h if cur[0] not in (repeat_items or []) else 0) + 0.01):
                chunks.append(cur)
                cur, used = [], 0.0
            cur.append(it)
            used += sz
            if it + 1 in break_set and cur:
                chunks.append(cur)
                cur, used = [], 0.0
        if cur:
            chunks.append(cur)
        return chunks or [[]]

    def _chunk_has_content(self, sd, rc, cc):
        if not rc or not cc:
            return False
        cset = set(cc)
        for r in rc:
            row = sd.cells.get(r)
            if row:
                for c, cell in row.items():
                    if c in cset and (cell[1] is not None or self._has_visual(cell[2])):
                        return True
        return False

    def _draw_chunk(self, sheet, sd, rc, cc, col_w, row_h, scale, ml, mt, avail_w, avail_h, merged_top, merged_skip,
                    repeat_rows, drawings, area, page_no, opts):
        st = self.styles
        s = scale
        # origin: centre horizontally/vertically when asked
        chunk_w = sum(col_w[c] for c in cc) * s
        ox = ml + ((avail_w - chunk_w) / 2 if opts.get("horizontalCentered") in ("1", "true") else 0.0)
        oy = mt
        rows_to_draw = list(rc)
        if repeat_rows and rc and rc[0] not in repeat_rows:
            rows_to_draw = [r for r in repeat_rows if r not in rc] + rows_to_draw
        colx = {}
        x = 0.0
        for c in cc:
            colx[c] = x
            x += col_w[c]
        rowy = {}
        y = 0.0
        for r in rows_to_draw:
            rowy[r] = y
            y += row_h.get(r, sd.row_h.get(r, sd.default_row_h))
        grid = opts.get("gridLines") in ("1", "true")
        # cell backgrounds first, then grid, text, borders
        cell_list = []
        for r in rows_to_draw:
            row = sd.cells.get(r, {})
            for c in cc:
                cell = row.get(c)
                if (r, c) in merged_top:
                    mg = merged_top[(r, c)]
                else:
                    mg = None
                if cell is None and mg is None:
                    continue
                if (r, c) in merged_skip:
                    continue
                cell_list.append((r, c, cell or ("n", None, 0), mg))
        occupied = {(r, c) for r, c, cell, mg in cell_list if cell[1] is not None}
        def span_rect(r, c, mg):
            if mg is None:
                return colx[c], rowy[r], colx[c] + col_w[c], rowy[r] + row_h.get(r, sd.default_row_h)
            x0 = colx.get(mg[1], colx[c])
            y0 = rowy[r]
            x1 = x0 + sum(col_w.get(k, 0) for k in range(mg[1], mg[3] + 1) if k in colx)
            y1 = y0 + sum(row_h.get(k, sd.default_row_h) for k in range(mg[0], mg[2] + 1) if k in rowy)
            return x0, y0, x1, y1
        # fills
        for r, c, cell, mg in cell_list:
            xf = st.xfs[cell[2]] if cell[2] < len(st.xfs) else st.xfs[0]
            fill = st.fills[xf["fillId"]] if xf["fillId"] < len(st.fills) else None
            if fill is not None:
                x0, y0, x1, y1 = span_rect(r, c, mg)
                sheet.rect(ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s, fill=fill)
        # conditional formatting fills (simple rules)
        self._conditional(sheet, sd, rows_to_draw, cc, colx, rowy, col_w, row_h, s, ox, oy)
        if grid:
            for c in cc:
                sheet.line(ox + colx[c] * s, oy, ox + colx[c] * s, oy + y * s, (0.8, 0.8, 0.8), 0.25)
            sheet.line(ox + x * s, oy, ox + x * s, oy + y * s, (0.8, 0.8, 0.8), 0.25)
            for r in rows_to_draw:
                sheet.line(ox, oy + rowy[r] * s, ox + x * s, oy + rowy[r] * s, (0.8, 0.8, 0.8), 0.25)
            sheet.line(ox, oy + y * s, ox + x * s, oy + y * s, (0.8, 0.8, 0.8), 0.25)
        # text
        link_map = self._link_map(sd)
        for r, c, cell, mg in cell_list:
            if cell[1] is None:
                continue
            text, color, numeric, xf = self._text_for(sd, cell)
            if text == "":
                continue
            x0, y0, x1, y1 = span_rect(r, c, mg)
            self._draw_text(sheet, sd, text, color, numeric, xf, cell, x0, y0, x1, y1, r, c, mg, cc, rows_to_draw, colx, rowy,
                            col_w, occupied, s, ox, oy)
            ref = (r, c)
            if ref in link_map:
                sheet.link(ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s, link_map[ref])
        # borders
        for r, c, cell, mg in cell_list:
            xf = st.xfs[cell[2]] if cell[2] < len(st.xfs) else st.xfs[0]
            b = st.borders[xf["borderId"]] if xf["borderId"] < len(st.borders) else {}
            if not b:
                continue
            x0, y0, x1, y1 = span_rect(r, c, mg)
            X0, Y0, X1, Y1 = ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s
            for side, (x_a, y_a, x_b, y_b) in (("top", (X0, Y0, X1, Y0)), ("bottom", (X0, Y1, X1, Y1)),
                                                ("left", (X0, Y0, X0, Y1)), ("right", (X1, Y0, X1, Y1))):
                spec = b.get(side)
                if not spec:
                    continue
                style, color = spec
                sheet.line(x_a, y_a, x_b, y_b, color or (0, 0, 0), max(0.3, BORDER_WIDTH.get(style, 0.5) * s), BORDER_DASH.get(style))
                if style == "double":
                    off = 1.5 * s
                    if side in ("top", "bottom"):
                        sheet.line(x_a, y_a + off, x_b, y_b + off, color or (0, 0, 0), 0.4 * s)
                    else:
                        sheet.line(x_a + off, y_a, x_b + off, y_b, color or (0, 0, 0), 0.4 * s)
        # pictures and charts
        for d in drawings:
            if d["row0"] in rowy and d["col0"] in colx:
                self._draw_drawing(sheet, d, colx, rowy, col_w, row_h, sd, s, ox, oy)

    def _draw_text(self, sheet, sd, text, color, numeric, xf, cell, x0, y0, x1, y1, r, c, mg, cc, rows_to_draw, colx, rowy,
                   col_w, occupied, s, ox, oy):
        al = xf["align"]
        wrap = al.get("wrapText") in ("1", "true")
        horiz = al.get("horizontal") or "general"
        vert = al.get("vertical") or "bottom"
        if horiz == "general":
            horiz = "right" if (cell[0] == "n" and numeric) else ("center" if cell[0] in ("b", "e") else "left")
        font_scale = s
        st = self._cell_style(xf, color, font_scale)
        indent = float(al.get("indent", "0") or 0) * 9.0 * s
        pad = 2.0 * s
        width = (x1 - x0) * s
        height = (y1 - y0) * s
        # shrink to fit
        if al.get("shrinkToFit") in ("1", "true") and not wrap:
            tw = T.text_width(text, st)
            if tw > width - 2 * pad and tw > 0:
                f = max(0.3, (width - 2 * pad) / tw)
                st = Style(**{**st.__dict__, "size": st.size * f})
        X0 = ox + x0 * s
        Y0 = oy + y0 * s
        if cell[0] == "n" and numeric and T.text_width(text, st) > width - 2 * pad and not wrap and not al.get("shrinkToFit"):
            # numbers that do not fit show as ####
            text = "#" * max(1, int((width - 2 * pad) / max(T.char_width("#", st), 0.1)))
        if wrap:
            atoms = T.atomize([("text", text, st)]) + [Atom("", st, 0.0, "mark", brk=True)]
            avail = max(width - 2 * pad - indent, 4.0)
            lines = T.break_lines(atoms, lambda i, l: (0.0, avail), [], 36.0)
            heights = []
            for ln in lines:
                T.place_line(ln, {"left": "left", "right": "right", "center": "center", "justify": "both", "distributed": "distribute"}.get(horiz, "left"), avail)
                h, b = T.line_metrics(ln, None, 0.0, "ppt")
                heights.append((h, b))
            total = sum(h for h, _ in heights)
            top = Y0 + (height - total) / 2 if vert == "center" else (Y0 + height - total - pad * 0.5 if vert == "bottom" else Y0 + pad * 0.5)
            yy = max(top, Y0) if total <= height else Y0
            for ln, (h, b) in zip(lines, heights):
                if yy + h > Y0 + height + 0.5 and yy > Y0:
                    break
                T.emit_line(sheet, ln, X0 + pad + indent, yy + b, yy, h)
                yy += h
            return
        # single line: overflow into empty neighbours for text
        tw = T.text_width(text, st)
        left_limit, right_limit = X0, X0 + width
        if tw > width - 2 * pad and not numeric:
            if horiz in ("left", "center", "general"):
                k = (mg[3] if mg else c) + 1
                while k in colx and (r, k) not in occupied and (colx[k] + 0) >= 0 and right_limit < ox + sum(col_w[q] for q in cc) * s + ox - ox + 1e9:
                    right_limit += col_w[k] * s
                    k += 1
                    if right_limit - X0 > tw + 2 * pad:
                        break
            if horiz in ("right", "center"):
                k = c - 1
                while k in colx and (r, k) not in occupied:
                    left_limit -= col_w[k] * s
                    k -= 1
                    if X0 + width - left_limit > tw + 2 * pad:
                        break
        avail = (right_limit - left_limit) - 2 * pad
        # truncate what cannot be shown
        shown = text
        if tw > avail:
            acc = 0.0
            out = ""
            for ch in text:
                cw = T.char_width(ch, st)
                if acc + cw > avail:
                    break
                acc += cw
                out += ch
            shown = out
            tw = acc
        if horiz == "right":
            x = (X0 + width) - pad - tw - indent * 0
        elif horiz == "center":
            x = left_limit + ((right_limit - left_limit) - tw) / 2
        else:
            x = X0 + pad + indent
        size = st.size
        line_h = size * T.line_factor(st.family, "ppt")
        if vert == "top":
            base = Y0 + pad * 0.5 + (line_h - size) / 2 + size * 0.88
        elif vert == "center":
            base = Y0 + (height - line_h) / 2 + (line_h - size) / 2 + size * 0.88
        else:
            base = Y0 + height - pad * 0.5 - line_h + (line_h - size) / 2 + size * 0.88
        base = max(base, Y0 + size * 0.9)
        # draw through the line machinery so underline and strike-through work
        ln = T.Line([Atom(shown, st, tw, "text")], width=tw)
        ln.atoms[0].x = 0.0
        T.emit_line(sheet, ln, x, base, base - size * 0.88 - (line_h - size) / 2, line_h)

    def _conditional(self, sheet, sd, rows, cols, colx, rowy, col_w, row_h, s, ox, oy):
        """The common conditional formats: cell value comparisons and colour scales are not drawn; simple 'cellIs' and
        'containsText' rules with a fill are."""
        st = self.styles
        for sqref, rules in sd.cond:
            for rule in rules:
                dxf_id = rule.get("dxfId")
                if dxf_id is None or int(dxf_id) >= len(st.dxfs):
                    continue
                dxf = st.dxfs[int(dxf_id)]
                fill = dxf["fill"]
                if fill is None:
                    continue
                typ = rule.get("type")
                op = rule.get("operator")
                formula = (rule.get("formula") or [""])[0]
                for part in (sqref or "").split():
                    rng = parse_range(part)
                    if not rng:
                        continue
                    for r in rows:
                        if r < rng[0] or r > rng[2]:
                            continue
                        for c in cols:
                            if c < rng[1] or c > rng[3]:
                                continue
                            cell = sd.cells.get(r, {}).get(c)
                            if cell is None or cell[1] is None:
                                continue
                            if self._cond_match(typ, op, formula, cell, rule):
                                x0, y0 = colx[c], rowy[r]
                                sheet.rect(ox + x0 * s, oy + y0 * s, ox + (x0 + col_w[c]) * s,
                                           oy + (y0 + row_h.get(r, sd.default_row_h)) * s, fill=fill)

    @staticmethod
    def _cond_match(typ, op, formula, cell, rule) -> bool:
        value = cell[1]
        try:
            if typ == "cellIs":
                ref = float(formula)
                if not isinstance(value, (int, float)):
                    return False
                return {"greaterThan": value > ref, "lessThan": value < ref, "equal": value == ref, "notEqual": value != ref,
                        "greaterThanOrEqual": value >= ref, "lessThanOrEqual": value <= ref}.get(op, False)
            if typ == "containsText":
                return (rule.get("text") or "") in str(value)
        except ValueError:
            return False
        return False

    def _link_map(self, sd):
        out = {}
        for ref, rid, location in sd.hyperlinks:
            pos = parse_ref(ref.split(":")[0]) if ref else None
            if not pos:
                continue
            if rid and rid in sd.rels and sd.rels[rid][2] == "External":
                out[pos] = "uri:" + sd.rels[rid][0]
        return out

    # ---------------------------------------------------------------- headers and footers
    def _header_footer(self, sheet, sd, pw, ph, ml, mr, mt, mb, m):
        hf = sd.header_footer
        total = len(self.sheets_out)
        for key, y, kind in (("oddHeader", m["header"] * 72, "h"), ("oddFooter", ph - m["footer"] * 72, "f")):
            code = hf.get(key)
            if not code:
                continue
            parts = _parse_hf(code)
            for pos, (text, size, bold) in parts.items():
                text = text.replace("&P", "{PAGE}").replace("&N", "{NUMPAGES}").replace("&A", self.sheet_name) \
                    .replace("&F", self.file_name).replace("&D", "").replace("&T", "")
                if not text.strip():
                    continue
                st = Style(family="Calibri", size=size or 10.0, bold=bold)
                tw = T.text_width(text.replace("{PAGE}", "00").replace("{NUMPAGES}", "00"), st)
                if pos == "L":
                    x = ml
                elif pos == "R":
                    x = pw - mr - tw
                else:
                    x = (pw - tw) / 2
                base = y + (size or 10) * 0.88 if kind == "h" else y
                sheet.text(x, base, text, st)

    # ---------------------------------------------------------------- drawings
    def _read_drawings(self, sd: SheetData):
        out = []
        rid = getattr(sd, "drawing_rid", None)
        if not rid or rid not in sd.rels:
            return out
        part = sd.rels[rid][0]
        root = self.pkg.xml(part)
        if root is None:
            return out
        rels = self.pkg.rels(part)
        xdr = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
        for anchor in root:
            tag = local(anchor.tag)
            if tag not in ("twoCellAnchor", "oneCellAnchor"):
                continue
            frm = anchor.find("{%s}from" % xdr)
            to = anchor.find("{%s}to" % xdr)
            ext = anchor.find("{%s}ext" % xdr)
            if frm is None:
                continue

            def pos(el):
                return (int(el.find("{%s}row" % xdr).text), emu(el.find("{%s}rowOff" % xdr).text),
                        int(el.find("{%s}col" % xdr).text), emu(el.find("{%s}colOff" % xdr).text))

            r0, ro0, c0, co0 = pos(frm)
            info = {"row0": r0, "roff0": ro0, "col0": c0, "coff0": co0, "row1": r0, "col1": c0, "kind": None}
            if to is not None:
                r1, ro1, c1, co1 = pos(to)
                info.update(row1=r1, roff1=ro1, col1=c1, coff1=co1)
            elif ext is not None:
                info.update(w=emu(ext.get("cx")), h=emu(ext.get("cy")))
            pic = anchor.find("{%s}pic" % xdr)
            gf = anchor.find("{%s}graphicFrame" % xdr)
            if pic is not None:
                blip = pic.find(".//a:blip", NS)
                rid2 = blip.get("{%s}embed" % NS["r"]) if blip is not None else None
                rel = rels.get(rid2)
                if rel:
                    data = self.pkg.read(rel[0])
                    from .from_docx import _is_raster_or_svg
                    if data is not None and _is_raster_or_svg(data):
                        info["kind"] = "pic"
                        info["data"] = data
            elif gf is not None:
                ch = gf.find(".//{%s}chart" % NS["c"])
                if ch is not None:
                    rel = rels.get(ch.get("{%s}id" % NS["r"]))
                    if rel:
                        info["kind"] = "chart"
                        info["chart"] = self.pkg.xml(rel[0])
            if info["kind"]:
                out.append(info)
        return out

    def _draw_drawing(self, sheet, d, colx, rowy, col_w, row_h, sd, s, ox, oy):
        x0 = colx[d["col0"]] + d["coff0"]
        y0 = rowy[d["row0"]] + d["roff0"]
        if "w" in d:
            x1, y1 = x0 + d["w"], y0 + d["h"]
        else:
            if d["col1"] in colx:
                x1 = colx[d["col1"]] + d.get("coff1", 0.0)
            else:
                x1 = x0 + 100
            if d["row1"] in rowy:
                y1 = rowy[d["row1"]] + d.get("roff1", 0.0)
            else:
                y1 = y0 + 100
        X0, Y0, X1, Y1 = ox + x0 * s, oy + y0 * s, ox + x1 * s, oy + y1 * s
        if d["kind"] == "pic":
            sheet.image(X0, Y0, X1, Y1, d["data"])
        elif d["kind"] == "chart" and d.get("chart") is not None:
            from . import charts
            from .drawingml import Ctx
            charts.draw_chart(sheet, d["chart"], X0, Y0, X1 - X0, Y1 - Y0, Ctx(self.theme))


def area_is_explicit(area, sd) -> bool:
    return not (area == (0, 0, sd.max_row, sd.max_col))


def _in_phonetic(si, t) -> bool:
    return False


def _parse_hf(code: str):
    """&L left &C centre &R right with &"font" &nn size &B bold toggles -> {pos: (text, size, bold)}."""
    parts = {"L": ["", None, False], "C": ["", None, False], "R": ["", None, False]}
    pos = "C"
    i = 0
    while i < len(code):
        ch = code[i]
        if ch == "&" and i + 1 < len(code):
            nxt = code[i + 1]
            if nxt in "LCR":
                pos = nxt
                i += 2
                continue
            if nxt == '"':
                j = code.find('"', i + 2)
                i = (j + 1) if j > 0 else i + 2
                continue
            if nxt.isdigit():
                j = i + 1
                while j < len(code) and code[j].isdigit():
                    j += 1
                parts[pos][1] = float(code[i + 1:j])
                i = j
                continue
            if nxt == "B":
                parts[pos][2] = not parts[pos][2]
                i += 2
                continue
            if nxt in "PNADTF":
                parts[pos][0] += "&" + nxt
                i += 2
                continue
            if nxt == "&":
                parts[pos][0] += "&"
                i += 2
                continue
            i += 2
            continue
        parts[pos][0] += ch
        i += 1
    return {k: tuple(v) for k, v in parts.items()}


def convert(data: bytes, name: str = "") -> tuple[bytes, list[str]]:
    conv = XlsxToPdf(data)
    conv.file_name = name
    pdf = conv.convert()
    return pdf, conv.warnings
