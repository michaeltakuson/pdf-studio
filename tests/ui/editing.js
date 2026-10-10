async (page) => {
  const S = 'C:/path/to/scratch/';
  page.removeAllListeners('dialog');
  page.on('dialog', (d) => d.accept().catch(() => {}));
  page.removeAllListeners('pageerror'); page.removeAllListeners('console');
  const errs = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message + ' @ ' + (e.stack || '').split('\n').slice(1, 3).join(' | ')));
  page.on('console', (m) => { if (m.type() === 'error') errs.push('console: ' + m.text()); });
  await page.setViewportSize({ width: 1400, height: 850 });
  await page.goto('http://127.0.0.1:8765/?v=' + Date.now());
  await page.evaluate(() => { try { localStorage.removeItem('pdfstudio.defaults.v2'); } catch {} });
  await page.waitForFunction(() => document.getElementById('pyodideBoot').classList.contains('done'), null, { timeout: 180000 });
  await page.setInputFiles('#fileInput', S + 'sample.pdf');
  await page.waitForFunction(async () => (await import('/js/model.js')).store.docId, null, { timeout: 60000 });
  await page.waitForTimeout(2500);
  { const discard = page.locator('.dialog .btn', { hasText: '破棄する' }); if (await discard.count()) await discard.click(); }
  const out = {};
  const annots = () => page.evaluate(async () => (await import('/js/model.js')).store.annots.map((a) => ({ t: a.type, r: a.rect.map((v) => Math.round(v)), text: a.text || a.contents || undefined, aw: a.autoWidth, pts: a.points?.map((q) => q.map(Math.round)) })));
  const sel = () => page.evaluate(async () => (await import('/js/model.js')).store.selection.length);
  const tab = (name) => page.locator('#ribbonTabs .tab', { hasText: name }).click();
  const at = (x, y) => page.evaluate(([px, py]) => {
    const w = document.querySelector('.page-wrap').getBoundingClientRect();
    const s = Number(document.querySelector('.page-wrap').style.getPropertyValue('--scale-factor'));
    return { x: w.left + px * s, y: w.top + py * s };
  }, [x, y]);
  const drag = async (a, b) => { await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2); await page.mouse.move(b.x, b.y, { steps: 4 }); await page.mouse.up(); await page.waitForTimeout(250); };
  const step = async (name, fn) => { try { await fn(); } catch (e) { out['FAILED ' + name] = String(e.message).slice(0, 300); } };

  await step('text + keyboard', async () => {
    await page.getByTitle('テキスト追加（クリックした場所に文字を書き込む）').first().click();
    const p = await at(100, 250);
    await page.mouse.click(p.x, p.y);
    await page.waitForTimeout(200);
    await page.keyboard.type('コピーされる文字');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    out.selectedAfterEsc = await sel();
    out.first = await annots();
    await page.keyboard.press('Control+c');
    await page.keyboard.press('Control+v');
    await page.waitForTimeout(200);
    await page.keyboard.press('Control+d');
    await page.waitForTimeout(200);
    out.afterPaste = (await annots()).map((a) => a.r);
    for (let i = 0; i < 5; i += 1) await page.keyboard.press('ArrowRight');
    await page.keyboard.press('Shift+ArrowDown');
    await page.waitForTimeout(900);
    out.afterNudge = (await annots()).map((a) => a.r);
    await page.keyboard.press('Delete');
    out.afterDelete = (await annots()).length;
    await page.keyboard.press('Control+z');
    out.afterUndoDelete = (await annots()).length;
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    out.modeAfter = await page.evaluate(() => document.querySelector('#pages').dataset.mode);
  });

  await step('resize text box', async () => {
    const a = (await annots())[0];
    // press on the text and drag: moves it without opening the editor
    await drag(await at(a.r[0] + 10, a.r[1] + 9), await at(a.r[0] + 10 + 150, a.r[1] + 9));
    out.editorAfterDrag = await page.evaluate(() => !!document.querySelector('.ft-editor'));
    const b = (await annots())[0];
    out.moved = [a.r, b.r];
    out.selectedAfterDrag = await sel();
    // east handle: narrow the box so the text wraps
    await drag(await at(b.r[2], (b.r[1] + b.r[3]) / 2), await at(b.r[0] + 50, (b.r[1] + b.r[3]) / 2));
    const c = (await annots())[0];
    out.resized = { r: c.r, aw: c.aw };
    await page.screenshot({ path: S + 'n20.png' });
    // edit by clicking; drag by the frame while editing
    await page.keyboard.press('Escape');
    const mid = await at(c.r[0] + 12, c.r[1] + 9);
    await page.mouse.click(mid.x, mid.y);
    await page.waitForTimeout(250);
    out.editorOnClick = await page.evaluate(() => document.activeElement?.classList.contains('ft-editor'));
    const grip = await page.locator('.ft-grip').boundingBox();
    await drag({ x: grip.x + 3, y: grip.y + grip.height / 2 }, { x: grip.x + 3 + 60, y: grip.y + grip.height / 2 + 40 });
    const d = (await annots())[0];
    out.movedByFrame = [c.r, d.r];
    await page.keyboard.press('Escape');
  });

  await step('line endpoints', async () => {
    await tab('挿入');
    await page.getByTitle('直線').first().click();
    const a = await at(300, 240); const b = await at(420, 290);
    await drag(a, b);
    out.handles = await page.evaluate(() => [...document.querySelectorAll('.handle')].map((h) => h.dataset.handle));
    await drag(b, await at(480, 250));
    out.lineAfter = (await annots()).filter((x) => x.t === 'line').map((x) => x.pts);
    await page.keyboard.press('Escape');
  });

  await step('page context menu', async () => {
    const q = await at(450, 60);
    await page.mouse.click(q.x, q.y, { button: 'right' });
    await page.waitForTimeout(200);
    out.pageMenu = await page.locator('.menu button').allTextContents();
    await page.getByRole('menuitem', { name: 'ここにテキストを追加' }).click();
    await page.waitForTimeout(300);
    await page.keyboard.type('右クリックから追加');
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    out.afterContext = (await annots()).filter((x) => x.text === '右クリックから追加').map((x) => x.r);
  });

  await step('polygon + callout left', async () => {
    await page.getByTitle('多角形').first().click();
    for (const [x, y] of [[80, 60], [140, 50], [150, 100]]) { const p = await at(x, y); await page.mouse.click(p.x, p.y); }
    await page.keyboard.press('Enter');
    await page.waitForTimeout(200);
    out.polygon = (await annots()).filter((x) => x.t === 'polygon').length;
    await page.keyboard.press('Escape');
  });

  await tab('ホーム');
  await page.screenshot({ path: S + 'n21.png' });
  await page.setViewportSize({ width: 1024, height: 700 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: S + 'n19.png' });
  await page.setViewportSize({ width: 1400, height: 850 });
  out.errors = errs;
  return out;
}
