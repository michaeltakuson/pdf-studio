async (page) => {
  const S = 'C:/path/to/scratch/';
  page.removeAllListeners('dialog');
  page.on('dialog', (d) => d.accept().catch(() => {}));
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('requestfailed');
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200) + ' @ ' + (e.stack || '').split('\n').slice(1, 2).join('')));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  const out = {};
  const model = (fn) => page.evaluate(`(async () => { const m = await import('/js/model.js'); return (${fn})(m); })()`);
  const at = (x, y) => page.evaluate(([px, py]) => { const w = document.querySelector('.page-wrap').getBoundingClientRect(); const s = Number(document.querySelector('.page-wrap').style.getPropertyValue('--scale-factor')); return { x: w.left + px * s, y: w.top + py * s }; }, [x, y]);

  // ---- case 1: a plain file, edited while the engine is still loading
  let t0 = Date.now();
  await page.goto('http://127.0.0.1:8765/?t=' + Date.now());
  await page.waitForSelector('#btnOpen2');
  await page.setInputFiles('#fileInput', S + 'sample.pdf');
  await page.waitForSelector('.page-wrap canvas', { timeout: 30000 });
  await page.waitForFunction(async () => (await import('/js/model.js')).store.docId === 'preview', null, { timeout: 20000 });
  out.previewMs = Date.now() - t0;
  out.engineReadyAtEdit = await page.evaluate(() => !!window.pdfStudioReady);
  await page.getByTitle('テキスト追加（クリックした場所に文字を書き込む）').first().click();
  let p = await at(300, 60);
  await page.mouse.click(p.x, p.y);
  await page.waitForTimeout(250);
  await page.keyboard.type('準備中に書いた文字');
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  const a = await at(62, 152); const b = await at(200, 154);
  await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move(b.x, b.y, { steps: 6 }); await page.mouse.up();
  await page.waitForTimeout(600);
  await page.locator('#selectionBar button').first().click();
  await page.waitForTimeout(300);
  out.duringPreview = await model('(m) => [m.store.docId, m.store.annots.map((x) => x.type)]');
  out.editedMs = Date.now() - t0;
  out.saveState = await page.evaluate(() => document.getElementById('saveState').textContent);
  // ask for the file while still in preview: it must simply arrive once the engine is up
  const download = page.waitForEvent('download', { timeout: 150000 });
  await page.locator('.tab.file').click();
  await page.getByRole('menuitem', { name: 'コピーをダウンロード' }).click();
  const d = await download;
  await d.saveAs(S + 'preview_out.pdf');
  out.downloadedMs = Date.now() - t0;
  out.afterAdopt = await model('(m) => [m.store.docId !== "preview", m.store.annots.map((x) => x.type), m.history.canUndo]');
  await page.keyboard.press('Control+z');
  out.undoStillWorks = await model('(m) => m.store.annots.map((x) => x.type)');
  await page.keyboard.press('Control+y');

  // ---- case 2: a file that already has markup
  t0 = Date.now();
  await page.goto('http://127.0.0.1:8765/?t=' + Date.now());
  await page.waitForSelector('#btnOpen2');
  await page.setInputFiles('#fileInput', S + 'out2.pdf');
  await page.waitForFunction(async () => (await import('/js/model.js')).store.docId === 'preview', null, { timeout: 30000 });
  await page.getByTitle('テキスト追加（クリックした場所に文字を書き込む）').first().click();
  p = await at(300, 500);
  await page.mouse.click(p.x, p.y);
  await page.waitForTimeout(250);
  await page.keyboard.type('既存の書き込みに追加');
  await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
  await page.waitForFunction(async () => { const m = await import('/js/model.js'); return m.store.docId && m.store.docId !== 'preview'; }, null, { timeout: 150000 });
  await page.waitForTimeout(2500);
  out.case2 = await model('(m) => m.store.annots.map((x) => x.type + (x.text ? ":" + x.text.slice(0, 6) : ""))');
  await page.screenshot({ path: S + 'n30.png' });
  out.errors = errs;
  return out;
}
