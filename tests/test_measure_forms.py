"""Phase 4 checks: measurement, take-off, AcroForm fields, document comparison.

Run with: python -m tests.test_measure_forms   (from the project root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf

from backend import compare, forms, measure

HERE = Path(__file__).parent


def check(condition, label):
    print(("  OK   " if condition else "  FAIL ") + label)
    return bool(condition)


def close(a, b, tolerance=1e-6):
    return abs(a - b) <= tolerance


def main() -> int:
    failures = 0

    # ---------------------------------------------------------------- scale
    # A 100pt line drawn over something known to be 5 m.
    scale = measure.calibrate([0, 0], [100, 0], 5, "m")
    failures += not check(close(scale["pagePoints"], 100), "キャリブレーションが距離を測る")
    failures += not check(close(measure.scale_factor(scale), 0.05),
                          f"縮尺が 1pt = 0.05m になる ({measure.scale_factor(scale)})")

    d = measure.measure("distance", [[0, 0], [200, 0]], scale)
    failures += not check(close(d["value"], 10), f"距離が実寸で出る ({d['label']})")
    failures += not check(d["label"] == "10.00 m", f"距離の表示が単位つき ({d['label']})")

    square = [[0, 0], [100, 0], [100, 100], [0, 100]]
    a = measure.measure("area", square, scale)
    failures += not check(close(a["value"], 25), f"面積が実寸で出る ({a['label']})")
    failures += not check(a["label"] == "25.00 m²", f"面積の単位が平方になる ({a['label']})")

    p = measure.measure("perimeter", square, scale)
    failures += not check(close(p["value"], 20), f"周囲長が出る ({p['label']})")

    ang = measure.measure("angle", [[100, 0], [0, 0], [0, 100]], scale)
    failures += not check(close(ang["value"], 90, 0.001), f"直角を90度と測る ({ang['label']})")

    v = measure.measure("volume", square, scale, depth=2)
    failures += not check(close(v["value"], 50), f"体積が面積×深さになる ({v['label']})")

    r = measure.measure("radius", [[0, 0], [60, 80]], scale)
    failures += not check(close(r["value"], 5), f"半径が出る ({r['label']})")

    failures += not check(close(measure.convert(1, "m", "mm"), 1000), "単位変換 m→mm")
    failures += not check(close(measure.convert(1, "in", "mm"), 25.4), "単位変換 in→mm")

    # ---------------------------------------------------------------- take-off
    items = [
        {"page": 0, "subject": "床面積", "measure": a, "style": {"stroke": "#2f6df6"}},
        {"page": 0, "subject": "床面積", "measure": a, "style": {"stroke": "#2f6df6"}},
        {"page": 1, "subject": "配管長", "measure": d, "style": {"stroke": "#e0403a"}},
        {"page": 1, "subject": "コンセント", "tool": "count", "style": {"stroke": "#3fb950"}},
        {"page": 1, "subject": "コンセント", "tool": "count", "style": {"stroke": "#3fb950"}},
        {"page": 0, "type": "highlight"},  # not a measurement: must be ignored
    ]
    summary = measure.summarise(items)
    failures += not check(summary["groups"] == 3, f"分類ごとにまとまる ({summary['groups']} 分類)")
    failures += not check(summary["items"] == 5, f"計測でないものは除外される ({summary['items']} 件)")

    floor = next(r for r in summary["rows"] if r["label"] == "床面積")
    failures += not check(close(floor["total"], 50), f"同じ分類の合計が出る ({floor['total']})")
    sockets = next(r for r in summary["rows"] if r["label"] == "コンセント")
    failures += not check(sockets["count"] == 2 and sockets["kind"] == "count",
                          "カウントが個数として集計される")
    failures += not check(sockets["pages"] == [2], f"ページ番号が1始まりで出る ({sockets['pages']})")
    failures += not check(sockets["unit"] == "" and not sockets["summable"],
                          "カウントには単位も合計もつかない")
    failures += not check(floor["unit"] == "m²", f"面積の単位が平方で集計される ({floor['unit']})")

    angles = measure.summarise([{"page": 0, "measure": ang, "style": {}}])["rows"][0]
    failures += not check(angles["label"] == "角度", f"分類名が日本語になる ({angles['label']})")
    failures += not check(angles["unit"] == "°" and not angles["summable"],
                          f"角度は度で表され、合計しない ({angles['unit']}, {angles['summable']})")

    csv_text = measure.to_csv(summary, items)
    failures += not check("床面積" in csv_text and "50" in csv_text, "数量拾いをCSVにできる")

    # ---------------------------------------------------------------- forms
    doc = pymupdf.open(HERE / "sample.pdf")
    page = doc[0]
    forms.create_field(page, {
        "name": "氏名", "type": "text", "rect": [60, 600, 300, 622],
        "required": True, "fontSize": 11,
    })
    forms.create_field(page, {
        "name": "同意", "type": "checkbox", "rect": [60, 640, 78, 658],
    })
    forms.create_field(page, {
        "name": "区分", "type": "dropdown", "rect": [60, 680, 260, 702],
        "options": ["個人", "法人", "その他"],
    })
    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))

    fields = forms.read_fields(doc)
    failures += not check(len(fields) == 3, f"フォームを3つ作成できた ({len(fields)})")
    names = {f["name"] for f in fields}
    failures += not check("氏名" in names, "日本語のフィールド名が保持される")
    kinds = {f["name"]: f["type"] for f in fields}
    failures += not check(kinds.get("同意") == "checkbox", "チェックボックスとして復元される")
    failures += not check(kinds.get("区分") == "dropdown", "ドロップダウンとして復元される")
    dropdown = next(f for f in fields if f["name"] == "区分")
    failures += not check(dropdown["options"] == ["個人", "法人", "その他"],
                          f"選択肢が保持される ({dropdown['options']})")
    required = next(f for f in fields if f["name"] == "氏名")
    failures += not check(required["required"], "必須フラグが保持される")
    failures += not check(not forms.has_xfa(doc), "XFAではない（AcroFormとして作られている）")

    filled = forms.fill(doc, {"氏名": "山田太郎", "区分": "法人"})
    failures += not check(filled == 2, f"名前を指定して入力できる ({filled} 件)")
    doc = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    values = {f["name"]: f["value"] for f in forms.read_fields(doc)}
    failures += not check(values.get("氏名") == "山田太郎",
                          f"保存後も日本語の入力値が残る ({values.get('氏名')!r})")

    fdf = forms.to_fdf(doc, "sample.pdf")
    failures += not check("氏名" in fdf, "FDFに書き出せる")
    restored = forms.from_fdf(fdf)
    failures += not check(restored.get("氏名") == "山田太郎",
                          f"FDFから読み戻せる ({restored.get('氏名')!r})")

    csv_out = forms.to_csv(doc)
    failures += not check("山田太郎" in csv_out, "フォーム値をCSVにできる")

    collated = forms.collate([("回答1.pdf", doc), ("回答2.pdf", doc)])
    failures += not check(collated.count("山田太郎") == 2, "複数の回答を集計できる")
    doc.close()

    # ---------------------------------------------------------------- compare
    base = pymupdf.open(HERE / "sample.pdf")
    revised = pymupdf.open(HERE / "sample.pdf")
    same = compare.compare(base, revised)
    failures += not check(len(same) == 0, f"同一文書では差分が出ない ({len(same)} 件)")

    revised[1].insert_htmlbox(
        pymupdf.Rect(60, 560, 500, 620),
        "<p style='font-size:20pt'>この段落は改訂で追加されました</p>",
    )
    revised = pymupdf.open("pdf", revised.tobytes(garbage=3, deflate=True))
    changes = compare.compare(base, revised, author="比較")
    failures += not check(len(changes) >= 1, f"変更を検出できる ({len(changes)} 箇所)")
    failures += not check(all(c["page"] == 1 for c in changes),
                          f"変更のあるページだけを指す ({sorted({c['page'] for c in changes})})")
    first = changes[0]
    failures += not check(first["style"]["cloudIntensity"] > 0, "差分が雲形注釈として出る")
    failures += not check(first["subject"] == "文書比較", "差分に主題が入る（絞り込める）")
    failures += not check(first["rect"][1] > 500 and first["rect"][3] < 700,
                          f"差分の位置が変更箇所に一致する ({[round(v) for v in first['rect']]})")

    data = compare.overlay(base, revised, dpi=72)
    stacked = pymupdf.open("pdf", data)
    failures += not check(stacked.page_count == base.page_count, "重ね合わせPDFが生成される")
    failures += not check(len(stacked[0].get_images()) == 2,
                          "重ね合わせが2版を重ねている")
    stacked.close()
    base.close()
    revised.close()

    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
