// 変換ダイアログ（Word→PDF）を実機で通す例。ファイル選択は Playwright の browser_file_upload で行う
async (page) => {
  const ROOT = 'C:/Users/ttaku/Downloads/pdf-studio/webapp';
  const MIME = { html: 'text/html', js: 'text/javascript', mjs: 'text/javascript', css: 'text/css', json: 'application/json', svg: 'image/svg+xml', png: 'image/png', ttf: 'font/ttf', wasm: 'application/wasm', webmanifest: 'application/manifest+json', py: 'text/x-python', whl: 'application/zip' };
  await page.unroute('http://127.0.0.1:8765/**').catch(() => {});
  await page.route('http://127.0.0.1:8765/**', async (route) => {
    const u = new URL(route.request().url());
    let rel = decodeURIComponent(u.pathname); if (rel.endsWith('/')) rel += 'index.html';
    const f = rel.startsWith('/__files/') ? rel.slice(9) : ROOT + rel;
    const ext = f.split('.').pop();
    try { await route.fulfill({ path: f, headers: { 'content-type': MIME[ext] || 'application/octet-stream', 'cache-control': 'no-store' } }); }
    catch { await route.fulfill({ status: 404, body: 'nf' }); }
  });
  const errs = [];
  page.removeAllListeners('pageerror'); page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 300)));
  page.removeAllListeners('console'); page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  page.removeAllListeners('requestfailed'); page.on('response', (r) => { if (r.status() >= 400) errs.push('http ' + r.status() + ' ' + r.url().slice(-80)); });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('http://127.0.0.1:8765/?t=' + Date.now());
  await page.waitForFunction(() => document.getElementById('pyodideBoot')?.classList.contains('done'), null, { timeout: 180000 });
  const out = {};
  // Word -> PDF through the tool card with the file chooser
  await page.locator('.tool-card', { hasText: 'Word → PDF' }).click();
  await page.waitForSelector('.filelist-wrap');
  await page.screenshot({ path: 'C:/Users/ttaku/AppData/Local/Temp/w/out/ui_dlg1.png' });
  const [fc] = await Promise.all([page.waitForEvent('filechooser'), page.locator('.filelist-bar .btn', { hasText: 'ファイルを追加' }).click()]);
  await fc.setFiles('C:/Users/ttaku/Downloads/self_learning/金融教育.docx');
  await page.waitForTimeout(500);
  out.items = await page.locator('.fileitem .fi-name').allTextContents();
  await page.screenshot({ path: 'C:/Users/ttaku/AppData/Local/Temp/w/out/ui_dlg2.png' });
  await page.locator('.dialog .btn.primary').click();
  await page.waitForFunction(() => document.querySelectorAll('.page-wrap').length > 0, null, { timeout: 120000 });
  out.title = await page.locator('header, .title, #docTitle').first().textContent().catch(() => null);
  out.pages = await page.locator('.page-wrap').count();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: 'C:/Users/ttaku/AppData/Local/Temp/w/out/ui_opened.png' });
  out.errs = errs;
  return out;
}
