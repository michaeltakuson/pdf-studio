async (page) => {
  const S = 'C:/path/to/scratch/';
  page.removeAllListeners('dialog');
  page.on('dialog', (d) => d.accept().catch(() => {}));
  page.removeAllListeners('pageerror'); page.removeAllListeners('console'); page.removeAllListeners('requestfailed');
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message.slice(0, 200) + ' @ ' + (e.stack || '').split('\n').slice(1, 2).join('')));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text().slice(0, 200)); });
  await page.goto('http://127.0.0.1:8765/?t=' + Date.now());
  await page.waitForFunction(() => document.getElementById('pyodideBoot')?.classList.contains('done') || document.getElementById('pyodideBootStatus')?.classList.contains('error'), null, { timeout: 110000 });
  await page.setInputFiles('#fileInput', S + 'sample.pdf');
  await page.waitForFunction(async () => (await import('/js/model.js')).store.docId, null, { timeout: 30000 });
  await page.waitForTimeout(2000);
  { const b = page.locator('.dialog .btn', { hasText: '破棄する' }); if (await b.count()) await b.click(); }
  const out = {};
  const model = (fn) => page.evaluate(`(async () => { const m = await import('/js/model.js'); return (${fn})(m); })()`);
  const rects = () => model('(m) => m.store.annots.map((a) => a.rect.map(Math.round))');
  const tab = (name) => page.locator('#ribbonTabs .tab', { hasText: name }).click();
  const at = (x, y) => page.evaluate(([px, py]) => { const w = document.querySelector('.page-wrap').getBoundingClientRect(); const s = Number(document.querySelector('.page-wrap').style.getPropertyValue('--scale-factor')); return { x: w.left + px * s, y: w.top + py * s }; }, [x, y]);
  const step = async (name, fn) => { try { await fn(); } catch (e) { out['FAILED ' + name] = String(e.message).slice(0, 260); } };

  await step('align', async () => {
    await model(`(m) => { m.addAnnots([
      { type: 'square', page: 0, rect: [80, 60, 140, 90], style: { stroke: '#e0403a', width: 1.5, opacity: 1 }, flags: {} },
      { type: 'square', page: 0, rect: [130, 120, 200, 150], style: { stroke: '#e0403a', width: 1.5, opacity: 1 }, flags: {} },
      { type: 'square', page: 0, rect: [100, 240, 150, 270], style: { stroke: '#e0403a', width: 1.5, opacity: 1 }, flags: {} },
    ]); return 1; }`);
    await page.waitForTimeout(400);
    const p = await at(110, 75);
    await page.mouse.click(p.x, p.y, { button: 'right' });
    await page.waitForTimeout(250);
    out.menu = await page.locator('.menu button').allTextContents();
    await page.getByRole('menuitem', { name: '左をそろえる' }).click();
    await page.waitForTimeout(300);
    out.leftAligned = await rects();
    const q = await at(90, 75);
    await page.mouse.click(q.x, q.y, { button: 'right' });
    await page.waitForTimeout(250);
    await page.getByRole('menuitem', { name: '縦に等間隔' }).click();
    await page.waitForTimeout(300);
    out.spaced = await rects();
    await page.keyboard.press('Control+z'); await page.keyboard.press('Control+z');
    out.undone = await rects();
    await page.keyboard.press('Escape');
  });

  await step('touch drag', async () => {
    const cdp = await page.context().newCDPSession(page);
    const before = (await rects())[0];
    const a = await at(before[0] + 20, before[1] + 15);
    const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] });
    await touch('touchStart', a.x, a.y);
    for (let i = 1; i <= 6; i += 1) { await touch('touchMove', a.x + i * 15, a.y + i * 8); await page.waitForTimeout(30); }
    await touch('touchEnd');
    await page.waitForTimeout(400);
    out.touchMoved = [before, (await rects())[0]];
    out.scrolledByTouch = await page.evaluate(() => document.getElementById('stage').scrollTop);
  });

  await step('spread', async () => {
    await tab('表示');
    await page.getByTitle(/^見開き表示/).first().click();
    await page.waitForTimeout(900);
    out.spreadRows = await page.evaluate(() => [...document.querySelectorAll('.page-wrap')].map((w) => [Math.round(w.offsetLeft), Math.round(w.offsetTop)]));
    await page.screenshot({ path: S + 'n29.png' });
    await page.getByTitle(/^見開き表示/).first().click();
    await page.waitForTimeout(600);
    out.singleRows = await page.evaluate(() => [...document.querySelectorAll('.page-wrap')].map((w) => Math.round(w.offsetTop)));
  });
  out.errors = errs;
  return out;
}
