// ページ操作の「元に戻す／やり直し」、透かしを入れて取り除く、しおりの追加と取り消し、ファイルを閉じる
// 使い方: 先に setup（アプリを開いてエンジンの準備を待つ）してから、この関数を browser_run_code_unsafe で実行する
async (page) => {
  const out = {};
  const pages = () => page.locator('.page-wrap').count();
  const wait = (ms) => page.waitForTimeout(ms);
  await page.locator('#fileInput').setInputFiles('C:/Users/ttaku/Downloads/pdf-studio/tests/sample.pdf');
  await page.waitForFunction(() => /sample\.pdf/.test(document.title), null, { timeout: 60000 });
  await wait(2500);
  const discard = page.locator('.dialog .btn', { hasText: '破棄する' });
  if (await discard.count()) await discard.click();

  // ページを削除 → 元に戻す → やり直し
  out.start = await pages();
  await page.locator('.thumb-item').nth(1).click({ button: 'right' });
  await page.locator('.menu button').filter({ hasText: /^削除$/ }).last().click();
  await page.locator('.dialog .btn', { hasText: '削除する' }).click();
  await wait(3500);
  out.deleted = await pages();
  await page.keyboard.press('Control+z'); await wait(3000);
  out.undone = await pages();
  await page.keyboard.press('Control+y'); await wait(3000);
  out.redone = await pages();
  await page.keyboard.press('Control+z'); await wait(3000);

  // 透かし → 取り除く（保存したあとでも外せる印が付いている）
  await page.locator('#ribbonTabs .tab', { hasText: '挿入' }).click();
  await page.locator('.ribbon button', { hasText: '透かし' }).first().click();
  await page.waitForSelector('.dialog'); await page.locator('.dialog .btn.primary').click(); await wait(3000);
  await page.locator('.ribbon button', { hasText: '取り除く' }).first().click();
  await page.waitForSelector('.dialog');
  out.removeDialog = (await page.locator('.dialog').innerText()).includes('透かし');
  await page.locator('.dialog .btn.primary').click(); await wait(3000);
  out.toast = await page.locator('.toast').allTextContents();

  // ファイルを閉じる
  await page.locator('#ribbonTabs .tab', { hasText: 'ファイル' }).click();
  await page.locator('.menu button', { hasText: '閉じる' }).click(); await wait(500);
  const leave = page.locator('.dialog .btn', { hasText: '保存せずに進む' });
  if (await leave.count()) await leave.click();
  await wait(1500);
  out.closed = await page.locator('#emptyState').isVisible();
  return out;
}
