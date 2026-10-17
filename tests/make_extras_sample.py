"""A small "scanned application form" for the everyday-extras UI test.

Six pages scanned one side at a time: the three fronts, then the backs in
reverse order, one of them blank. The first front carries a telephone number,
an e-mail address, a My Number and a postcode.

    python tests/make_extras_sample.py   ->  tests/_extras_sample.pdf
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webapp", "py"))

import pymupdf  # noqa: E402

from pdfstudio import extras  # noqa: E402


def my_number(body: str) -> str:
    for digit in "0123456789":
        if extras._my_number_ok(body + digit):
            return body + digit
    raise AssertionError


def main() -> str:
    doc = pymupdf.open()

    def page(label, lines=(), blank=False):
        p = doc.new_page()
        p.draw_rect(p.rect, fill=(0.95, 0.95, 0.94), color=None)  # the grey of scanned paper
        if blank:
            return
        p.insert_text((60, 80), label, fontname="japan", fontsize=20)
        y = 120
        for text in lines:
            p.insert_text((60, y), text, fontname="japan", fontsize=11)
            y += 20

    page("表1 申込書", ["氏名 山田花子  電話 03-5555-1234", "メール hanako@example.jp",
                     f"マイナンバー {my_number('98765432109')}", "〒150-0001 東京都渋谷区"])
    page("表2 契約条件", ["携帯 080-1111-2222 (担当)"])
    page("表3 確認事項", ["問い合わせ 0120-123-456"])
    page("裏3 メモ", ["特記事項なし"])
    page("", blank=True)
    page("裏1 注意書き", ["個人情報の取り扱いについて"])
    out = os.path.join(os.path.dirname(__file__), "_extras_sample.pdf")
    doc.save(out)
    return out


if __name__ == "__main__":
    print(main())
