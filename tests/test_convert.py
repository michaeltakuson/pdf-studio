"""PDF <-> Word / PowerPoint / Excel round trips (engine only). Run: python tests/test_convert.py"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webapp", "py"))
import pymupdf  # noqa: E402

from pdfstudio import convert, from_docx, from_pptx, from_xlsx, to_docx, to_pptx, to_xlsx  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "sample.pdf")


def text_of(pdf_bytes):
    doc = pymupdf.open("pdf", pdf_bytes)
    return "".join(p.get_text() for p in doc)


def main():
    doc = pymupdf.open(SAMPLE)
    for mode in ("flow", "layout"):
        data, _ = to_docx.convert(doc, mode)
        pdf = from_docx.convert(data)
        assert "PDF Studio" in text_of(pdf), f"docx {mode}: text lost"
    data, _ = to_pptx.convert(doc)
    assert "PDF Studio" in text_of(from_pptx.convert(data)), "pptx: text lost"
    data, _ = to_xlsx.convert(doc)
    pdf, _ = from_xlsx.convert(data)
    assert text_of(pdf).strip(), "xlsx: empty"
    # merge, images, split by size
    merged = convert.merge_pdfs([{"name": "a.pdf", "data": open(SAMPLE, "rb").read()}] * 2, bookmarks=True)
    assert pymupdf.open("pdf", merged).page_count == 2 * doc.page_count
    zipped = convert.split_by_size(doc, "s", 10 ** 9)
    assert zipped[:2] == b"PK"
    assert convert.sniff("x.bin", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == "ole"
    # a phone photo stored sideways (EXIF orientation 6) comes out turned upright
    try:
        from PIL import Image
        import io as _io
        im = Image.new("RGB", (200, 100), (255, 0, 0))
        ex = im.getexif()
        ex[274] = 6
        buf = _io.BytesIO()
        im.save(buf, "JPEG", exif=ex)
        jpeg = buf.getvalue()
        assert convert.exif_orientation(jpeg) == 6
        pdf_bytes = convert.images_to_pdf([("p.jpg", jpeg)], paper="fit")
        page = pymupdf.open("pdf", pdf_bytes)[0]
        assert page.rect.height > page.rect.width, page.rect   # 200x100 turned a quarter: now taller than wide
    except ImportError:
        pass

    print("convert tests passed")


if __name__ == "__main__":
    main()
