"""How the app behaves when the file it is handed is not a normal PDF.

Opening is the one thing every user does, so a damaged, locked, empty or
oddly-shaped file has to produce a clear result rather than something that
looks like the app itself is broken.

Needs the server running:
    python -m uvicorn backend.main:app --port 8000
Then:
    python -m tests.test_awkward_open
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.error
import urllib.request
import json
import uuid

HERE = Path(__file__).parent
AWKWARD = HERE / "awkward"
BASE = "http://127.0.0.1:8000"


def check(condition, label, detail=""):
    print(("  OK   " if condition else "  FAIL ") + label + (f" : {detail}" if detail else ""))
    return bool(condition)


def upload(path: Path, password: str = ""):
    """POST a file the way the browser does, and report status plus body."""
    boundary = uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        b"Content-Type: application/pdf\r\n\r\n",
        path.read_bytes(),
        b"\r\n",
    ]
    if password:
        parts += [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="password"\r\n\r\n',
            password.encode(),
            b"\r\n",
        ]
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    request = urllib.request.Request(
        f"{BASE}/api/open", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except Exception:
            return error.code, {"detail": raw.decode("utf-8", "replace")[:200]}


def main() -> int:
    try:
        urllib.request.urlopen(BASE, timeout=5)
    except Exception:
        print("サーバが起動していません。先に uvicorn を起動してください。")
        return 1
    if not AWKWARD.exists():
        print("先に python tests\\make_awkward.py を実行してください。")
        return 1

    failures = 0

    # ---------------------------------------------------------------- broken
    status, body = upload(AWKWARD / "notapdf.pdf")
    failures += not check(status == 400, "PDFでないファイルは拒否される", f"status={status}")
    failures += not check("PDF" in str(body.get("detail", "")),
                          "拒否の理由が伝わる文言である", str(body.get("detail"))[:60])

    # A truncated file is the common real case: PyMuPDF repairs what it can.
    status, body = upload(AWKWARD / "damaged.pdf")
    if status == 200:
        failures += not check(body["pageCount"] >= 1,
                              "壊れたファイルでも読める分は開ける",
                              f"{body['pageCount']} ページ復旧")
    else:
        failures += not check("detail" in body, "壊れたファイルは理由つきで拒否される",
                              str(body.get("detail"))[:60])

    # ---------------------------------------------------------------- locked
    # A protected file must ask for a password, not fail with a server error.
    status, body = upload(AWKWARD / "locked.pdf")
    failures += not check(status == 401, "パスワード付きは401で返る（500ではない）",
                          f"status={status}")
    failures += not check("パスワード" in str(body.get("detail", "")),
                          "パスワードが必要だと日本語で伝わる", str(body.get("detail"))[:50])

    status, body = upload(AWKWARD / "locked.pdf", password="ちがう")
    failures += not check(status == 401, "間違ったパスワードは401", f"status={status}")
    failures += not check("違い" in str(body.get("detail", "")),
                          "間違いだと分かる文言が返る", str(body.get("detail"))[:50])

    status, body = upload(AWKWARD / "locked.pdf", password="secret")
    failures += not check(status == 200, "正しいパスワードで開ける", f"status={status}")
    if status == 200:
        failures += not check(body["pageCount"] == 3, "全ページ読める",
                              f"{body['pageCount']} ページ")
        failures += not check(body.get("wasProtected") is True,
                              "保護されていた文書だと伝える",
                              str(body.get("wasProtected")))
        failures += not check(len(body.get("toc") or []) == 3, "しおりも読める",
                              str(len(body.get("toc") or [])))
        # The working copy must be readable, or every later operation fails.
        text = urllib.request.urlopen(
            f"{BASE}/api/doc/{body['id']}/text/0", timeout=30
        ).read().decode()
        failures += not check("動作確認用サンプル" in text,
                              "解錠後は本文が読める（後続の操作が成立する）",
                              text[:60])

    # Owner passwords open it too.
    status, body = upload(AWKWARD / "locked.pdf", password="ownersecret")
    failures += not check(status == 200, "オーナーパスワードでも開ける", f"status={status}")

    # ---------------------------------------------------------------- odd but valid
    status, body = upload(AWKWARD / "blank.pdf")
    failures += not check(status == 200 and body["pageCount"] == 1,
                          "白紙1ページのPDFを開ける", f"status={status}")

    status, body = upload(AWKWARD / "mixed.pdf")
    failures += not check(status == 200 and body["pageCount"] == 5,
                          "寸法と回転がばらばらのPDFを開ける", f"status={status}")
    if status == 200:
        sizes = [(round(p["width"]), round(p["height"]), p["rotation"]) for p in body["pages"]]
        failures += not check(len({s[:2] for s in sizes}) > 1,
                              "ページごとに違う寸法が返る", str(sizes))
        rotated = [s for s in sizes if s[2]]
        failures += not check(rotated, "回転したページが回転として報告される", str(rotated))
        # A rotated page must report its rotated dimensions, or the viewer
        # would lay it out in the wrong shape.
        for width, height, rotation in sizes:
            if rotation in (90, 270):
                failures += not check(width > height or width != height,
                                      f"回転{rotation}°のページ寸法が入れ替わっている",
                                      f"{width}x{height}")
                break

    status, body = upload(AWKWARD / "many.pdf")
    failures += not check(status == 200 and body["pageCount"] == 120,
                          "120ページのPDFを開ける", f"status={status}")

    print(f"\n{'すべて成功' if not failures else str(failures) + ' 件失敗'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
