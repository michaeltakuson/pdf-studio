"""Invisible writing: the structure tags that assistive technology reads.

None of this shows on screen, but reflow, read-aloud and every accessibility
checker depend on it. A PDF without tags simply cannot be read in order.
"""

from __future__ import annotations

import pymupdf

from .common import rect_to_view

# Structure types worth offering; the spec defines many more.
TAG_TYPES = {
    "Document": "文書",
    "H1": "見出し1", "H2": "見出し2", "H3": "見出し3",
    "P": "段落",
    "L": "リスト", "LI": "リスト項目",
    "Table": "表", "TR": "行", "TH": "見出しセル", "TD": "セル",
    "Figure": "図",
    "Caption": "キャプション",
    "Link": "リンク",
    "Artifact": "アーティファクト（読み上げ対象外）",
}


def is_tagged(doc: pymupdf.Document) -> bool:
    try:
        kind, _ = doc.xref_get_key(doc.pdf_catalog(), "StructTreeRoot")
    except Exception:
        return False
    return kind not in ("null", "unknown")


def has_language(doc: pymupdf.Document) -> str | None:
    try:
        kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
    except Exception:
        return None
    return value.strip("()") if kind in ("string", "name") else None


def set_language(doc: pymupdf.Document, language: str = "ja-JP") -> None:
    doc.xref_set_key(doc.pdf_catalog(), "Lang", pymupdf.get_pdf_str(language))


def image_alt_texts(doc: pymupdf.Document) -> list[dict]:
    """Images and whatever alternate text they currently carry."""
    found = []
    for page in doc:
        for info in page.get_images(full=True):
            xref = info[0]
            alt = None
            try:
                kind, value = doc.xref_get_key(xref, "Alt")
                if kind == "string":
                    alt = value.strip("()")
            except Exception:
                pass
            for rect in page.get_image_rects(xref):
                found.append({
                    "xref": xref, "page": page.number,
                    "rect": rect_to_view(page, rect), "alt": alt,
                })
    return found


def set_alt_text(doc: pymupdf.Document, xref: int, text: str) -> None:
    doc.xref_set_key(xref, "Alt", pymupdf.get_pdf_str(text))


def audit(doc: pymupdf.Document) -> dict:
    """A plain-language accessibility check, with what to do about each item."""
    issues = []

    tagged = is_tagged(doc)
    if not tagged:
        issues.append({
            "id": "tags",
            "severity": "high",
            "title": "タグ（論理構造）がありません",
            "detail": "読み上げ順序もリフロー表示も、すべてタグに依存します。タグが無いPDFではこれらの機能が働きません。",
            "fix": "「自動でタグを付ける」を実行してください。",
        })

    language = has_language(doc)
    if not language:
        issues.append({
            "id": "lang",
            "severity": "medium",
            "title": "文書の言語が指定されていません",
            "detail": "支援技術がどの言語で読み上げるか判断できません。",
            "fix": "「言語を設定する」で日本語を指定してください。",
        })

    images = image_alt_texts(doc)
    missing = [i for i in images if not i["alt"]]
    if missing:
        issues.append({
            "id": "alt",
            "severity": "high",
            "title": f"代替テキストのない画像が {len(missing)} 個あります",
            "detail": "画像に説明が無いと、読み上げでは何も伝わりません。",
            "fix": "画像ごとに代替テキストを入力してください。装飾目的ならアーティファクトに指定します。",
            "items": missing,
        })

    empty_pages = [p.number + 1 for p in doc if not p.get_text().strip() and p.get_images()]
    if empty_pages:
        issues.append({
            "id": "scanned",
            "severity": "high",
            "title": f"テキストのない画像ページが {len(empty_pages)} ページあります",
            "detail": f"{', '.join(str(p) for p in empty_pages[:8])} ページ目。画像だけのページは読み上げも検索もできません。",
            "fix": "「OCR」を実行してテキスト層を埋め込んでください。",
        })

    metadata = doc.metadata or {}
    if not (metadata.get("title") or "").strip():
        issues.append({
            "id": "title",
            "severity": "low",
            "title": "文書のタイトルが設定されていません",
            "detail": "支援技術はファイル名ではなくタイトルを読み上げます。",
            "fix": "文書のタイトルを設定してください。",
        })

    return {
        "tagged": tagged,
        "language": language,
        "images": len(images),
        "imagesWithoutAlt": len(missing),
        "issues": issues,
        "passed": not issues,
    }


def autotag(doc: pymupdf.Document, *, language: str = "ja-JP") -> dict:
    """Build a structure tree from the text already on the page.

    Headings are inferred from font size — the same signal a sighted reader
    uses. This is a starting point that a person should then correct, not a
    substitute for real authoring.
    """
    catalog = doc.pdf_catalog()
    set_language(doc, language)

    sizes = []
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes.append(round(span.get("size", 11), 1))
    if not sizes:
        return {"tagged": False, "reason": "テキストがありません（先にOCRしてください）"}

    body = max(set(sizes), key=sizes.count)
    structure = []
    for page in doc:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            text = "".join(
                span.get("text", "")
                for line in block.get("lines", [])
                for span in line.get("spans", [])
            ).strip()
            if not text:
                continue
            size = max(
                (span.get("size", body) for line in block.get("lines", [])
                 for span in line.get("spans", [])),
                default=body,
            )
            if size >= body * 1.4:
                tag = "H1"
            elif size >= body * 1.15:
                tag = "H2"
            else:
                tag = "P"
            structure.append({"page": page.number, "tag": tag, "text": text[:120],
                              "rect": list(block["bbox"])})

    _write_struct_tree(doc, catalog, structure)
    return {
        "tagged": True,
        "elements": len(structure),
        "headings": sum(1 for s in structure if s["tag"].startswith("H")),
        "bodySize": body,
        "structure": structure,
    }


def _write_struct_tree(doc: pymupdf.Document, catalog: int, structure: list[dict]) -> None:
    """Write a minimal StructTreeRoot with one element per block."""
    kids = []
    for entry in structure:
        xref = doc.get_new_xref()
        doc.update_object(
            xref,
            f"<< /Type /StructElem /S /{entry['tag']} /Pg {doc[entry['page']].xref} 0 R "
            f"/ActualText {pymupdf.get_pdf_str(entry['text'])} >>",
        )
        kids.append(f"{xref} 0 R")

    root = doc.get_new_xref()
    doc.update_object(
        root,
        "<< /Type /StructTreeRoot /K [ " + " ".join(kids) + " ] >>",
    )
    doc.xref_set_key(catalog, "StructTreeRoot", f"{root} 0 R")
    doc.xref_set_key(catalog, "MarkInfo", "<< /Marked true >>")


def reading_order(doc: pymupdf.Document, page_index: int) -> list[dict]:
    """The order assistive technology would read a page in, as blocks."""
    page = doc[page_index]
    blocks = []
    for order, block in enumerate(page.get_text("blocks")):
        x0, y0, x1, y1, text, *_ = block
        if not str(text).strip():
            continue
        blocks.append({
            "order": order + 1,
            "rect": rect_to_view(page, [x0, y0, x1, y1]),
            "text": str(text).strip()[:120],
        })
    return blocks
