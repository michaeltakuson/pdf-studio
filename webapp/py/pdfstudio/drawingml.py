"""DrawingML shapes (``a:sp`` / ``wps:wsp``): fills, lines and outlines drawn onto a display list.

Shared by the Word, PowerPoint and Excel converters.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from . import shapes
from .ooxml import NS, Theme, color_from_drawingml, emu, local
from .typeset import Sheet, hex_color

DASHES = {
    "dash": "[4 3] 0", "sysDash": "[3 1] 0", "dot": "[1 2] 0", "sysDot": "[1 1] 0", "dashDot": "[4 2 1 2] 0",
    "lgDash": "[8 3] 0", "lgDashDot": "[8 3 1 3] 0", "sysDashDot": "[3 1 1 1] 0",
}


class Ctx:
    """What a shape needs to resolve colours and images."""

    def __init__(self, theme: Theme, scheme_map: dict | None = None, image_for=None):
        self.theme = theme
        self.scheme_map = scheme_map or {}
        self.image_for = image_for or (lambda rid: None)


def parse_fill(parent: ET.Element | None, ctx: Ctx):
    """('solid', color, alpha) / ('grad', stops, angle) / ('none',) / ('blip', rid) / None when unspecified."""
    if parent is None:
        return None
    for child in parent:
        tag = local(child.tag)
        if tag == "noFill":
            return ("none",)
        if tag == "solidFill":
            color = color_from_drawingml(child, ctx.theme, ctx.scheme_map)
            alpha = 1.0
            if len(child):
                for mod in child[0]:
                    if local(mod.tag) == "alpha":
                        alpha = int(mod.get("val", "100000")) / 100000.0
            return ("solid", color, alpha) if color is not None else None
        if tag == "gradFill":
            stops = []
            gs_list = child.find("a:gsLst", NS)
            if gs_list is not None:
                for gs in gs_list.findall("a:gs", NS):
                    color = color_from_drawingml(gs, ctx.theme, ctx.scheme_map)
                    if color is not None:
                        stops.append((int(gs.get("pos", "0")) / 100000.0, color))
            lin = child.find("a:lin", NS)
            angle = int(lin.get("ang", "0")) / 60000.0 if lin is not None else 90.0
            path = child.find("a:path", NS)
            if stops:
                return ("grad", sorted(stops), angle, path is not None)
            return None
        if tag == "blipFill":
            blip = child.find("a:blip", NS)
            if blip is not None:
                return ("blip", blip.get("{%s}embed" % NS["r"]))
            return None
        if tag == "pattFill":
            fg = color_from_drawingml(child.find("a:fgClr", NS), ctx.theme, ctx.scheme_map)
            bg = color_from_drawingml(child.find("a:bgClr", NS), ctx.theme, ctx.scheme_map)
            if fg is not None and bg is not None:
                return ("solid", tuple((a + b) / 2 for a, b in zip(fg, bg)), 1.0)
            return ("solid", fg or bg, 1.0) if (fg or bg) else None
        if tag == "grpFill":
            return None
    return None


def parse_line(sp_pr: ET.Element | None, ctx: Ctx):
    """dict(width, color, dash, head, tail) / 'none' / None when unspecified."""
    if sp_pr is None:
        return None
    ln = sp_pr.find("a:ln", NS)
    if ln is None:
        return None
    if ln.find("a:noFill", NS) is not None:
        return "none"
    width = emu(ln.get("w")) if ln.get("w") else None
    fill = parse_fill(ln, ctx)
    color = fill[1] if fill and fill[0] == "solid" else None
    dash = None
    pd = ln.find("a:prstDash", NS)
    if pd is not None:
        dash = DASHES.get(pd.get("val"))
    head = ln.find("a:headEnd", NS)
    tail = ln.find("a:tailEnd", NS)
    return {
        "width": width, "color": color, "dash": dash,
        "head": head.get("type") if head is not None and head.get("type") not in (None, "none") else None,
        "tail": tail.get("type") if tail is not None and tail.get("type") not in (None, "none") else None,
    }


def style_colors(style: ET.Element | None, ctx: Ctx):
    """Fill and line colours named by a shape's <p:style> / <wps:style> references."""
    fill = line = None
    if style is None:
        return fill, line
    fr = style.find("a:fillRef", NS)
    lr = style.find("a:lnRef", NS)
    if fr is not None and fr.get("idx") not in (None, "0") and len(fr):
        c = color_from_drawingml(fr, ctx.theme, ctx.scheme_map) if False else color_from_drawingml(fr[0], ctx.theme, ctx.scheme_map) if len(fr) else None
        if c is not None:
            fill = c
    if lr is not None and lr.get("idx") not in (None, "0") and len(lr):
        c = color_from_drawingml(lr[0], ctx.theme, ctx.scheme_map)
        if c is not None:
            line = c
    return fill, line


def geometry(sp_pr: ET.Element | None, w: float, h: float):
    """Outline paths of a shape's geometry, plus the preset name."""
    name = "rect"
    if sp_pr is None:
        return shapes.outline("rect", w, h), name
    cust = sp_pr.find("a:custGeom", NS)
    if cust is not None:
        paths = shapes.custom_paths(cust, w, h)
        return (paths or shapes.outline("rect", w, h)), "custom"
    prst = sp_pr.find("a:prstGeom", NS)
    if prst is not None:
        name = prst.get("prst") or "rect"
        adj = {}
        av = prst.find("a:avLst", NS)
        if av is not None:
            for gd in av.findall("a:gd", NS):
                fmla = gd.get("fmla") or ""
                if fmla.startswith("val "):
                    try:
                        adj[gd.get("name")] = float(fmla[4:])
                    except ValueError:
                        pass
        return shapes.outline(name, w, h, adj), name
    return shapes.outline("rect", w, h), name


def transform(points, x, y, w, h, rot=0.0, flip_h=False, flip_v=False):
    cx, cy = w / 2, h / 2
    out = []
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    for px, py in points:
        if flip_h:
            px = w - px
        if flip_v:
            py = h - py
        if rot:
            dx, dy = px - cx, py - cy
            px, py = cx + dx * ca - dy * sa, cy + dx * sa + dy * ca
        out.append((x + px, y + py))
    return out


def lerp_stops(stops, t):
    t = min(1.0, max(0.0, t))
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            f = 0 if p1 == p0 else (t - p0) / (p1 - p0)
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))
    return stops[-1][1]


def draw_shape(sheet: Sheet, x: float, y: float, w: float, h: float, sp_pr: ET.Element | None, ctx: Ctx,
               style: ET.Element | None = None, rot: float = 0.0, flip_h: bool = False, flip_v: bool = False,
               default_fill=None, default_line=None, image_op=None):
    """Draw the fill and outline of a shape; returns the preset name (for the caller's text handling)."""
    paths, name = geometry(sp_pr, max(w, 0.01), max(h, 0.01))
    fill = parse_fill(sp_pr, ctx)
    line = parse_line(sp_pr, ctx)
    s_fill, s_line = style_colors(style, ctx)
    if fill is None and s_fill is not None:
        fill = ("solid", s_fill, 1.0)
    if fill is None and default_fill is not None:
        fill = default_fill
    stroke = None
    width = 0.75
    dash = None
    arrows = (None, None)
    if line == "none":
        stroke = None
    else:
        color = None
        if isinstance(line, dict):
            color = line["color"]
            width = line["width"] if line["width"] is not None else (0.75 if style is None else 0.75)
            dash = line["dash"]
            arrows = (line["head"], line["tail"])
        if color is None and (line is not None or s_line is not None):
            color = s_line
        if color is None and line is not None and not isinstance(line, dict):
            color = None
        if color is None and default_line is not None and line is None and s_line is None:
            color = default_line
        stroke = color
        if isinstance(line, dict) and line["width"] is None and s_line is not None:
            width = 0.75
    closed_fill_ok = name not in ("line", "straightConnector1") and not name.startswith(("bentConnector", "curvedConnector"))
    # image fill
    if fill and fill[0] == "blip" and image_op is not None:
        data = ctx.image_for(fill[1])
        if data is not None:
            sheet.image(x, y, x + w, y + h, data)
            fill = None
    if fill and fill[0] == "grad" and closed_fill_ok:
        stops, angle = fill[1], fill[2]
        plain_rect = name in ("rect", "roundRect", "flowChartProcess", "textBox") or name == "custom" and False
        if plain_rect and not rot:
            n = 36
            vertical = not (45 <= (angle % 180) <= 135) is False
            horizontal_gradient = (angle % 180) < 45 or (angle % 180) > 135
            rev = 90 < (angle % 360) < 270 if horizontal_gradient else (angle % 360) >= 180
            for i in range(n):
                t0, t1 = i / n, (i + 1) / n
                col = lerp_stops(stops, (t0 + t1) / 2)
                if horizontal_gradient:
                    xa, xb = x + w * t0, x + w * t1
                    if rev:
                        xa, xb = x + w - w * t1, x + w - w * t0
                    sheet.rect(xa, y, xb + 0.4, y + h, fill=col)
                else:
                    ya, yb = y + h * t0, y + h * t1
                    if rev:
                        ya, yb = y + h - h * t1, y + h - h * t0
                    sheet.rect(x, ya, x + w, yb + 0.4, fill=col)
            fill = None
        else:
            mid = lerp_stops(stops, 0.5)
            fill = ("solid", mid, 1.0)
    fill_color = None
    if fill and fill[0] == "solid":
        fill_color = fill[1]
        if fill[2] < 1.0 and fill_color is not None:
            fill_color = tuple(1 - (1 - c) * fill[2] for c in fill_color)   # blend with white
    for points, closed in paths:
        pts = transform(points, x, y, w, h, rot, flip_h, flip_v)
        if closed_fill_ok:
            sheet.poly(pts, fill=fill_color if closed else None, stroke=stroke, width=width, closed=closed, dash=dash)
        else:
            sheet.poly(pts, fill=None, stroke=stroke if stroke is not None else (0, 0, 0), width=width, closed=False, dash=dash)
        if not closed_fill_ok and (arrows[0] or arrows[1]) and len(pts) >= 2:
            col = stroke if stroke is not None else (0, 0, 0)
            if arrows[1]:
                _arrow_head(sheet, pts[-2], pts[-1], width, col)
            if arrows[0]:
                _arrow_head(sheet, pts[1], pts[0], width, col)
    return name


def _arrow_head(sheet: Sheet, p_from, p_to, width, color):
    dx, dy = p_to[0] - p_from[0], p_to[1] - p_from[1]
    d = math.hypot(dx, dy)
    if d < 0.01:
        return
    ux, uy = dx / d, dy / d
    size = max(5.0, width * 4.5)
    bx, by = p_to[0] - ux * size, p_to[1] - uy * size
    nx, ny = -uy * size * 0.4, ux * size * 0.4
    sheet.poly([(p_to[0], p_to[1]), (bx + nx, by + ny), (bx - nx, by - ny)], fill=color, stroke=color, width=0.3, closed=True)
