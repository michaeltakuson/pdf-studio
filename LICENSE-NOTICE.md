# ライセンスについて（公開前に必ずお読みください）

このアプリは PDF の中身を **PyMuPDF** で読み書きしています。PyMuPDF のライセンスは
**GNU AFFERO GPL 3.0（AGPL-3.0）または Artifex の商用ライセンス**の二者択一です。

商用ライセンスを購入していない場合、選べるのは AGPL-3.0 だけです。そして AGPL-3.0 には
次の条件があります。

| | 内容 |
|---|---|
| **全体が AGPL になる** | PyMuPDF を組み込んだこのアプリ全体を AGPL-3.0 で配布する必要があります |
| **ソースの公開** | 配布する相手に、対応するソースコードを提供する必要があります |
| **ネットワーク越しの利用も「配布」に当たる** | AGPL 独自の条項です。サーバに置いて他人にブラウザから使わせる場合、**その利用者に対してソースを提供する義務が生じます** |

## だから、どうすればよいか

### GitHub で公開する場合 — これで条件を満たせます

同梱の `LICENSE`（AGPL-3.0 全文）をリポジトリのルートに置いたまま、**ソースコード一式を
公開リポジトリに置いてください。** これで「ソースの提供」も「ネットワーク利用者への提供」も
満たされます。README にリポジトリの URL を書いておけば十分です。

**非公開（private）リポジトリでも、自分だけで使う分には問題ありません。**
AGPL の義務が生じるのは、他人に配布したときとネットワーク越しに使わせたときだけです。

### やってはいけないこと

- **ソースを公開せずに、他人が使えるサーバに置くこと。** これは AGPL 違反になります
- **ソースを公開せずに配布・販売すること。** 同上

商用利用でソースを公開したくない場合は、Artifex から PyMuPDF の商用ライセンスを
購入する必要があります（有償）。

## 同梱・利用している他のソフトとフォント

| もの | ライセンス | 置き場所・備考 |
|---|---|---|
| PyMuPDF（WebAssembly 版） | AGPL-3.0 / 商用 | `webapp/vendor/pymupdf-wasm/`。上記のとおり、このアプリの制約はこれが決めている |
| pdf.js | Apache-2.0 | `webapp/vendor/pdfjs/`。`LICENSE` に全文あり。**削除しないでください**（著作権表示の保持にあたります） |
| BIZ UDPGothic / BIZ UDPMincho | SIL Open Font License 1.1 | `webapp/vendor/fonts/`。© The BIZ UDGothic / UDMincho Project Authors（モリサワ） |
| Zen Maru Gothic | SIL Open Font License 1.1 | 同上。© The Zen Maru Gothic Project Authors |
| Klee One | SIL Open Font License 1.1 | 同上。© The Klee Project Authors（フォントワークス） |
| Yomogi | SIL Open Font License 1.1 | 同上。© The Yomogi Project Authors |
| Pyodide | MPL-2.0 | 同梱していない。起動時に CDN（jsDelivr）から読み込む |
| fontTools | MIT | 同梱していない。文字を保存するときに Pyodide のパッケージとして読み込む |
| tesseract.js と言語データ | Apache-2.0 | 同梱していない。文字認識（OCR）を初めて使うときに CDN から読み込む |

フォントの `OFL-*.txt`（ライセンス全文）は、フォントファイルと同じフォルダに置いたままにしてください。
SIL OFL は、フォントを**文書に埋め込むこと**も、ソフトに**同梱して再配布すること**も認めています
（フォント単体を販売することだけが禁じられています）。このアプリで作ったPDFに埋め込まれる文字の形について、
利用者に追加の義務は生じません。
