async (page) => {
  // Folding the ribbon away (Ctrl+F1, double-click on a tab, the button at the
  // right of the tabs), using it while folded, and the list of search results.
  const S = 'C:/path/to/scratch/';
  const ROOT = 'C:/path/to/pdf-studio/webapp/';
  const B = 'http://127.0.0.1:8765/';
  await page.unrouteAll().catch(() => {});
  await page.route(B + '**', (route) => {
    const rel = decodeURIComponent(new URL(route.request().url()).pathname).slice(1) || 'index.html';
    return route.fulfill({ path: ROOT + rel }).catch(() => route.fulfill({ status: 404, body: '' }));
  });
  page.removeAllListeners('dialog'); page.on('dialog', (d) => d.accept().catch(() => {}));
  page.removeAllListeners('pageerror'); page.removeAllListeners('console');
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200) + (e.stack || '').split('\n').slice(1, 2).join('')));
  page.on('console', (m) => { if (m.type() === 'error' && !/fetching the script/.test(m.text())) errs.push('console: ' + m.text().slice(0, 200)); });
  const out = {};
  await page.goto(B + '?t=' + Date.now());
  await page.evaluate(async () => { for (const r of await navigator.serviceWorker.getRegistrations()) await r.unregister(); localStorage.removeItem('pdfstudio.ribbon'); for (const d of await indexedDB.databases()) indexedDB.deleteDatabase(d.name); });
  await page.goto(B + '?t=' + Date.now());
  await page.waitForSelector('#btnOpen2');
  await page.setInputFiles('#fileInput', S + 'sample.pdf');
  await page.waitForSelector('.page-wrap canvas', { timeout: 30000 });
  await page.waitForFunction(() => !!window.pdfStudioReady, null, { timeout: 240000 });
  await page.waitForTimeout(3000);
  const geo = () => page.evaluate(() => { const r = document.querySelector('#ribbon'); const st = document.querySelector('.stage').getBoundingClientRect(); return [r.className, getComputedStyle(r).display, getComputedStyle(r).position, Math.round(st.top), document.querySelector('.page-wrap')?.style.getPropertyValue('--scale-factor').slice(0, 5), document.querySelector('.tab-fold').textContent].join(' | '); });
  out.open = await geo();
  await page.keyboard.press('Control+F1'); await page.waitForTimeout(400);
  out.folded = await geo();
  await page.locator('.tab[data-tab="insert"]').click(); await page.waitForTimeout(300);
  out.peek = await geo();
  await page.screenshot({ path: S + 'n32.png' });
  await page.locator('.tab[data-tab="home"]').click(); await page.waitForTimeout(200);
  out.peekHome = await geo();
  await page.getByTitle('テキスト追加（クリックした場所に文字を書き込む）').first().click(); await page.waitForTimeout(300);
  out.afterCommand = await geo();
  out.mode = await page.evaluate(() => document.querySelector('.pages').dataset.mode);
  const w = await page.evaluate(() => { const r = document.querySelector('.page-wrap').getBoundingClientRect(); return { x: r.left + 200, y: r.top + 120 }; });
  await page.mouse.click(w.x, w.y); await page.waitForTimeout(300);
  await page.keyboard.type('たたんだまま書く');
  await page.keyboard.press('Control+F1'); await page.waitForTimeout(200);   // works while typing too
  out.whileTyping = await geo();
  await page.keyboard.press('Control+F1'); await page.waitForTimeout(200);
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  out.annots = await page.evaluate(async () => (await import('/js/model.js')).store.annots.map((a) => a.text));
  await page.locator('.tab[data-tab="view"]').click(); await page.waitForTimeout(200);
  out.peek2 = await geo();
  await page.mouse.click(w.x + 200, w.y + 300); await page.waitForTimeout(200);
  out.closedByOutside = await geo();
  await page.locator('.tab[data-tab="home"]').click(); await page.waitForTimeout(200);
  await page.locator('#ribbon select').first().selectOption({ index: 1 }); await page.waitForTimeout(200);
  out.afterSelect = await geo();
  await page.keyboard.press('Escape'); await page.waitForTimeout(150);
  out.afterEsc = await geo();
  // the same tab again closes it
  await page.locator('.tab[data-tab="home"]').click(); await page.waitForTimeout(150);
  await page.locator('.tab[data-tab="home"]').click(); await page.waitForTimeout(450);
  out.sameTabTwice = await geo();
  await page.goto(B + '?t=' + Date.now());
  await page.waitForSelector('#btnOpen2');
  out.afterReload = await geo();
  await page.locator('.tab[data-tab="pages"]').dblclick(); await page.waitForTimeout(300);
  out.afterDbl = await geo();
  out.stored = await page.evaluate(() => localStorage.getItem('pdfstudio.ribbon'));
  await page.setInputFiles('#fileInput', S + 'sample.pdf');
  await page.waitForFunction(() => /ページ ／/.test(document.querySelector('footer').textContent) && !/準備中/.test(document.getElementById('saveState').textContent), null, { timeout: 240000 });
  await page.waitForTimeout(1500);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+f');
  await page.keyboard.type('redaction'); await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#searchCount').textContent !== '', null, { timeout: 60000 });
  await page.locator('#searchCount').click({ timeout: 5000 }); await page.waitForTimeout(300);
  out.rows = await page.locator('.hit-row').evaluateAll((rows) => rows.slice(0, 2).map((r) => r.querySelector('.ctx').innerHTML));
  await page.keyboard.press('Escape');
  out.errors = errs;
  return out;
}
