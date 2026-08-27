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
        # True when the file arrived encrypted: the working copy is not, and
        # the reader should be told so.
        self.was_protected = False
        # True when the file arrived encrypted. The working copy is not, so the
        # app says so rather than letting the protection quietly disappear.
        self.was_protected = False

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


class PasswordRequired(Exception):
    """The file is encrypted and the password given (if any) did not open it."""

    def __init__(self, wrong_password: bool = False):
        self.wrong_password = wrong_password
        super().__init__(
            "パスワードが違います" if wrong_password else "この文書はパスワードで保護されています"
        )


_docs: dict[str, Doc] = {}


def _unlock(data: bytes, password: str) -> tuple[bytes, bool]:
    """Return openable bytes, and whether the source was password-protected.

    An encrypted source is rewritten without its protection, because the
    working copy has to be readable for every later operation. Authenticating
    in place is not enough — MuPDF still refuses to hand over page text until
    the document has been written out decrypted.
    """
    probe = pymupdf.open("pdf", data)
    try:
        if not probe.needs_pass:
            return data, False
        if not password:
            raise PasswordRequired()
        if not probe.authenticate(password):
            raise PasswordRequired(wrong_password=True)
        return probe.tobytes(
            garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE,
        ), True
    finally:
        probe.close()


def create(name: str, data: bytes, password: str = "") -> Doc:
    data, was_protected = _unlock(data, password)
    doc_id = uuid.uuid4().hex[:12]
    folder = WORK_DIR / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "current.pdf"
    path.write_bytes(data)
    doc = Doc(doc_id, name, path)
    doc.was_protected = was_protected
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
