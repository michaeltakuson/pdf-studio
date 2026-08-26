"""Round-trip checks: model -> real PDF annotations -> model.

Run with: python -m tests.test_roundtrip   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from backend import annots


def build_sample() -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Redact me please", fontsize=14)
    page.insert_htmlbox(pymupdf.Rect(72, 140, 500, 200), "<p>日本語のテキストです。検索できます。</p>")
    return doc


SAMPLE = [
    {
        "id": "hl1", "page": 0, "type": "highlight",
        "rect": [72, 88, 220, 104],
        "quads": [[72, 88, 220, 88, 72, 104, 220, 104]],
        "style": {"stroke": "#ffe14d", "opacity": 0.45},
        "author": "テスト", "contents": "強調", "subject": "レビュー",
        "flags": {"print": True},
        "state": "accepted",
        "replies": [
            {"author": "佐藤", "contents": "ここは修正済みです"},
            {"author": "テスト", "contents": "確認しました。ありがとうございます"},
        ],
    },
    {
        "id": "ft1", "page": 0, "type": "freetext",
        "rect": [300, 300, 480, 350], "text": "テキストボックスです",
        "style": {"stroke": "#2f6df6", "fill": "#ffffff", "width": 1,
                  "font": {"family": "japan", "size": 12, "color": "#2f6df6", "align": "left"}},
        "author": "テスト",
    },
    {
        "id": "co1", "page": 0, "type": "freetext",
        "rect": [300, 400, 480, 440], "text": "引き出し線つき",
        "callout": [[200, 500], [250, 460], [300, 440]],
        "style": {"stroke": "#e0403a", "fill": "#ffffff", "width": 1,
                  "font": {"family": "japan", "size": 11, "color": "#000000", "align": "left"}},
    },
    {
        "id": "sq1", "page": 0, "type": "square",
        "rect": [72, 400, 260, 500],
        "style": {"stroke": "#e0403a", "fill": None, "width": 2, "cloudIntensity": 2},
        "contents": "雲形の枠",
    },
    {
        "id": "ci1", "page": 0, "type": "circle",
        "rect": [280, 400, 420, 500],
        "style": {"stroke": "#8b5cf6", "fill": "#f0e9ff", "width": 1.5},
    },
    {
        "id": "ln1", "page": 0, "type": "line",
        "points": [[72, 550], [260, 600]], "rect": [62, 540, 270, 610],
        "style": {"stroke": "#3fb950", "width": 2, "lineEnds": ["none", "openArrow"]},
    },
    {
        "id": "ink1", "page": 0, "type": "ink",
        "rect": [70, 640, 300, 720],
        "strokes": [{
            "pts": [[80, 700], [120, 660], [160, 700], [200, 660], [240, 700]],
            "pressure": [0.2, 0.6, 1.0, 0.6, 0.2],
        }],
        "style": {"stroke": "#8b5cf6", "width": 3},
        "tool": "pen",
    },
    {
        "id": "pg1", "page": 0, "type": "polygon",
        "points": [[350, 600], [450, 620], [420, 700], [340, 680]],
        "rect": [340, 600, 450, 700],
        "style": {"stroke": "#22b3a4", "fill": None, "width": 1.5},
    },
    {
        "id": "nt1", "page": 0, "type": "note",
        "rect": [520, 100, 540, 120], "contents": "付箋のコメント", "icon": "Comment",
        "style": {"stroke": "#ffd23d"},
    },
    {
        "id": "st1", "page": 0, "type": "stamp",
        "rect": [400, 200, 520, 250], "stampIndex": 12,
        "style": {"stroke": "#1b7f3b", "width": 2},
    },
]


def check(condition, label):
    print(("  OK   " if condition else "  FAIL ") + label)
    return bool(condition)


def main() -> int:
    failures = 0
    doc = build_sample()

    written = annots.write_document(doc, SAMPLE)
    failures += not check(written == len(SAMPLE), f"{len(SAMPLE)} 件すべて書き込まれた (実際 {written})")

    out = Path(__file__).parent / "roundtrip.pdf"
    doc.save(out, garbage=3, deflate=True)
    doc.close()

    reopened = pymupdf.open(out)
    read = annots.read_document(reopened)
    failures += not check(len(read) == len(SAMPLE), f"再読込で {len(SAMPLE)} 件検出 (実際 {len(read)})")

    by_type = {}
    for item in read:
        by_type.setdefault(item["type"], []).append(item)

    for kind in ("highlight", "freetext", "square", "circle", "line", "ink", "polygon", "note", "stamp"):
        failures += not check(kind in by_type, f"{kind} が復元された")

    hl = by_type.get("highlight", [{}])[0]
    failures += not check(hl.get("author") == "テスト", "作成者(日本語)が保持された")
    failures += not check(hl.get("contents") == "強調", "コメント(日本語)が保持された")
    failures += not check(hl.get("subject") == "レビュー", "主題が保持された")
    failures += not check(len(hl.get("quads") or []) == 1, "ハイライトのquadが保持された")

    replies = hl.get("replies") or []
    failures += not check(len(replies) == 2, f"返信2件が復元された (実際 {len(replies)})")
    failures += not check(any("修正済み" in (r.get("contents") or "") for r in replies),
                          "返信の本文(日本語)が保持された")
    failures += not check(any(r.get("author") == "佐藤" for r in replies),
                          "返信の作成者が保持された")
    failures += not check(hl.get("state") == "accepted",
                          f"レビュー状態が/Stateから復元された (実際 {hl.get('state')})")

    ink = by_type.get("ink", [{}])[0]
    pressure = (ink.get("strokes") or [{}])[0].get("pressure")
    failures += not check(pressure and len(pressure) == 5, "筆圧データが私用キー経由で往復した")

    line = by_type.get("line", [{}])[0]
    failures += not check(
        (line.get("style") or {}).get("lineEnds", [None, None])[1] == "openArrow",
        "矢印の先端形状が保持された",
    )

    square = by_type.get("square", [{}])[0]
    failures += not check((square.get("style") or {}).get("cloudIntensity", 0) > 0, "雲形の枠が保持された")

    note = by_type.get("note", [{}])[0]
    failures += not check((note.get("style") or {}).get("stroke", "").lower() == "#ffd23d",
                          "付箋のアイコン色が保持された")

    stamp = by_type.get("stamp", [{}])[0]
    failures += not check(stamp.get("stampIndex") == 12,
                          f"標準スタンプの種類が保持された (実際 {stamp.get('stampIndex')})")
    failures += not check((stamp.get("style") or {}).get("stroke", "").lower() == "#1b7f3b",
                          "スタンプの色が保持された")

    freetexts = by_type.get("freetext", [])
    failures += not check(any("テキストボックス" in (f.get("text") or "") for f in freetexts),
                          "FreeTextの日本語が保持された")
    failures += not check(any(f.get("callout") for f in freetexts), "引き出し線の座標が保持された")

    box = next((f for f in freetexts if "テキストボックス" in (f.get("text") or "")), {})
    failures += not check((box.get("style") or {}).get("fill", "").lower() == "#ffffff",
                          "FreeTextの背景色が保持された")
    box_font = (box.get("style") or {}).get("font") or {}
    failures += not check(box_font.get("color", "").lower() == "#2f6df6",
                          f"FreeTextの文字色が/DAから復元された (実際 {box_font.get('color')})")
    failures += not check(round(box_font.get("size", 0)) == 12,
                          f"FreeTextの文字サイズが復元された (実際 {box_font.get('size')})")
    failures += not check((box.get("style") or {}).get("stroke", "").lower() == "#2f6df6",
                          "FreeTextの枠線色が文字色と一致する")

    # Repeated save/reload must not creep. PyMuPDF pads /Rect to cover borders
    # and cloud scallops, so naively reading it back grows the shape each time.
    drifts = [Path(__file__).parent / "_drift_a.pdf", Path(__file__).parent / "_drift_b.pdf"]
    items = read
    before = {i["type"]: [round(v, 1) for v in i["rect"]] for i in items}
    cycles = reopened
    for index in range(3):
        target = drifts[index % 2]  # PyMuPDF refuses a full save over its own source
        annots.write_document(cycles, items)
        cycles.save(target, garbage=3, deflate=True, clean=True)
        cycles.close()
        cycles = pymupdf.open(target)
        items = annots.read_document(cycles)
    after = {i["type"]: [round(v, 1) for v in i["rect"]] for i in items}
    for kind in ("square", "circle", "line", "stamp"):
        failures += not check(before.get(kind) == after.get(kind),
                              f"{kind} が3往復しても座標不変 ({before.get(kind)} -> {after.get(kind)})")
    reopened = cycles

    # Redaction must genuinely remove text, not just cover it.
    page = reopened[0]
    failures += not check("Redact me" in page.get_text(), "墨消し前は本文が抽出できる")
    hits = page.search_for("Redact me please")
    for quad in hits:
        page.add_redact_annot(quad, fill=(0, 0, 0))
    page.apply_redactions()
    failures += not check("Redact me" not in page.get_text(), "墨消し適用後は本文から実際に消えている")

    reopened.close()
    for path in drifts:
        path.unlink(missing_ok=True)
    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}  ->  {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
