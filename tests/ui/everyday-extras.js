// 仕事・勉強の便利機能: 両面スキャンの並べ替え、白紙ページの削除、個人情報の墨消し、白黒化、
// 罫線つきの余白、ページ番号（表紙なし）、透かしの定番文言、QRコード、改行をつなげてコピー、保存確認の3択
// 使い方: 先に `python tests/make_extras_sample.py` で tests/_extras_sample.pdf を作り、setup（アプリを開いて
// エンジンの準備を待つ）してから、この関数を browser_run_code_unsafe で実行する。
// コピーの確認にはクリップボードの読み取り許可が要る（context.grantPermissions）
async (page) => {
  const out = {};
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const wait = (ms) => page.waitForTimeout(ms);
  const idle = async () => {
    await wait(300);
    for (let i = 0; i < 600 && await page.evaluate(() => !document.querySelector('#busy').hidden); i += 1) await wait(200);
  };
  const tab = (name) => page.locator('#ribbonTabs .tab', { hasText: name }).click();
  const button = async (label) => {
    const index = await page.evaluate((want) => [...document.querySelectorAll('.rbtn')]
      .findIndex((b) => b.offsetParent && (b.querySelector('.lbl')?.textContent || '').replace(/\s/g, '') === want), label);
    await page.locator('.rbtn').nth(index).click();
    await wait(300);
  };
  const dialogButton = (text) => page.locator('.dialog button', { hasText: text }).click();
  const toast = () => page.locator('#toast').textContent();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(page.url()).origin }).catch(() => {});

  await page.locator('#fileInput').setInputFiles('C:/Users/ttaku/Downloads/pdf-studio/tests/_extras_sample.pdf');
  await page.waitForFunction(() => /_extras_sample\.pdf/.test(document.title), null, { timeout: 60000 });
  await wait(2500);
  const discard = page.locator('.dialog .btn', { hasText: '破棄する' });
  if (await discard.count()) await discard.click();

  // 両面スキャン → 表1・裏1・表2…の順に
  await tab('ページ');
  await button('両面スキャンを並べる');
  await dialogButton('並べ替える'); await idle();
  out.duplex = await toast();

  // 白紙ページ（裏2）が1ページだけ見つかり、削除される
  await button('白紙ページを削除');
  await dialogButton('探す'); await idle();
  out.blankDialog = await page.locator('.dialog h2').textContent();
  await dialogButton('ページを削除'); await idle();
  out.pagesAfterBlank = await page.locator('.page-wrap').count();

  // 個人情報: 6件 → 5件を指定 → 適用
  await tab('校閲');
  await button('個人情報を検出');
  await dialogButton('探す'); await idle();
  out.piiFound = await page.locator('.pii-list .pii-row:not(.pii-all)').count();
  await page.locator('.pii-list .pii-row:not(.pii-all) input').last().uncheck();
  await dialogButton('墨消しを指定する'); await idle();
  await button('墨消しを適用');
  await dialogButton('適用して削除する'); await idle();
  out.statusAfterRedaction = await page.locator('#statusLeft').textContent();

  // 白黒にする → 元に戻す
  await tab('ページ');
  await button('白黒にする');
  await dialogButton('白黒にする'); await idle();
  out.grayscale = await toast();
  await page.keyboard.press('Control+z'); await idle();

  // 罫線つきの余白
  const ratio = () => page.evaluate(() => { const r = document.querySelector('.page-wrap').getBoundingClientRect(); return r.width / r.height; });
  const before = await ratio();
  await button('余白を足す');
  await page.locator('.dialog select').nth(1).selectOption('grid');
  await dialogButton('余白を足す'); await idle();
  out.wider = (await ratio()) > before + 0.1;
  await page.keyboard.press('Control+z'); await idle();

  // ページ番号（表紙なし・外側）と透かし（定番の文言）
  await tab('挿入');
  await button('ページ番号');
  await page.locator('.dialog select').first().selectOption('outside');
  await page.locator('.dialog input[type=number]').first().fill('1');
  await dialogButton('入れる'); await idle();
  out.pageNumbers = await toast();
  await button('透かし');
  await page.locator('.dialog select').first().selectOption('下書き');
  await dialogButton('入れる'); await idle();
  out.watermark = await toast();

  // QRコード
  await button('QRコード');
  await page.locator('.dialog textarea').fill('https://example.com/form');
  await dialogButton('入れる'); await wait(800);
  out.qr = await page.locator('#statusLeft').textContent();

  // 改行をつなげてコピー
  await page.keyboard.press('Escape');
  const spans = await page.evaluate(() => [...document.querySelectorAll('.page-wrap')[0].querySelectorAll('.text-layer span')]
    .filter((s) => s.textContent.trim().length > 3)
    .map((s) => { const r = s.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; }));
  const [a, b] = [spans[1], spans[2]];
  await page.mouse.move(a.x + 2, a.y + a.h / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.w - 2, b.y + b.h / 2, { steps: 10 });
  await page.mouse.up();
  await wait(500);
  await page.locator('#selectionBar button[title^="改行をつなげて"]').click();
  await wait(300);
  out.copied = await page.evaluate(() => navigator.clipboard.readText().catch(() => '(読めません)'));

  // 未保存で閉じる → 3択（戻る／保存せずに進む／保存して進む）
  await tab('ファイル');
  await page.locator('.menu button', { hasText: '閉じる' }).click(); await wait(400);
  out.choices = await page.locator('.dialog .dialog-actions button').allTextContents();
  await dialogButton('保存せずに進む'); await wait(1200);
  out.closed = await page.locator('#emptyState').isVisible();
  out.errors = errors;
  return out;
}
