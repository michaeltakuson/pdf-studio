"""Inspect the most recently saved working document from outside the app.

This is the portability check: if these annotations look right here, they look
right in Acrobat, Edge and everything else that reads standard PDF.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymupdf


def main(path: str | None = None) -> None:
    if path is None:
        candidates = sorted(
            glob.glob(str(Path(__file__).parent.parent / "data" / "work" / "*" / "current.pdf")),
            key=os.path.getmtime,
        )
        if not candidates:
            print("保存済みの作業ファイルが見つかりません")
            return
        path = candidates[-1]

    doc = pymupdf.open(path)
    print(f"file: {path}")
    print(f"pages: {doc.page_count}   size: {os.path.getsize(path) / 1024:.0f} KB")
    total = 0
    for page in doc:
        for annot in page.annots():
            total += 1
            info = annot.info
            rect = tuple(round(v) for v in annot.rect)
            extra = ""
            if annot.type[1] == "Ink":
                extra = f" strokes={len(annot.vertices or [])}"
            elif annot.vertices:
                extra = f" verts={len(annot.vertices)}"
            print(
                f"  p{page.number} {annot.type[1]:<12} rect={rect}"
                f" colour={annot.colors.get('stroke')}"
                f" author={info.get('title')!r}{extra}"
            )
    print(f"total annotations: {total}")
    doc.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
