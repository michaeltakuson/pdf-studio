"""Convert a PDF to docx with the engine and print the paragraphs (for eyeballing structure)."""
import io
import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parents[2] / "webapp" / "py"
sys.path.insert(0, str(root))
import pymupdf  # noqa: E402
from pdfstudio import to_docx  # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "real.pdf")
mode = sys.argv[2] if len(sys.argv) > 2 else "flow"
doc = pymupdf.open(src)
data, warnings = to_docx.convert(doc, mode)
out = Path(__file__).parent / f"probe_{mode}.docx"
out.write_bytes(data)
x = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode()
paras = re.findall(r"<w:p[ >].*?</w:p>", x, flags=re.S)
print(len(paras), "paragraphs; warnings:", warnings)
for p in paras[: int(sys.argv[3]) if len(sys.argv) > 3 else 30]:
    t = p.replace("<w:tab/>", "→").replace("<w:br/>", "⏎")
    t = re.sub(r"<[^>]+>", "", t)
    print(f"{len(t):4d} {t[:96]}")
