"""PDF to Excel (.xlsx).

Tables are rebuilt as cells (ruled tables from their lines, unruled ones from
the way the text lines up), merged cells and cell colours are kept, and numbers
and dates become real numbers and dates when asked. Text that is not in a
table goes into the sheet one line per row.

``per_page``   one sheet per page (default)
``single``     everything on one sheet, page after page
``tables``     only the tables, one sheet each
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from xml.sax.saxutils import escape

import pymupdf

from .pdf_analyze import Block, Line, PageInfo, Span, analyze_page, cell_lines, inside, line_text, merge_same_baseline, word_font

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def esc(text: str) -> str:
    return escape(_CTRL.sub("", text), {'"': "&quot;"})


def col_letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


# ============================================================================ value recognition

_NUM = re.compile(r"^[\s]*[(（]?[-−△▲+]?[¥￥$€£]?\s*[-−+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:円|%|％|千円|百万円)?[)）]?\s*$")
_PLAIN = re.compile(r"^[-−+]?\d+(?:\.\d+)?$")
_DATE = re.compile(r"^(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})日?$")


def recognise(text: str):
    """(value, number format) for a cell's text: a float or datetime when it is clearly one, else (None, None)."""
    t = text.strip()
    if not t or len(t) > 24:
        return None, None
    m = _DATE.match(t)
    if m:
        try:
            d = _dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d, "yyyy/m/d"
        except ValueError:
            return None, None
    if _PLAIN.match(t):
        digits = t.lstrip("+-−").split(".")[0]
        if len(digits) > 1 and digits.startswith("0") and "." not in t:
            return None, None        # codes like 007
        if len(digits) > 15:
            return None, None
        try:
            return float(t.replace("−", "-")), ("0.00" if "." in t and len(t.split(".")[1]) > 0 else "0") if "." in t else "General"
        except ValueError:
            return None, None
    if not _NUM.match(t):
        return None, None
    negative = t.startswith(("(", "（")) or "△" in t or "▲" in t or re.match(r"^\s*[-−]", t.lstrip("(（¥￥$€£ ")) is not None or t.lstrip().startswith(("-", "−"))
    percent = "%" in t or "％" in t
    yen = "円" in t or "¥" in t or "￥" in t
    core = re.sub(r"[^\d.]", "", t)
    if not core or core.count(".") > 1:
        return None, None
    try:
        v = float(core)
    except ValueError:
        return None, None
    if "千円" in t:
        v *= 1000
    elif "百万円" in t:
        v *= 1_000_000
    if percent:
        v /= 100
    if negative:
        v = -v
    decimals = len(core.split(".")[1]) if "." in core else 0
    if percent:
        fmt = "0" + ("." + "0" * decimals if decimals else "") + "%"
    else:
        grouped = "," in t
        base = ("#,##0" if grouped else "0") + ("." + "0" * decimals if decimals else "")
        fmt = f'"¥"{base}' if yen and "円" not in t else (f'{base}"円"' if "円" in t and "千" not in t and "百" not in t else base)
    return v, fmt


# ============================================================================ the workbook writer


class Styles:
    def __init__(self):
        self.fonts: list[tuple] = [("游ゴシック", 11.0, False, False, "000000")]
        self.fills: list[str | None] = [None, None]       # 0 none, 1 gray125 (reserved)
        self.borders: list[bool] = [False, True]
        self.numfmts: list[str] = []
        self.xfs: list[tuple] = [(0, 0, 0, 0, None, None, False)]
        self._xf_index: dict = {self.xfs[0]: 0}

    def _idx(self, lst, item):
        if item in lst:
            return lst.index(item)
        lst.append(item)
        return len(lst) - 1

    def style(self, font=None, fill=None, border=False, numfmt=None, halign=None, valign=None, wrap=False) -> int:
        fi = self._idx(self.fonts, font) if font else 0
        fl = self._idx(self.fills, fill) if fill else 0
        bi = 1 if border else 0
        nf = 0
        if numfmt and numfmt != "General":
            builtin = {"0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10}.get(numfmt)
            if builtin:
                nf = builtin
            else:
                if numfmt not in self.numfmts:
                    self.numfmts.append(numfmt)
                nf = 164 + self.numfmts.index(numfmt)
        key = (fi, fl, bi, nf, halign, valign, wrap)
        if key not in self._xf_index:
            self.xfs.append(key)
            self._xf_index[key] = len(self.xfs) - 1
        return self._xf_index[key]

    def xml(self) -> str:
        out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
        if self.numfmts:
            out.append(f'<numFmts count="{len(self.numfmts)}">' + "".join(
                f'<numFmt numFmtId="{164 + i}" formatCode="{esc(c)}"/>' for i, c in enumerate(self.numfmts)) + "</numFmts>")
        out.append(f'<fonts count="{len(self.fonts)}">')
        for name, size, bold, italic, color in self.fonts:
            out.append("<font>" + ("<b/>" if bold else "") + ("<i/>" if italic else "") + f'<sz val="{size:g}"/><color rgb="FF{color}"/><name val="{esc(name)}"/><family val="3"/><charset val="128"/></font>')
        out.append("</fonts>")
        out.append(f'<fills count="{len(self.fills)}"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>')
        for color in self.fills[2:]:
            out.append(f'<fill><patternFill patternType="solid"><fgColor rgb="FF{color}"/><bgColor indexed="64"/></patternFill></fill>')
        out.append("</fills>")
        thin = "".join(f'<{s} style="thin"><color auto="1"/></{s}>' for s in ("left", "right", "top", "bottom"))
        out.append('<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
                   f"<border>{thin}<diagonal/></border></borders>")
        out.append('<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>')
        out.append(f'<cellXfs count="{len(self.xfs)}">')
        for fi, fl, bi, nf, ha, va, wrap in self.xfs:
            attrs = f'numFmtId="{nf}" fontId="{fi}" fillId="{fl}" borderId="{bi}" xfId="0"'
            if nf:
                attrs += ' applyNumberFormat="1"'
            if fi:
                attrs += ' applyFont="1"'
            if fl:
                attrs += ' applyFill="1"'
            if bi:
                attrs += ' applyBorder="1"'
            align = ""
            if ha or va or wrap:
                align = "<alignment" + (f' horizontal="{ha}"' if ha else "") + (f' vertical="{va}"' if va else "") + (' wrapText="1"' if wrap else "") + "/>"
                attrs += ' applyAlignment="1"'
            out.append(f"<xf {attrs}>{align}</xf>" if align else f"<xf {attrs}/>")
        out.append('</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        return "".join(out)


class SheetBuilder:
    def __init__(self, name: str):
        self.name = name
        self.cells: dict[tuple[int, int], tuple] = {}     # (r, c) -> (kind, value, style)
        self.merges: list[tuple[int, int, int, int]] = []
        self.col_w: dict[int, float] = {}
        self.row_h: dict[int, float] = {}
        self.links: list[tuple[str, str]] = []
        self.landscape = False
        self.next_row = 0

    def set(self, r, c, kind, value, style):
        self.cells[(r, c)] = (kind, value, style)


def safe_sheet_name(name: str, taken: set) -> str:
    name = re.sub(r"[\[\]:*?/\\]", "_", name).strip("' ")[:31] or "Sheet"
    base, n = name, 2
    while name.lower() in taken:
        suffix = f"_{n}"
        name = base[: 31 - len(suffix)] + suffix
        n += 1
    taken.add(name.lower())
    return name


def excel_date(d: _dt.datetime) -> float:
    delta = d - _dt.datetime(1899, 12, 30)
    return delta.days + delta.seconds / 86400.0


# ============================================================================ page to rows


class XlsxFromPdf:
    def __init__(self, doc: pymupdf.Document, mode: str = "per_page", pages: list[int] | None = None, numbers: bool = True):
        self.doc = doc
        self.mode = mode
        self.pages = pages if pages is not None else list(range(doc.page_count))
        self.numbers = numbers
        self.styles = Styles()
        self.sheets: list[SheetBuilder] = []
        self.warnings: list[str] = []
        self._taken: set = set()
        self._single: SheetBuilder | None = None

    def _font_of(self, sp: Span | None):
        if sp is None:
            return None
        return (word_font(sp.font), round(max(6.0, min(sp.size, 36.0)) * 2) / 2, sp.bold, sp.italic, sp.color or "000000")

    def convert(self) -> bytes:
        for pno in self.pages:
            page = self.doc[pno]
            info = analyze_page(page)
            self._page(page, pno, info)
        if not self.sheets:
            sheet = SheetBuilder(safe_sheet_name("Sheet1", self._taken))
            self.sheets.append(sheet)
            self.warnings.append("表や文字が見つからなかったため、空のシートを作りました。スキャンした文書は先に文字認識（OCR）をしてください")
        return self._package()

    def _sheet_for(self, pno: int, info: PageInfo, tag: str = "") -> SheetBuilder:
        if self.mode == "single":
            if self._single is None:
                self._single = SheetBuilder(safe_sheet_name("Sheet1", self._taken))
                self.sheets.append(self._single)
            return self._single
        sheet = SheetBuilder(safe_sheet_name(f"ページ{pno + 1}{tag}", self._taken))
        sheet.landscape = info.width > info.height
        self.sheets.append(sheet)
        return sheet

    def _page(self, page, pno, info: PageInfo):
        table_boxes = [t["bbox"] for t in info.tables]
        items: list[tuple[float, str, object]] = []       # (y, kind, payload)
        for t in info.tables:
            items.append((t["bbox"][1], "table", t))
        free_lines: list[Line] = []
        for block in info.blocks:
            if any(inside(block.bbox, tb, 0.5) for tb in table_boxes):
                continue
            for ln in block.lines:
                if any(inside(ln.bbox, tb, 0.5) for tb in table_boxes):
                    continue
                if line_text(ln).strip():
                    free_lines.append(ln)
        free_lines = merge_same_baseline(sorted(free_lines, key=lambda l: (l.bbox[1], l.bbox[0])))
        for ln in free_lines:
            items.append((ln.bbox[1], "line", ln))
        items.sort(key=lambda it: it[0])
        if self.mode == "tables":
            tables = [it[2] for it in items if it[1] == "table"]
            text_tables = self._text_tables(free_lines)
            for i, t in enumerate(tables):
                sheet = self._sheet_for(pno, info, f"-表{i + 1}")
                self._write_table(sheet, t, page, info, 0)
            for i, tt in enumerate(text_tables):
                sheet = self._sheet_for(pno, info, f"-表{len(tables) + i + 1}")
                self._write_text_table(sheet, tt, 0)
            return
        sheet = self._sheet_for(pno, info)
        row = sheet.next_row
        if self.mode == "single" and row:
            row += 1
            sheet.set(row, 0, "s", f"— ページ {pno + 1} —", self.styles.style(font=("游ゴシック", 9.0, False, True, "808080")))
            row += 1
        # consecutive tabular lines are laid out as a table of their own
        groups = self._group_items(items)
        for kind, payload in groups:
            if kind == "table":
                row = self._write_table(sheet, payload, page, info, row) + 1
            elif kind == "texttable":
                row = self._write_text_table(sheet, payload, row) + 1
            else:
                ln = payload
                sp = next((s for s in ln.spans if s.text.strip()), None)
                text = "".join(s.text for s in ln.spans).replace("\t", " ").strip()
                style = self.styles.style(font=self._font_of(sp))
                sheet.set(row, 0, "s", text, style)
                if sp is not None and sp.link:
                    sheet.links.append((f"A{row + 1}", sp.link))
                sheet.row_h[row] = max(15.0, (ln.bbox[3] - ln.bbox[1]) * 1.15)
                sheet.col_w[0] = max(sheet.col_w.get(0, 8.0), 8.0)
                row += 1
        sheet.next_row = row

    def _group_items(self, items):
        out = []
        buf: list[Line] = []

        def flush():
            nonlocal buf
            if len(buf) >= 2:
                out.append(("texttable", buf))
            else:
                out.extend(("line", b) for b in buf)
            buf = []

        for _y, kind, payload in items:
            if kind == "table":
                flush()
                out.append(("table", payload))
            else:
                ln = payload
                pieces = sum(1 for s in ln.spans if s.text == "\t")
                if pieces >= 1:
                    buf.append(ln)
                else:
                    flush()
                    out.append(("line", ln))
        flush()
        return out

    def _text_tables(self, lines):
        tabular = [l for l in lines if any(s.text == "\t" for s in l.spans)]
        return [tabular] if len(tabular) >= 2 else []

    # ---- ruled tables
    def _write_table(self, sheet: SheetBuilder, t: dict, page, info: PageInfo, row0: int) -> int:
        cells = [c for c in t["cells"] if c is not None]
        if not cells:
            return row0
        xs = _merge_close(sorted({round(v, 1) for c in cells for v in (c[0], c[2])}))
        ys = _merge_close(sorted({round(v, 1) for c in cells for v in (c[1], c[3])}))
        n_c, n_r = len(xs) - 1, len(ys) - 1
        if n_c < 1 or n_r < 1:
            return row0

        def idx(vals, v):
            return min(range(len(vals)), key=lambda i: abs(vals[i] - v))

        placed: dict[tuple[int, int], dict] = {}
        covered = set()
        for c in cells:
            c0, c1 = idx(xs, c[0]), idx(xs, c[2])
            r0, r1 = idx(ys, c[1]), idx(ys, c[3])
            if c1 <= c0 or r1 <= r0 or (r0, c0) in placed:
                continue
            placed[(r0, c0)] = {"c0": c0, "c1": c1, "r0": r0, "r1": r1, "bbox": c}
            for rr in range(r0, r1):
                for cc in range(c0, c1):
                    if (rr, cc) != (r0, c0):
                        covered.add((rr, cc))
        lines_by_cell = cell_lines(info.blocks, placed, t["bbox"])
        from .to_docx import _cell_fills
        fills = _cell_fills(page, placed)
        for key, cell in placed.items():
            r, c = key
            lines = sorted(lines_by_cell.get(key, []), key=lambda l: (round(l.bbox[1]), l.bbox[0]))
            text = "\n".join(line_text(l).replace("\t", " ").strip() for l in lines).strip()
            sp = next((s for l in lines for s in l.spans if s.text.strip()), None)
            fill = fills.get(key)
            value, fmt = (recognise(text) if self.numbers and "\n" not in text else (None, None))
            cb = cell["bbox"]
            halign = None
            if lines:
                lx0 = min(l.bbox[0] for l in lines)
                lx1 = max(l.bbox[2] for l in lines)
                cw = cb[2] - cb[0]
                if abs((lx0 + lx1) / 2 - (cb[0] + cb[2]) / 2) < 3 and (lx1 - lx0) < cw * 0.85:
                    halign = "center"
                elif cb[2] - lx1 < 4 and lx0 - cb[0] > cw * 0.25:
                    halign = "right"
            style = self.styles.style(font=self._font_of(sp), fill=fill, border=True, numfmt=fmt, halign=halign, valign="center",
                                      wrap="\n" in text)
            if isinstance(value, _dt.datetime):
                sheet.set(row0 + r, c, "n", excel_date(value), style)
            elif value is not None:
                sheet.set(row0 + r, c, "n", value, style)
            elif text:
                sheet.set(row0 + r, c, "s", text, style)
            else:
                sheet.set(row0 + r, c, "e", None, style)
            if sp is not None and sp.link:
                sheet.links.append((f"{col_letter(c)}{row0 + r + 1}", sp.link))
            if cell["c1"] - cell["c0"] > 1 or cell["r1"] - cell["r0"] > 1:
                sheet.merges.append((row0 + r, c, row0 + cell["r1"] - 1, cell["c1"] - 1))
                # the covered cells carry the border and fill too
                for rr in range(cell["r0"], cell["r1"]):
                    for cc in range(cell["c0"], cell["c1"]):
                        if (rr, cc) != (r, c):
                            sheet.set(row0 + rr, cc, "e", None, style)
        for cc in range(n_c):
            w = (xs[cc + 1] - xs[cc]) / 5.25
            sheet.col_w[cc] = max(sheet.col_w.get(cc, 0.0), min(max(w, 2.5), 80.0))
        for rr in range(n_r):
            sheet.row_h[row0 + rr] = max(sheet.row_h.get(row0 + rr, 0.0), min(max((ys[rr + 1] - ys[rr]) * 1.0, 12.0), 400.0))
        return row0 + n_r - 1

    # ---- tables found from the layout of text
    def _write_text_table(self, sheet: SheetBuilder, lines: list[Line], row0: int) -> int:
        # split every line into cells at the tab marks
        rows: list[list[tuple[float, str, Span]]] = []
        for ln in lines:
            cells = []
            cur_x, cur_text, cur_sp = None, "", None
            for sp in ln.spans:
                if sp.text == "\t":
                    if cur_x is not None:
                        cells.append((cur_x, cur_text.strip(), cur_sp))
                    cur_x, cur_text, cur_sp = None, "", None
                    continue
                if cur_x is None:
                    cur_x = sp.bbox[0]
                    cur_sp = sp
                cur_text += sp.text
            if cur_x is not None:
                cells.append((cur_x, cur_text.strip(), cur_sp))
            rows.append(cells)
        # column anchors: cluster the cell x positions
        xs = sorted(x for r in rows for x, _t, _s in r)
        anchors: list[float] = []
        for x in xs:
            if anchors and x - anchors[-1] < 10:
                anchors[-1] = (anchors[-1] + x) / 2 if False else anchors[-1]
                continue
            anchors.append(x)
        for i, r in enumerate(rows):
            for x, text, sp in r:
                c = max(k for k, a in enumerate(anchors) if a <= x + 10) if any(a <= x + 10 for a in anchors) else 0
                value, fmt = recognise(text) if self.numbers else (None, None)
                style = self.styles.style(font=self._font_of(sp), numfmt=fmt, halign="right" if value is not None else None)
                if isinstance(value, _dt.datetime):
                    sheet.set(row0 + i, c, "n", excel_date(value), style)
                elif value is not None:
                    sheet.set(row0 + i, c, "n", value, style)
                elif text:
                    sheet.set(row0 + i, c, "s", text, style)
            sheet.row_h[row0 + i] = max(15.0, (lines[i].bbox[3] - lines[i].bbox[1]) * 1.15)
        for k in range(len(anchors)):
            width = (anchors[k + 1] - anchors[k]) / 5.25 if k + 1 < len(anchors) else 20.0
            sheet.col_w[k] = max(sheet.col_w.get(k, 0.0), min(max(width, 4.0), 60.0))
        return row0 + len(rows) - 1

    # ------------------------------------------------------------------ packaging
    def _package(self) -> bytes:
        shared: list[str] = []
        shared_idx: dict[str, int] = {}
        sheet_xml = []
        sheet_rels: list[list[tuple[str, str]]] = []
        for sh in self.sheets:
            out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']
            out.append('<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>')
            if sh.cells:
                max_r = max(r for r, _ in sh.cells)
                max_c = max(c for _, c in sh.cells)
                out.append(f'<dimension ref="A1:{col_letter(max_c)}{max_r + 1}"/>')
            out.append('<sheetViews><sheetView workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/>')
            if sh.col_w:
                out.append("<cols>" + "".join(f'<col min="{c + 1}" max="{c + 1}" width="{w + 0.7:.2f}" customWidth="1"/>' for c, w in sorted(sh.col_w.items())) + "</cols>")
            out.append("<sheetData>")
            by_row: dict[int, list[tuple[int, tuple]]] = {}
            for (r, c), cell in sh.cells.items():
                by_row.setdefault(r, []).append((c, cell))
            for r in sorted(set(by_row) | set(sh.row_h)):
                attrs = f'r="{r + 1}"'
                if r in sh.row_h:
                    attrs += f' ht="{sh.row_h[r]:.2f}" customHeight="1"'
                out.append(f"<row {attrs}>")
                for c, (kind, value, style) in sorted(by_row.get(r, [])):
                    ref = f"{col_letter(c)}{r + 1}"
                    if kind == "s":
                        if value not in shared_idx:
                            shared_idx[value] = len(shared)
                            shared.append(value)
                        out.append(f'<c r="{ref}" s="{style}" t="s"><v>{shared_idx[value]}</v></c>')
                    elif kind == "n":
                        out.append(f'<c r="{ref}" s="{style}"><v>{value!r}</v></c>')
                    else:
                        out.append(f'<c r="{ref}" s="{style}"/>')
                out.append("</row>")
            out.append("</sheetData>")
            if sh.merges:
                out.append(f'<mergeCells count="{len(sh.merges)}">' + "".join(
                    f'<mergeCell ref="{col_letter(c0)}{r0 + 1}:{col_letter(c1)}{r1 + 1}"/>' for r0, c0, r1, c1 in sh.merges) + "</mergeCells>")
            rels = []
            if sh.links:
                out.append("<hyperlinks>")
                for i, (ref, url) in enumerate(sh.links):
                    rid = f"rId{i + 1}"
                    out.append(f'<hyperlink ref="{ref}" r:id="{rid}"/>')
                    rels.append((rid, url))
                out.append("</hyperlinks>")
            out.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6" header="0.3" footer="0.3"/>')
            out.append(f'<pageSetup paperSize="9" orientation="{"landscape" if sh.landscape else "portrait"}" fitToHeight="0"/>')
            out.append("</worksheet>")
            sheet_xml.append("".join(out))
            sheet_rels.append(rels)
        sst = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
               f'count="{len(shared)}" uniqueCount="{len(shared)}">' + "".join(f'<si><t xml:space="preserve">{esc(s)}</t></si>' for s in shared) + "</sst>")
        n = len(self.sheets)
        wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
        wb_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
        ctypes = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                  '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>',
                  '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
                  '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
                  '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>']
        for i, sh in enumerate(self.sheets):
            wb.append(f'<sheet name="{esc(sh.name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>')
            wb_rels.append(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>')
            ctypes.append(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        wb.append("</sheets></workbook>")
        wb_rels.append(f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
        wb_rels.append(f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>')
        wb_rels.append("</Relationships>")
        ctypes.append("</Types>")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", "".join(ctypes))
            z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            z.writestr("xl/workbook.xml", "".join(wb))
            z.writestr("xl/_rels/workbook.xml.rels", "".join(wb_rels))
            z.writestr("xl/styles.xml", self.styles.xml())
            z.writestr("xl/sharedStrings.xml", sst)
            for i, xml in enumerate(sheet_xml):
                z.writestr(f"xl/worksheets/sheet{i + 1}.xml", xml)
                if sheet_rels[i]:
                    rel = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
                    for rid, url in sheet_rels[i]:
                        rel.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="{esc(url)}" TargetMode="External"/>')
                    rel.append("</Relationships>")
                    z.writestr(f"xl/worksheets/_rels/sheet{i + 1}.xml.rels", "".join(rel))
        return out.getvalue()


def _merge_close(vals, tol=1.5):
    out = []
    for v in vals:
        if out and v - out[-1] < tol:
            continue
        out.append(v)
    return out


def convert(doc: pymupdf.Document, mode: str = "per_page", pages: list[int] | None = None, numbers: bool = True):
    conv = XlsxFromPdf(doc, mode, pages, numbers)
    data = conv.convert()
    return data, conv.warnings
