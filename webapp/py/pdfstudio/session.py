"""Open-document registry — the browser-only variant.

The server version kept each open document as a file under data/work/ so it
would survive across requests to a stateless process. Here everything lives
inside one Pyodide runtime for the lifetime of the tab, so a document is just
bytes and a pymupdf.Document held in memory. Snapshots (the safety net before
destructive operations) are kept the same way: in a dict, not on disk. They
are lost on a page reload, which is the one real trade-off of this build —
see the notice on the "使い方" page.
"""

from __future__ import annotations

import uuid

import pymupdf


class PasswordRequired(Exception):
    """The file is encrypted and the password given (if any) did not open it."""

    def __init__(self, wrong_password: bool = False):
        self.wrong_password = wrong_password
        super().__init__(
            "パスワードが違います" if wrong_password else "この文書はパスワードで保護されています"
        )


class Doc:
    def __init__(self, doc_id: str, name: str, data: bytes):
        self.id = doc_id
        self.name = name
        self.doc = pymupdf.open("pdf", data)
        self.was_protected = False
        # label -> pdf bytes, most recent first. Kept short: this is an
        # in-tab undo net for destructive operations, not archival storage.
        self.snapshots: list[tuple[str, bytes]] = []

    def snapshot(self, label: str) -> str:
        self.snapshots.insert(0, (label, self.doc.tobytes()))
        del self.snapshots[20:]
        return label

    def commit(self) -> None:
        """Rewrite the in-memory document from scratch.

        A plain incremental save leaves superseded objects in the file — the
        "overwriting does not delete the page" trap. Rewriting with garbage
        collection is what actually drops deleted content.
        """
        data = self.doc.tobytes(garbage=3, deflate=True, clean=True)
        self.doc.close()
        self.doc = pymupdf.open("pdf", data)

    def bytes(self) -> bytes:
        return self.doc.tobytes(garbage=3, deflate=True, clean=True)

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass


_docs: dict[str, Doc] = {}


def _unlock(data: bytes, password: str) -> tuple[bytes, bool]:
    """Return openable bytes, and whether the source was password-protected.

    An encrypted source is rewritten without its protection, because the
    working copy has to be readable for every later operation. Authenticating
    in place is not enough — MuPDF still refuses page text until the document
    has been written out decrypted.
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
    doc = Doc(doc_id, name, data)
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
