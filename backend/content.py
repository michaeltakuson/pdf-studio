"""Page content operations (layer ②): real text, images and OCR.

Unlike the annotation layer, everything here rewrites what is *inside* the
page. There is no per-object undo once saved, which is why the app takes a
snapshot before each of these.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from .common import hex_to_rgb

TESSERACT_DIR = Path(r"C:\Program Files\Tesseract-OCR")


def tesseract_state() -> dict:
    """Where OCR stands on this machine, and what to do if it is missing."""
    exe = TESSERACT_DIR / "tesseract.exe"
    found = exe.exists() or bool(shutil.which("tesseract"))
    tessdata = TESSERACT_DIR / "tessdata"
    languages: list[str] = []
    if tessdata.exists():
        languages = sorted(p.stem for p in tessdata.glob("*.traineddata"))
    return {
        "installed": found,
        "path": str(exe) if exe.exists() else (shutil.which("tesseract") or ""),
        "languages": languages,
        "japanese": "jpn" in languages,
        "vertical": "jpn_vert" in languages,
    }


def _ensure_tessdata() -> str:
    """Locate the language data, and pass it explicitly rather than by env var.

    MuPDF reads TESSDATA_PREFIX when it initialises, so setting the variable
    from Python can be too late; every OCR call therefore gets the path handed
    to it directly.
    """
    tessdata = TESSERACT_DIR / "tessdata"
    if tessdata.exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata)
        return str(tessdata)
    prefix = os.environ.get("TESSDATA_PREFIX")
    if prefix and Path(prefix).exists():
        return prefix
    exe = shutil.which("tesseract")
    if exe:
        candidate = Path(exe).parent / "tessdata"
        if candidate.exists():
            os.environ["TESSDATA_PREFIX"] = str(candidate)
            return str(candidate)
    raise RuntimeError(
        "Tesseract OCR が見つかりません。README の導入手順を実行してください。"
    )


# Tesseract's page-segmentation modes. "block" (6) is the default because it
# measurably beat automatic segmentation on this project's mixed Japanese and
# Latin pages — automatic mode misread digits and split characters. Multi-column
# layouts are the case where "auto" wins, so it stays available.
PSM_MODES = {
    "block": "6",
    "auto": "3",
    "line": "7",
    "sparse": "11",
}
DEFAULT_PSM = "block"


def _tesseract_exe() -> str:
    exe = TESSERACT_DIR / "tesseract.exe"
    if exe.exists():
        return str(exe)
    found = shutil.which("tesseract")
    if found:
        return found
    raise RuntimeError(
        "Tesseract OCR が見つかりません。README の導入手順を実行してください。"
    )


def _run_tesseract(image: Path, out_stem: Path, language: str, psm: str,
                   tessdata: str, output: str) -> None:
    result = subprocess.run(
        [
            _tesseract_exe(), str(image), str(out_stem),
            "-l", language, "--psm", PSM_MODES.get(psm, psm),
            "--tessdata-dir", tessdata, output,
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"Tesseract が失敗しました: {result.stderr.strip()[:300]}")


def ocr_page_text(page: pymupdf.Page, *, language="jpn+eng", dpi=300,
                  psm=DEFAULT_PSM) -> str:
    """Read a scanned page without changing it."""
    tessdata = _ensure_tessdata()
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        image = work / "page.png"
        page.get_pixmap(dpi=dpi).save(image)
        _run_tesseract(image, work / "out", language, psm, tessdata, "txt")
        return (work / "out.txt").read_text(encoding="utf-8", errors="replace")


def ocr_document(doc: pymupdf.Document, *, language="jpn+eng", dpi=300,
                 psm=DEFAULT_PSM, pages: list[int] | None = None,
                 force: bool = False) -> dict:
    """Rebuild pages so their recognised text is really embedded in the file.

    Tesseract is asked for PDF output directly: that carries an invisible text
    layer aligned to the image, which is exactly what a searchable scan is.
    Driving the executable rather than the library binding is what lets the
    language and layout mode actually take effect.
    """
    tessdata = _ensure_tessdata()
    targets = sorted(set(pages)) if pages else list(range(doc.page_count))
    done = 0
    characters = 0
    skipped = 0

    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        for index in targets:
            page = doc[index]
            if not force and page.get_text().strip():
                skipped += 1  # already searchable; OCR would only add noise
                continue
            image = work / f"page-{index}.png"
            page.get_pixmap(dpi=dpi).save(image)
            stem = work / f"out-{index}"
            _run_tesseract(image, stem, language, psm, tessdata, "pdf")

            # Open from bytes: show_pdf_page keeps the source document alive
            # until the target is written, which would hold the temp file open.
            recognised = pymupdf.open("pdf", stem.with_suffix(".pdf").read_bytes())
            rect = page.rect
            doc.delete_page(index)
            new_page = doc.new_page(pno=index, width=rect.width, height=rect.height)
            new_page.show_pdf_page(new_page.rect, recognised, 0)
            recognised.close()
            characters += len(doc[index].get_text().strip())
            done += 1

    return {"pages": done, "characters": characters, "skipped": skipped}


# ---------------------------------------------------------------- searching


def search_relaxed(page: pymupdf.Page, needle: str) -> list[pymupdf.Quad]:
    """Find a phrase while ignoring whitespace and line breaks.

    OCR output puts spaces between Japanese characters ("契約 金額"), and normal
    documents break phrases across lines, so an exact search misses text the
    reader can plainly see. This walks the page character by character, matches
    against the whitespace-stripped string, and rebuilds quads from the
    characters that matched.
    """
    target = "".join(needle.split())
    if not target:
        return []

    chars: list[tuple[str, pymupdf.Rect]] = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    glyph = char.get("c", "")
                    if glyph.strip():
                        chars.append((glyph, pymupdf.Rect(char["bbox"])))

    haystack = "".join(c for c, _ in chars)
    results: list[pymupdf.Quad] = []
    start = haystack.find(target)
    while start != -1:
        matched = chars[start : start + len(target)]
        # One quad per line, so a match spanning a line break stays accurate.
        run: list[pymupdf.Rect] = []
        for _, rect in matched:
            if run and abs(rect.y0 - run[-1].y0) > run[-1].height * 0.6:
                results.append(_union(run).quad)
                run = []
            run.append(rect)
        if run:
            results.append(_union(run).quad)
        start = haystack.find(target, start + 1)
    return results


def _union(rects: list[pymupdf.Rect]) -> pymupdf.Rect:
    box = pymupdf.Rect(rects[0])
    for rect in rects[1:]:
        box |= rect
    return box


# ---------------------------------------------------------------- text editing


def find_text_blocks(page: pymupdf.Page) -> list[dict]:
    """Editable text spans, with the geometry needed to replace them in place."""
    blocks = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text", "")
                if not text.strip():
                    continue
                blocks.append({
                    "text": text,
                    "rect": list(span["bbox"]),
                    "size": span.get("size", 11),
                    "font": span.get("font", ""),
                    "colour": "#%06x" % (span.get("color", 0) & 0xFFFFFF),
                    "page": page.number,
                })
    return blocks


def replace_text(page: pymupdf.Page, rect: list[float], new_text: str, *,
                 size: float = 11, colour: str = "#000000",
                 align: int = 0, background: str | None = None) -> None:
    """Remove the text inside `rect` and lay new text in its place.

    Redaction is what actually deletes the old glyphs — covering them would
    leave the original selectable underneath.
    """
    box = pymupdf.Rect(rect)
    page.add_redact_annot(box, fill=hex_to_rgb(background) if background else None)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)
    if not new_text:
        return
    grown = pymupdf.Rect(box.x0, box.y0 - 1, box.x1 + 2, box.y1 + 4)
    page.insert_htmlbox(
        grown,
        f'<span style="font-size:{size}pt;color:{colour};'
        f'text-align:{["left", "center", "right"][align]}">{_escape(new_text)}</span>',
    )


def search_replace(doc: pymupdf.Document, needle: str, replacement: str, *,
                   size: float | None = None, colour: str = "#000000") -> int:
    count = 0
    for page in doc:
        hits = page.search_for(needle, flags=pymupdf.TEXTFLAGS_SEARCH)
        if not hits:
            continue
        spans = {tuple(round(v, 1) for v in b["rect"]): b for b in find_text_blocks(page)}
        for rect in hits:
            match = None
            for key, block in spans.items():
                if pymupdf.Rect(key).intersects(rect):
                    match = block
                    break
            replace_text(
                page, list(rect), replacement,
                size=size or (match or {}).get("size", 11),
                colour=colour,
            )
            count += 1
    return count


# ---------------------------------------------------------------- images


def list_images(page: pymupdf.Page) -> list[dict]:
    out = []
    for info in page.get_images(full=True):
        xref = info[0]
        for rect in page.get_image_rects(xref):
            out.append({
                "xref": xref,
                "rect": list(rect),
                "width": info[2],
                "height": info[3],
                "page": page.number,
            })
    return out


def insert_image(page: pymupdf.Page, rect: list[float], data: bytes) -> None:
    page.insert_image(pymupdf.Rect(rect), stream=data, keep_proportion=True)


def replace_image(doc: pymupdf.Document, xref: int, data: bytes) -> None:
    doc.replace_image(xref, stream=data)


def delete_image(doc: pymupdf.Document, xref: int) -> None:
    doc.delete_image(xref)


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
