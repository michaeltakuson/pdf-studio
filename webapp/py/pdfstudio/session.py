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
        self.restrictions: list[str] = []
        # label -> pdf bytes, most recent first. Kept short: this is an
        # in-tab undo net for destructive operations, not archival storage.
        self.snapshots: list[tuple[str, bytes]] = []
        # The states stepped back over, most recent first, so "redo" can return to them.
        self.redos: list[tuple[str, bytes]] = []
        # id -> canonical form of each annotation as last read or written, so
        # a save only rewrites the ones that actually changed.
        self.baseline: dict[str, str] = {}

    def snapshot(self, label: str, *, keep_redo: bool = False) -> str:
        if not keep_redo:
            self.redos.clear()  # a new operation ends the redo line
        self.snapshots.insert(0, (label, self.doc.tobytes()))
        # Bounded by size as well as count: twenty copies of a 50 MB scan
        # would take the tab down.
        total = 0
        for index, (_, data) in enumerate(self.snapshots):
            total += len(data)
            if index >= 1 and (index >= 15 or total > 300_000_000):
                del self.snapshots[index:]
                break
        return label

    def restore(self) -> str | None:
        """Go back to the most recent snapshot. Returns its label, or None."""
        if not self.snapshots:
            return None
        label, data = self.snapshots.pop(0)
        self.redos.insert(0, (label, self.doc.tobytes()))
        del self.redos[15:]
        self.doc.close()
        self.doc = pymupdf.open("pdf", data)
        return label

    def redo(self) -> str | None:
        """Go forward again after a restore. Returns the label, or None."""
        if not self.redos:
            return None
        label, data = self.redos.pop(0)
        self.snapshot(label, keep_redo=True)
        self.doc.close()
        self.doc = pymupdf.open("pdf", data)
        return label

    fonts_pending = False

    def replace(self, data: bytes) -> None:
        self.doc.close()
        self.doc = pymupdf.open("pdf", data)

    def commit(self) -> None:
        """Mark the document as changed.

        The server build rewrote the whole file here after every operation.
        In the browser that rewrite runs on the page's own thread — on a 50 MB
        scan it froze the tab for several seconds per click — and it is only
        the file handed to the user that needs it. So the rewrite happens once,
        in bytes(), when the document actually leaves the app.
        """
        self.changed = True

    def bytes(self) -> bytes:
        """The finished file, rewritten from scratch.

        A plain incremental save leaves superseded objects in the file — the
        "overwriting does not delete the page" trap. Rewriting with garbage
        collection is what actually drops deleted content.
        """
        from . import content

        data = content.compact_bytes(self.doc)
        # Once text has been written into pages with a whole font, every file
        # handed out is cut down to the glyphs used (see content.subset). It
        # stays on after an undo, which may bring back a page with such text.
        if content.subset_pending(self.doc):
            self.fonts_pending = True
        if self.fonts_pending:
            data = content.finish_fonts(data)
        return data

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


# What the author of a file forbade, worded for the reader. These are the
# permission flags of a file that opens without a password but carries an
# owner password; they are not enforced by MuPDF, so the reader is told.
_PERMISSIONS = (
    (pymupdf.PDF_PERM_MODIFY, "内容の変更"),
    (pymupdf.PDF_PERM_ANNOTATE, "注釈・書き込み"),
    (pymupdf.PDF_PERM_FORM, "フォームへの入力"),
    (pymupdf.PDF_PERM_ASSEMBLE, "ページの追加・削除・並べ替え"),
    (pymupdf.PDF_PERM_COPY, "文字のコピー"),
    (pymupdf.PDF_PERM_PRINT, "印刷"),
)


def restrictions_of(data: bytes) -> list[str]:
    """The uses the author of an encrypted-but-openable file did not allow."""
    try:
        probe = pymupdf.open("pdf", data)
    except Exception:  # noqa: BLE001 - opening reports its own errors
        return []
    try:
        if probe.needs_pass or not (probe.metadata or {}).get("encryption"):
            return []
        allowed = probe.permissions
        return [label for flag, label in _PERMISSIONS if not allowed & flag]
    finally:
        probe.close()


def create(name: str, data: bytes, password: str = "") -> Doc:
    restrictions = restrictions_of(data) if not password else []
    data, was_protected = _unlock(data, password)
    doc_id = uuid.uuid4().hex[:12]
    doc = Doc(doc_id, name, data)
    doc.was_protected = was_protected
    doc.restrictions = restrictions
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
