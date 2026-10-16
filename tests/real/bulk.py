import sys, glob, time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'webapp'/'py'))
import pymupdf
from pdfstudio import to_docx
files = sys.argv[1:]
for f in files:
    try:
        d = pymupdf.open(f)
        n = min(len(d), 6)
        t = time.time()
        data, w = to_docx.convert(d, 'flow', list(range(n)))
        print('OK ', Path(f).name[:40], n, len(data), round(time.time()-t,1), w[:1])
    except Exception as e:
        import traceback; traceback.print_exc(); print('ERR', f, e)
