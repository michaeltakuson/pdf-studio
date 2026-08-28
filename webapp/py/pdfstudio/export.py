"""Getting annotations back out: XFDF, CSV, Markdown and a summary PDF.

The source material's point is that a review's value is downstream of the PDF.
XFDF carries the annotations alone (tens of KB against a multi-MB document),
CSV feeds a spreadsheet, Markdown feeds a knowledge base, and the summary PDF
is what you hand to someone who only has a viewer.
"""

from __future__ import annotations

import csv
import io
from xml.etree import ElementTree as ET

import pymupdf

from .annots import STATES, TEXT_MARKUP

XFDF_NS = "http://ns.adobe.com/xfdf/"

TYPE_LABELS = {
    "highlight": "ハイライト", "underline": "下線", "squiggly": "波線",
    "strikeout": "取消線", "areaHighlight": "範囲塗り", "freetext": "テキスト",
    "note": "付箋", "line": "線", "square": "矩形", "circle": "円",
    "polygon": "多角形", "polyline": "折れ線", "ink": "手書き",
    "stamp": "スタンプ", "redact": "墨消し", "caret": "挿入",
}

STATE_LABELS = {
    "accepted": "承諾", "rejected": "却下",
    "completed": "完了", "cancelled": "取り消し",
}


def label_for(item: dict) -> str:
    return TYPE_LABELS.get(item.get("type", ""), item.get("type", ""))


def body_of(item: dict) -> str:
    """The text a reader cares about: the annotation's own words."""
    parts = []
    if item.get("text"):
        parts.append(item["text"])
    contents = item.get("contents")
    if contents and contents not in parts:
        parts.append(contents)
    return "\n".join(parts)


def quoted_text(doc: pymupdf.Document, item: dict) -> str:
    """For markup annotations, the page text sitting under the mark."""
    if item.get("type") not in TEXT_MARKUP and item.get("type") != "redact":
        return ""
    page_index = int(item.get("page", 0))
    if page_index >= doc.page_count:
        return ""
    page = doc[page_index]
    pieces = []
    for quad in item.get("quads") or []:
        if len(quad) != 8:
            continue
        rect = pymupdf.Quad(
            (quad[0], quad[1]), (quad[2], quad[3]), (quad[4], quad[5]), (quad[6], quad[7])
        ).rect
        pieces.append(page.get_textbox(rect).strip())
    return " ".join(p for p in pieces if p)


def sort_key(item: dict):
    return (int(item.get("page", 0)), item.get("rect", [0, 0])[1], item.get("rect", [0])[0])


# ---------------------------------------------------------------- CSV


def to_csv(doc: pymupdf.Document, items: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "ページ", "種類", "作成者", "主題", "状態", "チェック済",
        "本文", "対象テキスト", "返信数", "色", "作成日時",
        "x0", "y0", "x1", "y1",
    ])
    for item in sorted(items, key=sort_key):
        rect = item.get("rect") or [0, 0, 0, 0]
        writer.writerow([
            int(item.get("page", 0)) + 1,
            label_for(item),
            item.get("author", ""),
            item.get("subject", ""),
            STATE_LABELS.get(item.get("state") or "", ""),
            "✓" if item.get("checked") else "",
            body_of(item),
            quoted_text(doc, item),
            len(item.get("replies") or []),
            (item.get("style") or {}).get("stroke", ""),
            item.get("created") or "",
            *[round(v, 1) for v in rect],
        ])
    return buffer.getvalue()


# ---------------------------------------------------------------- Markdown


def to_markdown(doc: pymupdf.Document, items: list[dict], name: str,
                colour_tags: dict[str, str] | None = None) -> str:
    """Extract to Markdown, keeping colour as meaning.

    Colour-coding a highlight (yellow = key idea, red = objection) only pays
    off if the colour survives extraction, so each entry carries its tag.
    """
    tags = colour_tags or {}
    lines = [f"# {name}", ""]
    lines.append(f"注釈 {len(items)} 件 — PDF Studio で抽出")
    lines.append("")

    current_page = None
    for item in sorted(items, key=sort_key):
        page_index = int(item.get("page", 0))
        if page_index != current_page:
            current_page = page_index
            lines.append("")
            lines.append(f"## {page_index + 1} ページ")
            lines.append("")

        colour = (item.get("style") or {}).get("stroke", "")
        tag = tags.get(colour.lower())
        heading = [label_for(item)]
        if item.get("author"):
            heading.append(item["author"])
        if tag:
            heading.append(f"#{tag}")
        if item.get("state"):
            heading.append(f"［{STATE_LABELS.get(item['state'], item['state'])}］")
        lines.append(f"**{' · '.join(heading)}**")

        quote = quoted_text(doc, item)
        if quote:
            lines.append("")
            for row in quote.splitlines():
                lines.append(f"> {row}")
        body = body_of(item)
        if body:
            lines.append("")
            lines.append(body)
        for reply in item.get("replies") or []:
            who = reply.get("author") or "返信"
            lines.append("")
            lines.append(f"- **{who}**: {reply.get('contents', '')}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------- XFDF


def _rect_str(rect, height: float) -> str:
    """XFDF stores PDF user space, which counts from the bottom of the page."""
    x0, y0, x1, y1 = rect
    return f"{x0:.2f},{height - y1:.2f},{x1:.2f},{height - y0:.2f}"


def to_xfdf(doc: pymupdf.Document, items: list[dict], href: str) -> str:
    ET.register_namespace("", XFDF_NS)
    root = ET.Element(f"{{{XFDF_NS}}}xfdf")
    fields = ET.SubElement(root, f"{{{XFDF_NS}}}f")
    fields.set("href", href)
    annots_el = ET.SubElement(root, f"{{{XFDF_NS}}}annots")

    for item in sorted(items, key=sort_key):
        page_index = int(item.get("page", 0))
        height = doc[page_index].rect.height if page_index < doc.page_count else 842
        kind = item.get("type")
        tag = {"areaHighlight": "square", "note": "text"}.get(kind, kind)
        node = ET.SubElement(annots_el, f"{{{XFDF_NS}}}{tag}")
        node.set("page", str(page_index))
        node.set("rect", _rect_str(item.get("rect") or [0, 0, 0, 0], height))
        style = item.get("style") or {}
        if style.get("stroke"):
            node.set("color", style["stroke"])
        if style.get("fill"):
            node.set("interior-color", style["fill"])
        node.set("opacity", str(style.get("opacity", 1)))
        node.set("width", str(style.get("width", 1)))
        if item.get("author"):
            node.set("title", item["author"])
        if item.get("subject"):
            node.set("subject", item["subject"])
        if item.get("created"):
            node.set("creationdate", item["created"])
        if item.get("state") in STATES:
            node.set("state", STATES[item["state"]])
            node.set("statemodel", "Review")

        if item.get("quads"):
            coords = []
            for quad in item["quads"]:
                for index in range(0, 8, 2):
                    coords.append(f"{quad[index]:.2f}")
                    coords.append(f"{height - quad[index + 1]:.2f}")
            node.set("coords", ",".join(coords))
        if item.get("points"):
            node.set("vertices", ";".join(
                f"{x:.2f},{height - y:.2f}" for x, y in item["points"]
            ))
        if item.get("strokes"):
            node.set("inklist", ";".join(
                ",".join(f"{x:.2f},{height - y:.2f}" for x, y in stroke.get("pts", []))
                for stroke in item["strokes"]
            ))

        body = body_of(item)
        if body:
            ET.SubElement(node, f"{{{XFDF_NS}}}contents").text = body
        for reply in item.get("replies") or []:
            reply_el = ET.SubElement(annots_el, f"{{{XFDF_NS}}}text")
            reply_el.set("page", str(page_index))
            reply_el.set("rect", node.get("rect"))
            reply_el.set("inreplyto", str(id(item)))
            if reply.get("author"):
                reply_el.set("title", reply["author"])
            ET.SubElement(reply_el, f"{{{XFDF_NS}}}contents").text = reply.get("contents", "")

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def from_xfdf(doc: pymupdf.Document, xml: str) -> list[dict]:
    """Read annotations back out of an XFDF file into model items."""
    root = ET.fromstring(xml)
    items: list[dict] = []
    container = root.find(f"{{{XFDF_NS}}}annots")
    if container is None:
        return items

    for node in container:
        tag = node.tag.split("}")[-1]
        if tag not in TYPE_LABELS and tag not in ("text",):
            continue
        page_index = int(node.get("page", 0))
        if page_index >= doc.page_count:
            continue
        height = doc[page_index].rect.height
        rect = [float(v) for v in (node.get("rect") or "0,0,0,0").split(",")]
        item: dict = {
            "type": "note" if tag == "text" else tag,
            "page": page_index,
            "rect": [rect[0], height - rect[3], rect[2], height - rect[1]],
            "author": node.get("title", ""),
            "subject": node.get("subject", ""),
            "contents": (node.findtext(f"{{{XFDF_NS}}}contents") or "").strip(),
            "style": {
                "stroke": node.get("color") or "#e0403a",
                "fill": node.get("interior-color"),
                "opacity": float(node.get("opacity", 1)),
                "width": float(node.get("width", 1)),
            },
            "replies": [],
            "state": None,
        }
        state = node.get("state")
        if state:
            item["state"] = {v: k for k, v in STATES.items()}.get(state)
        if item["type"] == "freetext":
            item["text"] = item["contents"]

        coords = node.get("coords")
        if coords:
            values = [float(v) for v in coords.split(",")]
            item["quads"] = [
                [
                    values[i], height - values[i + 1], values[i + 2], height - values[i + 3],
                    values[i + 4], height - values[i + 5], values[i + 6], height - values[i + 7],
                ]
                for i in range(0, len(values) - 7, 8)
            ]
        vertices = node.get("vertices")
        if vertices:
            item["points"] = [
                [float(p.split(",")[0]), height - float(p.split(",")[1])]
                for p in vertices.split(";") if "," in p
            ]
        inklist = node.get("inklist")
        if inklist:
            strokes = []
            for stroke in inklist.split(";"):
                values = [float(v) for v in stroke.split(",") if v]
                strokes.append({
                    "pts": [[values[i], height - values[i + 1]] for i in range(0, len(values) - 1, 2)],
                    "pressure": None,
                })
            item["strokes"] = [s for s in strokes if len(s["pts"]) >= 2]
        items.append(item)
    return items


# ---------------------------------------------------------------- summary PDF


SUMMARY_CSS = """
body { font-family: sans-serif; font-size: 10pt; }
h1 { font-size: 16pt; margin: 0 0 2pt 0; }
h2 { font-size: 11pt; margin: 14pt 0 4pt 0; color: #444; }
.meta { color: #666; font-size: 8.5pt; margin-bottom: 10pt; }
.item { margin-bottom: 9pt; padding-left: 6pt; border-left: 3pt solid #ccc; }
.head { font-size: 8.5pt; color: #555; }
.body { margin-top: 2pt; }
blockquote { margin: 2pt 0 2pt 8pt; color: #333; font-style: italic; }
.reply { margin-left: 12pt; font-size: 9pt; color: #444; }
"""


def summary_pdf(doc: pymupdf.Document, items: list[dict], name: str) -> bytes:
    """Build a standalone PDF listing every annotation, grouped by page."""
    html = [f"<h1>注釈一覧</h1><div class='meta'>{_escape(name)} — {len(items)} 件</div>"]

    current_page = None
    for item in sorted(items, key=sort_key):
        page_index = int(item.get("page", 0))
        if page_index != current_page:
            current_page = page_index
            html.append(f"<h2>{page_index + 1} ページ</h2>")
        colour = (item.get("style") or {}).get("stroke", "#cccccc")
        head = [label_for(item)]
        if item.get("author"):
            head.append(_escape(item["author"]))
        if item.get("state"):
            head.append(STATE_LABELS.get(item["state"], item["state"]))
        if item.get("checked"):
            head.append("✓")
        block = [f"<div class='item' style='border-left-color:{_escape(colour)}'>"]
        block.append(f"<div class='head'>{' · '.join(head)}</div>")
        quote = quoted_text(doc, item)
        if quote:
            block.append(f"<blockquote>{_escape(quote)}</blockquote>")
        body = body_of(item)
        if body:
            block.append(f"<div class='body'>{_escape(body)}</div>")
        for reply in item.get("replies") or []:
            who = _escape(reply.get("author") or "返信")
            block.append(f"<div class='reply'>↳ <b>{who}</b>: {_escape(reply.get('contents', ''))}</div>")
        block.append("</div>")
        html.append("".join(block))

    story = pymupdf.Story(html="".join(html), user_css=SUMMARY_CSS)
    mediabox = pymupdf.Rect(0, 0, 595, 842)
    frame = pymupdf.Rect(50, 50, 545, 792)

    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    more = 1
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(frame)
        story.draw(device)
        writer.end_page()
    writer.close()

    # Story embeds the whole CJK fallback face — several megabytes for a page
    # of comments. Subsetting keeps only the characters actually used.
    built = pymupdf.open("pdf", buffer.getvalue())
    built.subset_fonts()
    data = built.tobytes(garbage=4, deflate=True, clean=True)
    built.close()
    return data


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
