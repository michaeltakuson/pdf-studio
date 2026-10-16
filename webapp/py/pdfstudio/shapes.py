"""Preset and custom shape outlines (DrawingML) as polylines.

``outline(name, w, h, adj)`` returns a list of subpaths ``(points, closed)`` in
the shape's own coordinate space (0..w, 0..h, y down). Curves are flattened.
It covers the presets that actually turn up in slides and documents; any other
name falls back to a rectangle so a shape is never lost.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

from .ooxml import NS, local

Path = tuple[list[tuple[float, float]], bool]


def _bez(p0, p1, p2, p3, steps=14):
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1 - t
        x = mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0]
        y = mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1]
        out.append((x, y))
    return out


def _arc(cx, cy, rx, ry, a0, a1, steps=None):
    """Points along an ellipse arc from angle a0 to a1 (degrees, clockwise on screen)."""
    sweep = a1 - a0
    n = steps or max(6, int(abs(sweep) / 8))
    return [(cx + rx * math.cos(math.radians(a0 + sweep * i / n)), cy + ry * math.sin(math.radians(a0 + sweep * i / n)))
            for i in range(n + 1)]


def _ellipse(w, h):
    return [(_arc(w / 2, h / 2, w / 2, h / 2, 0, 360, 48), True)]


def _rect(w, h):
    return [([(0, 0), (w, 0), (w, h), (0, h)], True)]


def _round_rect(w, h, r):
    r = max(0.0, min(r, w / 2, h / 2))
    pts = []
    pts += _arc(w - r, r, r, r, -90, 0, 6)
    pts += _arc(w - r, h - r, r, r, 0, 90, 6)
    pts += _arc(r, h - r, r, r, 90, 180, 6)
    pts += _arc(r, r, r, r, 180, 270, 6)
    return [(pts, True)]


def _poly(*pts):
    return [(list(pts), True)]


def _regular(w, h, n, rot=-90.0):
    return [([(w / 2 + w / 2 * math.cos(math.radians(rot + 360 * i / n)), h / 2 + h / 2 * math.sin(math.radians(rot + 360 * i / n)))
              for i in range(n)], True)]


def _star(w, h, n, inner=0.5, rot=-90.0):
    pts = []
    for i in range(n * 2):
        r = 1.0 if i % 2 == 0 else inner
        a = math.radians(rot + 180 * i / n)
        pts.append((w / 2 + w / 2 * r * math.cos(a), h / 2 + h / 2 * r * math.sin(a)))
    return [(pts, True)]


def outline(name: str, w: float, h: float, adj: dict | None = None) -> list[Path]:
    adj = adj or {}

    def a(key, default):
        v = adj.get(key)
        return default if v is None else v / 100000.0

    m = min(w, h)
    if name in ("rect", "flowChartProcess", "flowChartAlternateProcess" if False else "rect", "textBox", "flowChartPredefinedProcess"):
        return _rect(w, h)
    if name in ("roundRect", "flowChartAlternateProcess"):
        return _round_rect(w, h, m * a("adj", 0.16667))
    if name in ("ellipse", "flowChartConnector", "flowChartOr", "flowChartSummingJunction"):
        return _ellipse(w, h)
    if name in ("triangle", "flowChartExtract"):
        x = w * a("adj", 0.5)
        return _poly((x, 0), (w, h), (0, h))
    if name == "rtTriangle":
        return _poly((0, 0), (w, h), (0, h))
    if name in ("diamond", "flowChartDecision"):
        return _poly((w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2))
    if name == "parallelogram":
        o = m * a("adj", 0.25) if False else w * min(a("adj", 0.25), 0.9) * (m / w)
        return _poly((o, 0), (w, 0), (w - o, h), (0, h))
    if name in ("flowChartInputOutput",):
        o = w * 0.2
        return _poly((o, 0), (w, 0), (w - o, h), (0, h))
    if name in ("trapezoid", "flowChartManualOperation"):
        o = m * a("adj", 0.25)
        if name == "flowChartManualOperation":
            return _poly((0, 0), (w, 0), (w - w * 0.2, h), (w * 0.2, h))
        return _poly((o, 0), (w - o, 0), (w, h), (0, h))
    if name in ("pentagon", "regularPentagon"):
        return _regular(w, h, 5)
    if name == "hexagon":
        o = w * a("adj", 0.25)
        return _poly((o, 0), (w - o, 0), (w, h / 2), (w - o, h), (o, h), (0, h / 2))
    if name == "octagon":
        o = m * a("adj", 0.29289)
        return _poly((o, 0), (w - o, 0), (w, o), (w, h - o), (w - o, h), (o, h), (0, h - o), (0, o))
    if name == "plus" or name == "mathPlus":
        o = m * (a("adj", 0.25) if name == "plus" else 0.23)
        return _poly((o, 0), (w - o, 0), (w - o, o), (w, o), (w, h - o), (w - o, h - o), (w - o, h), (o, h), (o, h - o), (0, h - o), (0, o), (o, o))
    if name == "star4":
        return _star(w, h, 4, 0.38)
    if name == "star5":
        return _star(w, h, 5, 0.382)
    if name == "star6":
        return _star(w, h, 6, 0.58)
    if name == "star8":
        return _star(w, h, 8, 0.7)
    if name in ("rightArrow", "leftArrow", "upArrow", "downArrow"):
        head = a("adj2", 0.5) * m
        body = a("adj1", 0.5)
        if name in ("rightArrow", "leftArrow"):
            t, b = h * (1 - body) / 2, h * (1 + body) / 2
            hx = w - head
            pts = [(0, t), (hx, t), (hx, 0), (w, h / 2), (hx, h), (hx, b), (0, b)]
            if name == "leftArrow":
                pts = [(w - x, y) for x, y in pts]
        else:
            l, r = w * (1 - body) / 2, w * (1 + body) / 2
            hy = h - head
            pts = [(l, 0), (r, 0), (r, hy), (w, hy), (w / 2, h), (0, hy), (l, hy)]
            pts = [(l, h), (l, head), (0, head), (w / 2, 0), (w, head), (r, head), (r, h)] if name == "upArrow" else \
                [(l, 0), (r, 0), (r, hy), (w, hy), (w / 2, h), (0, hy), (l, hy)]
        return [(pts, True)]
    if name == "leftRightArrow":
        head = a("adj2", 0.5) * m
        body = a("adj1", 0.5)
        t, b = h * (1 - body) / 2, h * (1 + body) / 2
        return _poly((0, h / 2), (head, 0), (head, t), (w - head, t), (w - head, 0), (w, h / 2), (w - head, h), (w - head, b), (head, b), (head, h))
    if name in ("chevron",):
        o = m * a("adj", 0.5)
        return _poly((0, 0), (w - o, 0), (w, h / 2), (w - o, h), (0, h), (o, h / 2))
    if name in ("homePlate", "pentagon" if False else "homePlate"):
        o = m * a("adj", 0.5)
        return _poly((0, 0), (w - o, 0), (w, h / 2), (w - o, h), (0, h))
    if name == "notchedRightArrow":
        head = a("adj2", 0.5) * m
        body = a("adj1", 0.5)
        t, b = h * (1 - body) / 2, h * (1 + body) / 2
        return _poly((0, t), (w - head, t), (w - head, 0), (w, h / 2), (w - head, h), (w - head, b), (0, b), (h * 0.25, h / 2))
    if name in ("line", "straightConnector1"):
        return [([(0, 0), (w, h)], False)]
    if name in ("bentConnector2",):
        return [([(0, 0), (w, 0), (w, h)], False)]
    if name in ("bentConnector3",):
        x = w * a("adj1", 0.5)
        return [([(0, 0), (x, 0), (x, h), (w, h)], False)]
    if name in ("bentConnector4", "bentConnector5"):
        x = w * a("adj1", 0.5)
        y = h * a("adj2", 0.5)
        return [([(0, 0), (x, 0), (x, y), (w, y), (w, h)], False)]
    if name in ("curvedConnector2", "curvedConnector3", "curvedConnector4", "curvedConnector5"):
        pts = [(0, 0)] + _bez((0, 0), (w / 2, 0), (w / 2, h), (w, h), 20)
        return [(pts, False)]
    if name == "can" or name == "flowChartMagneticDisk":
        ry = h * 0.12
        pts = _arc(w / 2, ry, w / 2, ry, 180, 360, 20)
        body = [(w, ry), (w, h - ry)] + _arc(w / 2, h - ry, w / 2, ry, 0, 180, 20) + [(0, ry)]
        return [(pts + body, True), (_arc(w / 2, ry, w / 2, ry, 0, 180, 20), False)]
    if name in ("leftBracket", "rightBracket"):
        pts = [(w, 0), (0, 0), (0, h), (w, h)]
        if name == "rightBracket":
            pts = [(0, 0), (w, 0), (w, h), (0, h)]
        return [(pts, False)]
    if name in ("leftBrace", "rightBrace"):
        pts = [(w, 0)] + _bez((w, 0), (w / 2, 0), (w / 2, h * 0.2), (w / 2, h * 0.25)) + [(w / 2, h * 0.4)] + \
            _bez((w / 2, h * 0.4), (w / 2, h * 0.47), (w / 4, h * 0.5), (0, h * 0.5)) + _bez((0, h * 0.5), (w / 4, h * 0.5), (w / 2, h * 0.53), (w / 2, h * 0.6)) + \
            [(w / 2, h * 0.75)] + _bez((w / 2, h * 0.75), (w / 2, h * 0.8), (w / 2, h), (w, h))
        if name == "rightBrace":
            pts = [(w - x, y) for x, y in pts]
        return [(pts, False)]
    if name in ("wedgeRectCallout", "wedgeRoundRectCallout", "wedgeEllipseCallout", "borderCallout1", "callout1"):
        tx = w / 2 + w * a("adj1", -0.20833)
        ty = h / 2 + h * a("adj2", 0.625)
        if name == "wedgeEllipseCallout":
            return _ellipse(w, h) + [([(w * 0.4, h * 0.95), (tx, ty), (w * 0.6, h * 0.92)], True)]
        base = _round_rect(w, h, m * 0.16667) if name == "wedgeRoundRectCallout" else _rect(w, h)
        return base + [([(w * 0.3, h * 0.99), (tx, ty), (w * 0.45, h * 0.99)], True)]
    if name == "heart":
        pts = [(w / 2, h * 0.25)] + _bez((w / 2, h * 0.25), (w * 0.45, 0), (w * 0.05, 0), (0, h * 0.3)) + \
            _bez((0, h * 0.3), (0, h * 0.55), (w * 0.35, h * 0.75), (w / 2, h)) + _bez((w / 2, h), (w * 0.65, h * 0.75), (w, h * 0.55), (w, h * 0.3)) + \
            _bez((w, h * 0.3), (w * 0.95, 0), (w * 0.55, 0), (w / 2, h * 0.25))
        return [(pts, True)]
    if name == "cloud":
        pts = []
        bumps = 10
        for i in range(bumps):
            a0 = 360 * i / bumps
            a1 = 360 * (i + 1) / bumps
            r = 0.5
            pts += _arc(w / 2 + w * 0.34 * math.cos(math.radians((a0 + a1) / 2)), h / 2 + h * 0.34 * math.sin(math.radians((a0 + a1) / 2)),
                        w * 0.17, h * 0.17, a0 - 90, a1 + 90, 6)
        return [(pts, True)]
    if name == "moon":
        pts = _arc(w, h / 2, w, h / 2, 90, 270, 24) + _arc(w, h / 2, w * 0.55, h / 2, 270, 90, 24)
        return [(pts, True)]
    if name == "sun":
        return _star(w, h, 12, 0.7) + _ellipse(w * 0.5, h * 0.5)
    if name == "lightningBolt":
        return _poly((w * 0.4, 0), (w * 0.15, h * 0.5), (w * 0.4, h * 0.5), (w * 0.2, h), (w * 0.85, h * 0.4), (w * 0.55, h * 0.4), (w * 0.8, 0))
    if name in ("flowChartTerminator",):
        return _round_rect(w, h, h / 2)
    if name in ("flowChartDocument",):
        pts = [(0, 0), (w, 0), (w, h * 0.85)] + [(w - w * i / 20, h * (0.85 + 0.1 * math.sin(2 * math.pi * i / 20))) for i in range(21)] + [(0, h * 0.85)]
        return [(pts, True)]
    if name in ("flowChartPreparation",):
        o = w * 0.15
        return _poly((o, 0), (w - o, 0), (w, h / 2), (w - o, h), (o, h), (0, h / 2))
    if name in ("flowChartDelay",):
        return [([(0, 0), (w / 2, 0)] + _arc(w / 2, h / 2, w / 2, h / 2, -90, 90, 20) + [(0, h)], True)]
    if name in ("flowChartMerge",):
        return _poly((0, 0), (w, 0), (w / 2, h))
    if name == "frame":
        t = m * a("adj1", 0.125)
        return [([(0, 0), (w, 0), (w, h), (0, h)], True), ([(t, t), (t, h - t), (w - t, h - t), (w - t, t)], True)]
    if name == "donut":
        t = m * a("adj", 0.25)
        return _ellipse(w, h) + [(_arc(w / 2, h / 2, w / 2 - t, h / 2 - t, 360, 0, 40), True)]
    if name in ("round1Rect", "round2SameRect", "round2DiagRect"):
        r = m * a("adj1", 0.16667)
        pts = [(0, 0)] + [(w - r, 0)] + _arc(w - r, r, r, r, -90, 0, 6) + [(w, h), (0, h)]
        if name == "round2SameRect":
            pts = [(r, 0), (w - r, 0)] + _arc(w - r, r, r, r, -90, 0, 6) + [(w, h), (0, h), (0, r)] + _arc(r, r, r, r, 180, 270, 6)
        return [(pts, True)]
    if name in ("snip1Rect", "snip2SameRect", "snipRoundRect"):
        s = m * a("adj1", 0.16667)
        return _poly((0, 0), (w - s, 0), (w, s), (w, h), (0, h))
    if name == "foldedCorner":
        s = m * 0.16667
        return [([(0, 0), (w, 0), (w, h - s), (w - s, h), (0, h)], True), ([(w - s, h), (w - s, h - s), (w, h - s)], False)]
    if name == "mathMinus":
        o = h * 0.38
        return _poly((0, o), (w, o), (w, h - o), (0, h - o))
    if name == "mathEqual":
        return _poly((0, h * 0.2), (w, h * 0.2), (w, h * 0.45), (0, h * 0.45)) + _poly((0, h * 0.55), (w, h * 0.55), (w, h * 0.8), (0, h * 0.8))
    if name == "mathMultiply":
        o = m * 0.2
        return _poly((o, 0), (w / 2, h / 2 - o), (w - o, 0), (w, o), (w / 2 + o, h / 2), (w, h - o), (w - o, h), (w / 2, h / 2 + o), (o, h), (0, h - o), (w / 2 - o, h / 2), (0, o))
    if name in ("arc", "blockArc", "pie", "chord"):
        a0 = adj.get("adj1")
        a1 = adj.get("adj2")
        s = (a0 / 60000.0) if a0 is not None else (270.0 if name != "blockArc" else 180.0)
        e = (a1 / 60000.0) if a1 is not None else (0.0 if name != "blockArc" else 0.0)
        if e <= s:
            e += 360
        pts = _arc(w / 2, h / 2, w / 2, h / 2, s, e, 30)
        if name == "pie":
            pts = [(w / 2, h / 2)] + pts
        return [(pts, name != "arc")]
    if name in ("rightArrowCallout", "leftArrowCallout", "upArrowCallout", "downArrowCallout", "bentArrow", "uturnArrow",
                "circularArrow", "curvedRightArrow", "stripedRightArrow"):
        return _poly((0, h * 0.3), (w * 0.6, h * 0.3), (w * 0.6, 0), (w, h / 2), (w * 0.6, h), (w * 0.6, h * 0.7), (0, h * 0.7))
    if name == "teardrop":
        pts = _arc(w / 2, h / 2, w / 2, h / 2, 0, 270, 36) + [(w, 0)]
        return [(pts, True)]
    if name == "smileyFace":
        return _ellipse(w, h) + [(_arc(w * 0.33, h * 0.38, w * 0.04, h * 0.05, 0, 360, 12), True),
                                 (_arc(w * 0.67, h * 0.38, w * 0.04, h * 0.05, 0, 360, 12), True),
                                 (_arc(w / 2, h * 0.5, w * 0.28, h * 0.28, 20, 160, 16), False)]
    return _rect(w, h)


# ----------------------------------------------------------------------------- custom geometry


def custom_paths(cust: ET.Element, w: float, h: float) -> list[Path]:
    """Paths of an <a:custGeom>, scaled to a w x h box."""
    out: list[Path] = []
    path_list = cust.find("a:pathLst", NS)
    if path_list is None:
        return out
    for path in path_list.findall("a:path", NS):
        pw = float(path.get("w") or 0) or w
        ph = float(path.get("h") or 0) or h
        sx = w / pw if pw else 1.0
        sy = h / ph if ph else 1.0
        cur: list[tuple[float, float]] = []
        pos = (0.0, 0.0)
        start = (0.0, 0.0)

        def pt(el):
            return (float(el.get("x", 0)) * sx, float(el.get("y", 0)) * sy)

        def flush(closed=False):
            nonlocal cur
            if len(cur) >= 2:
                out.append((cur, closed))
            cur = []

        for cmd in path:
            t = local(cmd.tag)
            if t == "moveTo":
                flush()
                pos = pt(cmd.find("a:pt", NS))
                start = pos
                cur = [pos]
            elif t == "lnTo":
                pos = pt(cmd.find("a:pt", NS))
                cur.append(pos)
            elif t == "cubicBezTo":
                pts = [pt(p) for p in cmd.findall("a:pt", NS)]
                if len(pts) == 3:
                    cur += _bez(pos, pts[0], pts[1], pts[2])
                    pos = pts[2]
            elif t == "quadBezTo":
                pts = [pt(p) for p in cmd.findall("a:pt", NS)]
                if len(pts) == 2:
                    c1 = (pos[0] + 2 / 3 * (pts[0][0] - pos[0]), pos[1] + 2 / 3 * (pts[0][1] - pos[1]))
                    c2 = (pts[1][0] + 2 / 3 * (pts[0][0] - pts[1][0]), pts[1][1] + 2 / 3 * (pts[0][1] - pts[1][1]))
                    cur += _bez(pos, c1, c2, pts[1])
                    pos = pts[1]
            elif t == "arcTo":
                wr = float(cmd.get("wR", 0)) * sx
                hr = float(cmd.get("hR", 0)) * sy
                st = float(cmd.get("stAng", 0)) / 60000.0
                sw = float(cmd.get("swAng", 0)) / 60000.0
                if wr and hr:
                    cx = pos[0] - wr * math.cos(math.radians(st))
                    cy = pos[1] - hr * math.sin(math.radians(st))
                    arc = _arc(cx, cy, wr, hr, st, st + sw)
                    cur += arc[1:]
                    pos = arc[-1]
            elif t == "close":
                if cur:
                    flush(True)
                    cur = []
                pos = start
        flush()
    return out
