async (page) => {
  const S = 'C:/path/to/scratch/';
  page.removeAllListeners('dialog');
  page.on('dialog', (d) => d.accept().catch(() => {}));
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('requestfailed');
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200) + ' @ ' + (e.stack || '').split('\n').slice(1, 2).join('')));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  await page.goto('http://127.0.0.1:8765/?t=' + Date.now());
  await page.waitForFunction(() => document.getElementById('pyodideBoot')?.classList.contains('done') || document.getElementById('pyodideBootStatus')?.classList.contains('error'), null, { timeout: 110000 });
  await page.setInputFiles('#fileInput', S + 'sample.pdf');
  await page.waitForFunction(async () => (await import('/js/model.js')).store.docId, null, { timeout: 30000 });
  await page.waitForTimeout(2200);
  const discard = page.locator('.dialog .btn', { hasText: '破棄する' });
  if (await discard.count()) await discard.click();
  const out = {};
  const annots = () => page.evaluate(async () => (await import('/js/model.js')).store.annots.map((a) => ({ t: a.type, tool: a.tool, r: a.rect.map((v) => Math.round(v)), fill: a.style?.fill })));
  const pages = () => page.evaluate(async () => (await import('/js/model.js')).store.pages.map((p) => [Math.round(p.width), Math.round(p.height)]));
  const tab = (name) => page.locator('#ribbonTabs .tab', { hasText: name }).click();
  const toastText = () => page.evaluate(() => document.getElementById('toast').textContent);
  const idle = () => page.waitForFunction(() => document.getElementById('busy').hidden, null, { timeout: 120000 });
  const at = (x, y) => page.evaluate(([px, py]) => { const w = document.querySelector('.page-wrap').getBoundingClientRect(); const s = Number(document.querySelector('.page-wrap').style.getPropertyValue('--scale-factor')); return { x: w.left + px * s, y: w.top + py * s }; }, [x, y]);
  const drag = async (a, b) => { await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2); await page.mouse.move(b.x, b.y, { steps: 4 }); await page.mouse.up(); await page.waitForTimeout(300); };
  const step = async (name, fn) => { try { await fn(); } catch (e) { out['FAILED ' + name] = String(e.message).slice(0, 300); } };

  await step('whiteout', async () => {
    await page.getByTitle(/^修正テープ/).first().click();
    await drag(await at(60, 108), await at(250, 128));
    out.whiteout = await annots();
    await page.keyboard.press('Escape');
  });

  await step('study', async () => {
    // mark a phrase, then cover it
    await drag(await at(62, 152), await at(200, 154));
    await page.waitForTimeout(500);
    await page.locator('#selectionBar button').first().click();
    await page.waitForTimeout(300);
    await page.getByTitle(/^暗記シート/).first().click();
    await page.waitForTimeout(400);
    out.studyToast = await toastText();
    out.covered = await page.locator('.annot.covered').count();
    await page.screenshot({ path: S + 'n26.png' });
    const p = await at(100, 158);
    await page.mouse.click(p.x, p.y);
    await page.waitForTimeout(300);
    out.coveredAfterClick = await page.locator('.annot.covered').count();
    await page.getByTitle(/^暗記シート/).first().click();
  });

  await step('snapshot', async () => {
    await page.getByTitle(/^切り抜きコピー/).first().click();
    await drag(await at(50, 60), await at(400, 140));
    await idle(); await page.waitForTimeout(600);
    out.snapshotToast = await toastText();
    out.clipboardTypes = await page.evaluate(async () => { try { const items = await navigator.clipboard.read(); return items.flatMap((i) => i.types); } catch (e) { return String(e); } });
  });

  await step('selection bar', async () => {
    await drag(await at(62, 195), await at(160, 197));
    await page.waitForTimeout(500);
    out.barButtons = await page.locator('#selectionBar button').evaluateAll((list) => list.map((b) => b.title.split('（')[0]));
    await page.keyboard.press('Escape');
  });

  await step('outline', async () => {
    await page.locator('#leftPanel .side-tab', { hasText: 'しおり' }).click();
    await page.getByText('見出しから自動で作る').click();
    await page.waitForTimeout(400);
    const ok = page.locator('.dialog .btn.primary');
    if (await ok.count()) await ok.click();
    await page.waitForTimeout(1200);
    out.outline = await page.locator('.outline-item').count();
    out.outlineToast = await toastText();
    await page.locator('#leftPanel .side-tab', { hasText: 'ページ' }).click();
  });

  await step('crop', async () => {
    await tab('ページ');
    out.before = await pages();
    await page.getByTitle(/^トリミング（/).first().click();
    await drag(await at(40, 40), await at(560, 300));
    await page.waitForTimeout(300);
    await page.locator('.dialog .btn.primary').click();
    await page.waitForTimeout(400); await idle(); await page.waitForTimeout(800);
    out.cropped = await pages();
    out.annotsAfterCrop = await annots();
    await page.screenshot({ path: S + 'n27.png' });
    await page.getByTitle(/^トリミングを解除/).first().click();
    await page.waitForTimeout(400); await idle(); await page.waitForTimeout(800);
    out.uncropped = await pages();
  });

  const [d] = await Promise.all([
    page.waitForEvent('download', { timeout: 60000 }),
    (async () => { await page.locator('.tab.file').click(); await page.getByRole('menuitem', { name: 'コピーをダウンロード' }).click(); })(),
  ]);
  await d.saveAs(S + 'out3.pdf');
  out.errors = errs;
  return out;
}
