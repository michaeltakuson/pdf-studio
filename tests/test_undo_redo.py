"""Page-level undo and redo in the engine: delete, undo, redo, and a new edit ending the redo line."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "webapp" / "py"))

from pdfstudio import bridge, session  # noqa: E402

data = (pathlib.Path(__file__).parent / "sample.pdf").read_bytes()
entry = session.create("s.pdf", data)
n0 = entry.doc.page_count
assert n0 >= 3

entry.snapshot("before-delete")
entry.doc.delete_page(1)
assert entry.doc.page_count == n0 - 1

assert entry.restore() == "before-delete" and entry.doc.page_count == n0
assert len(entry.redos) == 1
assert entry.redo() == "before-delete" and entry.doc.page_count == n0 - 1
assert len(entry.snapshots) == 1 and not entry.redos
assert entry.restore() and entry.doc.page_count == n0

# a new operation ends the redo line
entry.snapshot("before-other")
entry.doc.delete_page(0)
assert entry.restore() and entry.redos
entry.snapshot("before-new")
assert not entry.redos and entry.redo() is None

described = bridge._describe(entry)
assert "redoDepth" in described and described["redoDepth"] == 0
print("undo/redo tests passed")
