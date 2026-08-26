"""Open-document registry.

One process, one user, so documents simply stay open in memory. Each document
gets a working directory holding the live file plus timestamped snapshots, so
destructive operations (flatten, redaction, optimisation) always have
something to fall back to.
"""

from __future__ import annotations

import datetime
import shutil
import threading
import uuid
from pathlib import Path

import pymupdf

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WORK_DIR = DATA_DIR / "work"


class Doc:
    def __init__(self, doc_id: str, name: str, path: Path):
        self.id = doc_id
        self.name = name
        self.path = path
        self.doc = pymupdf.open(path)
        self.lock = threading.RLock()

    @property
    def dir(self) -> Path:
        return self.path.parent

    def snapshot(self, label: str) -> Path:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.dir / "snapshots" / f"{stamp}-{label}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, target)
        return target

    def commit(self) -> None:
        """Write the in-memory document out as a fresh, fully rewritten file.

        A plain incremental save would leave superseded objects in the file —
        the "overwriting does not delete the page" trap. Rewriting from
        scratch with garbage collection is the only way deleted content
        actually leaves the file.
        """
        tmp = self.path.with_suffix(".tmp.pdf")
        self.doc.save(tmp, garbage=3, deflate=True, clean=True)
        self.doc.close()
        tmp.replace(self.path)
        self.doc = pymupdf.open(self.path)

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass


_docs: dict[str, Doc] = {}


def create(name: str, data: bytes) -> Doc:
    doc_id = uuid.uuid4().hex[:12]
    folder = WORK_DIR / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "current.pdf"
    path.write_bytes(data)
    doc = Doc(doc_id, name, path)
    _docs[doc_id] = doc
    return doc


def create_blank(name: str = "無題.pdf", width: float = 595, height: float = 842) -> Doc:
    blank = pymupdf.open()
    blank.new_page(width=width, height=height)
    data = blank.tobytes()
    blank.close()
    return create(name, data)


def get(doc_id: str) -> Doc:
    doc = _docs.get(doc_id)
    if doc is None:
        raise KeyError(doc_id)
    return doc


def close(doc_id: str) -> None:
    doc = _docs.pop(doc_id, None)
    if doc:
        doc.close()


def all_docs() -> list[Doc]:
    return list(_docs.values())
