import sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'webapp'/'py'))
import pymupdf
from pdfstudio import convert
ok = bad = 0
for f in sys.argv[1:]:
    try:
        data = Path(f).read_bytes()
        t = time.time()
        pdf, w = convert.office_to_pdf(Path(f).name, data)
        n = pymupdf.open('pdf', pdf).page_count
        ok += 1
        print('OK ', n, round(time.time()-t,1), Path(f).name[:50])
    except Exception as e:
        bad += 1; print('ERR', Path(f).name[:50], repr(e)[:120])
print('ok', ok, 'bad', bad)
