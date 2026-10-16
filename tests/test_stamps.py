"""Page furniture on a page whose content leaves the graphics state open (Google Docs / Skia output), the removal of
that furniture again, and the redaction overlay text that has to survive a read and write."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "webapp" / "py"))

import pymupdf  # noqa: E402
from pdfstudio import annots, pages  # noqa: E402


def skia_like_pdf() -> pymupdf.Document:
    doc = pymupdf.open()
    for _ in range(3):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 100), "Body text of the page", fontsize=12)
        # Skia writes "1 0 0 -1 0 842 cm" first and never restores the state
        xref = page.get_contents()[0]
        doc.update_stream(xref, b"1 0 0 -1 0 842 cm\n" + b"q 1 0 0 -1 0 842 cm\n" + doc.xref_stream(xref) + b"\nQ\n")
    return doc


def ys(page, needle):
    return [round(b[1]) for b in page.get_text("blocks") if needle in b[4]]


doc = skia_like_pdf()
pages.header_footer(doc, header="TOPLINE", footer="{page} / {pages}")
pages.bates(doc, prefix="X-")
pages.watermark(doc, "WATERMARK")
page = doc[1]
assert ys(page, "TOPLINE")[0] < 60, ys(page, "TOPLINE")           # header at the top
assert ys(page, "2 / 3")[0] > 780, ys(page, "2 / 3")               # footer at the bottom
assert ys(page, "X-000002")[0] > 780, ys(page, "X-000002")
assert page.search_for("WATERMARK")

# the stamps can be found again after a save, and taken out
data = doc.tobytes(garbage=3, deflate=True)
doc2 = pymupdf.open("pdf", data)
assert pages.count_stamps(doc2) == {"watermark": 3, "headerFooter": 3, "bates": 3}, pages.count_stamps(doc2)
assert pages.remove_stamps(doc2, {"watermark"}) == 3
assert pages.remove_stamps(doc2) == 6
assert "TOPLINE" not in doc2[0].get_text() and "Body text" in doc2[0].get_text()

# a redaction mark made by search keeps its overlay text through read -> write -> apply
doc3 = pymupdf.open()
page3 = doc3.new_page()
page3.insert_text((72, 100), "Secret Name appears here", fontsize=12)
assert pages.search_and_mark_redactions(doc3, "Secret Name", overlay="[removed]") == 1
items = annots.read_document(doc3)
assert items[0]["type"] == "redact" and items[0].get("overlayText") == "[removed]", items[0]
annots.write_document(doc3, items, annots.baseline_of(items))
pages.apply_redactions(doc3)
text = doc3[0].get_text()
assert "Secret Name" not in text and "[removed]" in text, text

# auto crop and uncrop, also after margins were added
doc4 = pymupdf.open()
page4 = doc4.new_page()
page4.insert_text((200, 300), "centre", fontsize=12)
pages.add_margins(doc4, None, left=60, top=60, right=60, bottom=60)
assert pages.auto_crop(doc4, None, 10) == 1
assert doc4[0].rect.width < 150
pages.reset_crop(doc4, [0])
assert doc4[0].rect.width > 600
print("stamp tests passed")
