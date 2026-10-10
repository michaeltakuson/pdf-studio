# 公開のしくみ／ローカルで動かす

## 公開（GitHub Pages）

`main` ブランチの `webapp/` 以下を変更して push すると、GitHub Actions
（`.github/workflows/pages.yml`）が `webapp/` をそのまま GitHub Pages に公開します。
ビルド工程はありません。

公開先: https://michaeltakuson.github.io/pdf-studio/

反映には push から1〜2分かかります。また GitHub Pages はファイルを10分ほどブラウザにキャッシュさせるので、
更新直後に動きがおかしいときは **Ctrl+F5（強制再読み込み）** をしてください。

### ソースの公開について

PDF処理に使っている PyMuPDF が AGPL-3.0 なので、**このリポジトリを公開にしておくこと**が、
ネットワーク越しに使う人への「ソースの提供」になります。非公開に切り替える場合は、公開サイトも止めてください。
詳しくは [LICENSE-NOTICE.md](LICENSE-NOTICE.md)。

## ローカルで動かす

`PDF Studio を起動.bat` をダブルクリックします。中身はこれだけです。

```
cd webapp
python -m http.server 8000 --bind 127.0.0.1
```

ブラウザで http://127.0.0.1:8000/ を開きます。`file://` で `index.html` を直接開くことはできません
（ブラウザがモジュールの読み込みを拒否します）。

Pyodide（Python の実行環境、約12MB）だけは CDN から読み込むので、初回はインターネット接続が必要です。
一度読み込めばブラウザのキャッシュが効きます。

## 開発するとき

- 画面は素の JavaScript（ES Modules）で、ビルドもパッケージのインストールも要りません。ファイルを直して再読み込みするだけです
- **ブラウザはモジュールを強くキャッシュします。** 直したのに変わらないときは、開発者ツールを開いて「キャッシュを無効化」にするか、Ctrl+F5
- エンジン（`webapp/py/pdfstudio/`）は普通の Python としても動くので、テストはブラウザなしで回せます（README の「テスト」）
- 新しい操作を足すときは、`py/pdfstudio/bridge.py` に処理を書き、`js/bridge.js` の `route()` に URL を1行足します

## 構成の経緯

以前はサーバー（FastAPI）で動く版と、ブラウザだけで動く版の2つがあり、画面のコードが二重になっていました。
片方にしか入らない修正が出始めたため、ブラウザ版に一本化しています。サーバー版は git の履歴に残っています
（`71e12ca` 以前の `backend/` と `frontend/`）。サーバー版にしかなかった OCR は、ブラウザ内の文字認識に置き換えました。
