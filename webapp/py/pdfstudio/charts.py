"""Charts (DrawingML ``c:chart``) drawn from their cached values: bar, column, line, area, pie, doughnut, scatter."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from . import typeset as T
from .drawingml import parse_fill
from .ooxml import NS, color_from_drawingml, local
from .typeset import Sheet, Style

C = NS["c"]

PALETTE = [(0.267, 0.447, 0.769), (0.929, 0.490, 0.192), (0.647, 0.647, 0.647), (1.0, 0.753, 0.0), (0.357, 0.608, 0.835),
           (0.439, 0.678, 0.278), (0.149, 0.267, 0.482), (0.620, 0.282, 0.086)]


def cq(tag):
    return "{%s}%s" % (C, tag)


def _cache_values(ref: ET.Element | None):
    if ref is None:
        return []
    cache = ref.find("c:numCache", NS)
    if cache is None:
        cache = ref.find("c:strCache", NS)
    if cache is None:
        lit = ref.find("c:numLit", NS) or ref.find("c:strLit", NS)
        cache = lit
    if cache is None:
        return []
    count = int(cache.find("c:ptCount", NS).get("val")) if cache.find("c:ptCount", NS) is not None else 0
    values = [None] * count
    for pt in cache.findall("c:pt", NS):
        i = int(pt.get("idx", "0"))
        v = pt.find("c:v", NS)
        if i < len(values) and v is not None and v.text is not None:
            values[i] = v.text
    return values


def _series(plot):
    out = []
    for ser in plot.findall("c:ser", NS):
        name_vals = _cache_values(ser.find("c:tx/c:strRef", NS))
        cats = _cache_values(ser.find("c:cat/c:strRef", NS) or ser.find("c:cat/c:numRef", NS) or ser.find("c:cat", NS))
        vals = ser.find("c:val/c:numRef", NS)
        if vals is None:
            vals = ser.find("c:yVal/c:numRef", NS)
        data = _cache_values(vals)
        xs = _cache_values(ser.find("c:xVal/c:numRef", NS))
        out.append({"name": name_vals[0] if name_vals else "", "cats": cats,
                    "vals": [float(v) if v not in (None, "") else None for v in data],
                    "xs": [float(v) if v not in (None, "") else None for v in xs], "el": ser})
    return out


def _nice_ticks(lo, hi, n=5):
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 2.5, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step) * step
    ticks = []
    v = start
    while v <= end + step * 0.001:
        ticks.append(round(v, 10))
        v += step
    return ticks


def _fmt(v):
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _title(root):
    t = root.find("c:chart/c:title", NS)
    if t is None:
        return None
    text = "".join(x.text or "" for x in t.iter("{%s}t" % NS["a"]))
    return text or None


def draw_chart(sheet: Sheet, root: ET.Element, x: float, y: float, w: float, h: float, dc) -> None:
    chart = root.find("c:chart", NS)
    if chart is None:
        return
    plot_area = chart.find("c:plotArea", NS)
    if plot_area is None:
        return
    base = Style(family="Yu Gothic", size=9.0, color=(0.35, 0.35, 0.35))
    title = _title(root)
    top = y + 4
    if title:
        st = Style(family="Yu Gothic", size=13.0, color=(0.2, 0.2, 0.2))
        tw = T.text_width(title, st)
        sheet.text(x + (w - tw) / 2, top + 13, title, st)
        top += 22
    plots = [p for p in plot_area if local(p.tag).endswith("Chart")]
    if not plots:
        return
    series_all = []
    kinds = []
    for plot in plots:
        kind = local(plot.tag)
        kinds.append(kind)
        for i, s in enumerate(_series(plot)):
            s["kind"] = kind
            s["plot"] = plot
            series_all.append(s)
    # colours
    for i, s in enumerate(series_all):
        color = PALETTE[i % len(PALETTE)]
        sp = s["el"].find("c:spPr", NS)
        if sp is not None:
            fill = parse_fill(sp, dc)
            if fill and fill[0] == "solid" and fill[1] is not None:
                color = fill[1]
            else:
                ln = sp.find("a:ln", NS)
                if ln is not None:
                    f = parse_fill(ln, dc)
                    if f and f[0] == "solid" and f[1] is not None:
                        color = f[1]
        s["color"] = color
    # legend
    legend = chart.find("c:legend", NS)
    legend_pos = "b"
    if legend is not None:
        lp = legend.find("c:legendPos", NS)
        legend_pos = lp.get("val") if lp is not None else "r"
    names = [s["name"] for s in series_all]
    pie_like = kinds[0] in ("pieChart", "doughnutChart", "pie3DChart")
    if pie_like and series_all:
        names = [c or "" for c in series_all[0]["cats"]]
    area_x0, area_y0, area_x1, area_y1 = x + 6, top, x + w - 6, y + h - 6
    if legend is not None and names:
        total_w = sum(T.text_width(n, base) + 22 for n in names)
        if legend_pos in ("b", "t"):
            ly = area_y1 - 8 if legend_pos == "b" else area_y0 + 8
            lx = x + max(6, (w - min(total_w, w - 12)) / 2)
            for i, n in enumerate(names):
                color = PALETTE[i % len(PALETTE)] if pie_like else series_all[i]["color"]
                sheet.rect(lx, ly - 7, lx + 8, ly + 1, fill=color)
                sheet.text(lx + 12, ly, n, base)
                lx += T.text_width(n, base) + 22
            if legend_pos == "b":
                area_y1 -= 20
            else:
                area_y0 += 20
        else:
            lw = max(T.text_width(n, base) for n in names) + 24
            lx = area_x1 - lw if legend_pos == "r" else area_x0
            ly = area_y0 + 12
            for i, n in enumerate(names):
                color = PALETTE[i % len(PALETTE)] if pie_like else series_all[i]["color"]
                sheet.rect(lx, ly - 7, lx + 8, ly + 1, fill=color)
                sheet.text(lx + 12, ly, n, base)
                ly += 14
            if legend_pos == "r":
                area_x1 -= lw
            else:
                area_x0 += lw
    if pie_like:
        _draw_pie(sheet, series_all[0] if series_all else None, kinds[0], area_x0, area_y0, area_x1, area_y1, base)
        return
    _draw_axes_chart(sheet, series_all, kinds, plots, area_x0, area_y0, area_x1, area_y1, base)


def _draw_pie(sheet, ser, kind, x0, y0, x1, y1, base):
    if not ser:
        return
    vals = [v or 0.0 for v in ser["vals"]]
    total = sum(v for v in vals if v > 0) or 1.0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = max(10.0, min(x1 - x0, y1 - y0) / 2 - 6)
    angle = -90.0
    for i, v in enumerate(vals):
        if v <= 0:
            continue
        sweep = 360.0 * v / total
        pts = [(cx, cy)] + [(cx + r * math.cos(math.radians(angle + sweep * k / max(6, int(sweep / 5)))),
                             cy + r * math.sin(math.radians(angle + sweep * k / max(6, int(sweep / 5)))))
                            for k in range(max(6, int(sweep / 5)) + 1)]
        color = PALETTE[i % len(PALETTE)]
        dpt = None
        for d in ser["el"].findall("c:dPt", NS):
            if d.get("idx") == str(i) if False else d.find("c:idx", NS) is not None and d.find("c:idx", NS).get("val") == str(i):
                dpt = d
        if dpt is not None:
            sp = dpt.find("c:spPr", NS)
            if sp is not None:
                f = parse_fill(sp, type("D", (), {"theme": None, "scheme_map": {}, "image_for": lambda *_: None})()) if False else None
        sheet.poly(pts, fill=color, stroke=(1, 1, 1), width=1.0, closed=True)
        mid = math.radians(angle + sweep / 2)
        label = f"{v / total * 100:.0f}%"
        lw = T.text_width(label, base)
        sheet.text(cx + r * 0.65 * math.cos(mid) - lw / 2, cy + r * 0.65 * math.sin(mid) + 3, label,
                   Style(family="Yu Gothic", size=9.0, color=(1, 1, 1), bold=True))
        angle += sweep
    if kind == "doughnutChart":
        ring = [(cx + r * 0.5 * math.cos(math.radians(a)), cy + r * 0.5 * math.sin(math.radians(a))) for a in range(0, 361, 6)]
        sheet.poly(ring, fill=(1, 1, 1), stroke=None, closed=True)


def _draw_axes_chart(sheet, series_all, kinds, plots, x0, y0, x1, y1, base):
    horizontal = any(local(p.tag) == "barChart" and (p.find("c:barDir", NS) is not None and p.find("c:barDir", NS).get("val") == "bar") for p in plots)
    scatter = kinds[0] == "scatterChart"
    stacked = any((p.find("c:grouping", NS) is not None and p.find("c:grouping", NS).get("val") in ("stacked", "percentStacked")) for p in plots)
    percent = any((p.find("c:grouping", NS) is not None and p.find("c:grouping", NS).get("val") == "percentStacked") for p in plots)
    ncat = max((len(s["cats"]) or len(s["vals"]) for s in series_all), default=0)
    if ncat == 0:
        return
    vals_all = [v for s in series_all for v in s["vals"] if v is not None]
    if not vals_all:
        return
    if stacked:
        sums = [sum((s["vals"][i] or 0.0) for s in series_all if i < len(s["vals"])) for i in range(ncat)]
        lo, hi = min(0.0, min(vals_all)), max(sums)
    else:
        lo, hi = min(0.0, min(vals_all)), max(vals_all)
    if percent:
        lo, hi = 0.0, 1.0
    if scatter:
        xs_all = [v for s in series_all for v in s["xs"] if v is not None] or list(range(ncat))
        lo_x, hi_x = min(xs_all), max(xs_all)
    ticks = _nice_ticks(lo, hi)
    lo, hi = ticks[0], ticks[-1]
    label_w = max(T.text_width(_fmt(t), base) for t in ticks) + 6
    cat_h = 16.0
    px0, px1 = x0 + label_w + 4, x1 - 4
    py0, py1 = y0 + 4, y1 - cat_h - 2
    if horizontal:
        px0 = x0 + max((T.text_width(str(c or ""), base) for c in series_all[0]["cats"]), default=20) + 8
        py1 = y1 - 14
    grid = (0.85, 0.85, 0.85)
    axis = (0.55, 0.55, 0.55)

    def val_pos(v):   # along the value axis
        return (v - lo) / (hi - lo) if hi != lo else 0.0

    # gridlines and value labels
    for t in ticks:
        f = val_pos(t)
        if horizontal:
            gx = px0 + (px1 - px0) * f
            sheet.line(gx, py0, gx, py1, grid, 0.5)
            lab = _fmt(t if not percent else t * 100) + ("%" if percent else "")
            sheet.text(gx - T.text_width(lab, base) / 2, py1 + 11, lab, base)
        else:
            gy = py1 - (py1 - py0) * f
            sheet.line(px0, gy, px1, gy, grid, 0.5)
            lab = _fmt(t if not percent else t * 100) + ("%" if percent else "")
            sheet.text(px0 - 4 - T.text_width(lab, base), gy + 3, lab, base)
    sheet.line(px0, py1, px1, py1, axis, 0.75) if not horizontal else sheet.line(px0, py0, px0, py1, axis, 0.75)
    cats = series_all[0]["cats"] if series_all[0]["cats"] else [str(i + 1) for i in range(ncat)]
    slot = ((py1 - py0) if horizontal else (px1 - px0)) / ncat
    nbars = sum(1 for s in series_all if s["kind"] == "barChart")
    zero = val_pos(0.0)
    bar_index = 0
    cum = [0.0] * ncat
    for si, s in enumerate(series_all):
        kind = s["kind"]
        color = s["color"]
        pts = []
        for i, v in enumerate(s["vals"][:ncat]):
            if v is None:
                continue
            if kind == "barChart":
                group_w = slot * 0.7
                if stacked:
                    bw = group_w
                    off = (slot - group_w) / 2
                    start = cum[i]
                    value = v
                    if percent:
                        tot = sum((ss["vals"][i] or 0.0) for ss in series_all if i < len(ss["vals"])) or 1.0
                        value = v / tot
                        start = start
                    f0, f1 = val_pos(start), val_pos(start + value)
                    cum[i] += value
                else:
                    bw = group_w / max(nbars, 1)
                    off = (slot - group_w) / 2 + bw * bar_index
                    f0, f1 = zero, val_pos(v)
                if horizontal:
                    ya = py0 + slot * i + off
                    xa, xb = px0 + (px1 - px0) * min(f0, f1), px0 + (px1 - px0) * max(f0, f1)
                    sheet.rect(xa, ya, xb, ya + bw, fill=color)
                else:
                    xa = px0 + slot * i + off
                    ya, yb = py1 - (py1 - py0) * max(f0, f1), py1 - (py1 - py0) * min(f0, f1)
                    sheet.rect(xa, ya, xa + bw, yb, fill=color)
            elif kind in ("lineChart", "areaChart", "scatterChart"):
                if kind == "scatterChart":
                    xv = s["xs"][i] if i < len(s["xs"]) and s["xs"][i] is not None else i
                    fx = (xv - lo_x) / ((hi_x - lo_x) or 1.0)
                    px = px0 + (px1 - px0) * (0.04 + 0.92 * fx)
                else:
                    px = px0 + slot * (i + 0.5)
                py = py1 - (py1 - py0) * val_pos(v)
                pts.append((px, py))
        if kind == "barChart":
            bar_index += 1
        elif pts:
            if kind == "areaChart":
                poly = pts + [(pts[-1][0], py1), (pts[0][0], py1)]
                sheet.poly(poly, fill=tuple(1 - (1 - c) * 0.6 for c in color), stroke=color, width=1.0, closed=True)
            else:
                smooth_off = True
                if kind != "scatterChart" or s["el"].find("c:spPr/a:ln/a:noFill", NS) is None:
                    sheet.poly(pts, stroke=color, width=2.0, closed=False)
                for px, py in pts:
                    sheet.rect(px - 2.5, py - 2.5, px + 2.5, py + 2.5, fill=color)
    # category labels
    for i, c in enumerate(cats[:ncat]):
        label = str(c or "")
        lw = T.text_width(label, base)
        if horizontal:
            sheet.text(px0 - 4 - min(lw, px0 - x0 - 4), py0 + slot * (i + 0.5) + 3, label, base)
        else:
            sheet.text(px0 + slot * (i + 0.5) - min(lw, slot) / 2, py1 + 12, label if lw <= slot + 4 else label[: max(1, int(len(label) * slot / lw))], base)
