"""Measurement and quantity take-off.

What separates these from ordinary shapes is the scale: every measurement is a
drawing distance multiplied by a ratio the user calibrates once. The result is
stored on the annotation so it can be recomputed and summed later.
"""

from __future__ import annotations

import math

# Conversion to millimetres. Points are the PDF's own unit (72 per inch).
UNITS = {
    "pt": 25.4 / 72,
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
}

AREA_UNITS = {"mm": "mm²", "cm": "cm²", "m": "m²", "in": "in²", "ft": "ft²", "pt": "pt²"}


def scale_factor(scale: dict) -> float:
    """Drawing points to real-world units of `scale['unit']`.

    A scale says "this many points on the page equals this much in reality",
    which is what calibrating against a known dimension produces.
    """
    page_length = float(scale.get("pagePoints") or 1)
    real_length = float(scale.get("realLength") or 1)
    if page_length <= 0:
        return 1.0
    return real_length / page_length


def calibrate(p1, p2, real_length: float, unit: str) -> dict:
    """Build a scale from a line the user drew over a known dimension."""
    points = math.dist(p1, p2)
    return {
        "pagePoints": points,
        "realLength": real_length,
        "unit": unit if unit in UNITS else "mm",
    }


def distance(points: list[list[float]], scale: dict) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.dist(a, b)
    return total * scale_factor(scale)


def perimeter(points: list[list[float]], scale: dict, closed: bool = True) -> float:
    loop = points + [points[0]] if closed and len(points) > 2 else points
    return distance(loop, scale)


def area(points: list[list[float]], scale: dict) -> float:
    """Shoelace area, converted to square units of the scale."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + [points[0]]):
        total += x0 * y1 - x1 * y0
    factor = scale_factor(scale)
    return abs(total) / 2 * factor * factor


def angle(points: list[list[float]]) -> float:
    """Angle at the middle point of three, in degrees."""
    if len(points) < 3:
        return 0.0
    (ax, ay), (bx, by), (cx, cy) = points[:3]
    first = math.atan2(ay - by, ax - bx)
    second = math.atan2(cy - by, cx - bx)
    degrees = math.degrees(abs(first - second))
    return 360 - degrees if degrees > 180 else degrees


def radius(points: list[list[float]], scale: dict) -> float:
    if len(points) < 2:
        return 0.0
    return math.dist(points[0], points[1]) * scale_factor(scale)


def volume(points: list[list[float]], scale: dict, depth: float) -> float:
    return area(points, scale) * depth


def format_value(value: float, kind: str, scale: dict, precision: int = 2) -> str:
    unit = scale.get("unit", "mm")
    if kind == "angle":
        return f"{value:.{precision}f}°"
    if kind == "area":
        return f"{value:.{precision}f} {AREA_UNITS.get(unit, unit + '²')}"
    if kind == "volume":
        return f"{value:.{precision}f} {unit}³"
    return f"{value:.{precision}f} {unit}"


def measure(kind: str, points: list[list[float]], scale: dict, *,
            depth: float = 0.0, precision: int = 2) -> dict:
    """Compute one measurement and return both the number and its label."""
    if kind == "distance":
        value = distance(points, scale)
    elif kind == "perimeter":
        value = perimeter(points, scale)
    elif kind == "area":
        value = area(points, scale)
    elif kind == "angle":
        value = angle(points)
    elif kind == "radius":
        value = radius(points, scale)
    elif kind == "volume":
        value = volume(points, scale, depth)
    else:
        raise ValueError(f"未対応の計測種別: {kind}")
    return {
        "kind": kind,
        "value": round(value, 6),
        "unit": scale.get("unit", "mm"),
        "label": format_value(value, kind, scale, precision),
    }


def convert(value: float, from_unit: str, to_unit: str) -> float:
    return value * UNITS.get(from_unit, 1.0) / UNITS.get(to_unit, 1.0)


# ---------------------------------------------------------------- take-off


KIND_LABELS = {
    "distance": "距離", "perimeter": "周囲長", "area": "面積",
    "angle": "角度", "radius": "半径", "volume": "体積", "count": "カウント",
}


def _unit_for(kind: str, unit: str) -> str:
    if kind == "count":
        return ""
    if kind == "angle":
        return "°"
    if kind == "area":
        return AREA_UNITS.get(unit, f"{unit}²")
    if kind == "volume":
        return f"{unit}³"
    return unit


def summarise(items: list[dict]) -> dict:
    """Group measurements and counts into a take-off table.

    Grouping is by subject, which is the field reviewers already use to label
    what a mark refers to, so the legend and the totals stay in step.
    """
    groups: dict[str, dict] = {}
    for item in items:
        measurement = item.get("measure")
        is_count = item.get("tool") == "count"
        if not measurement and not is_count:
            continue
        kind = "count" if is_count else measurement.get("kind", "")
        key = item.get("subject") or KIND_LABELS.get(kind, kind)
        group = groups.setdefault(key, {
            "label": key,
            "colour": (item.get("style") or {}).get("stroke", "#888888"),
            "kind": kind,
            "unit": _unit_for(kind, (measurement or {}).get("unit", "")),
            # Adding up angles is meaningless; only their count is reported.
            "summable": kind not in ("count", "angle"),
            "count": 0,
            "total": 0.0,
            "pages": set(),
        })
        group["count"] += 1
        group["pages"].add(int(item.get("page", 0)) + 1)
        if measurement:
            group["total"] += float(measurement.get("value") or 0)

    rows = []
    for group in groups.values():
        rows.append({
            **group,
            "total": round(group["total"], 4),
            "pages": sorted(group["pages"]),
        })
    rows.sort(key=lambda r: r["label"])
    return {"rows": rows, "groups": len(rows), "items": sum(r["count"] for r in rows)}


def to_csv(summary: dict, items: list[dict]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["分類", "種別", "個数", "合計", "単位", "ページ", "色"])
    for row in summary["rows"]:
        writer.writerow([
            row["label"], row["kind"], row["count"], row["total"], row["unit"],
            " ".join(str(p) for p in row["pages"]), row["colour"],
        ])
    writer.writerow([])
    writer.writerow(["ページ", "分類", "種別", "値", "単位", "表示", "コメント"])
    for item in sorted(items, key=lambda i: (i.get("page", 0), i.get("rect", [0, 0])[1])):
        measurement = item.get("measure")
        if not measurement and item.get("tool") != "count":
            continue
        writer.writerow([
            int(item.get("page", 0)) + 1,
            item.get("subject", ""),
            (measurement or {}).get("kind", "count"),
            (measurement or {}).get("value", 1),
            (measurement or {}).get("unit", ""),
            (measurement or {}).get("label", ""),
            item.get("contents", ""),
        ])
    return buffer.getvalue()
