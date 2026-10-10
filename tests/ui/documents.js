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
  const out = {};
  const model = (fn) => page.evaluate(`(async () => { const m = await import('/js/model.js'); return (${fn})(m); })()`);
  const annots = () => model('(m) => m.store.annots.map((a) => ({ t: a.type, p: a.page, text: a.text || a.contents || undefined, c: a.style?.stroke }))');
  const tab = (name) => page.locator('#ribbonTabs .tab', { hasText: name }).click();
  const toastText = () => page.evaluate(() => document.getElementById('toast').textContent);
  const idle = async () => { await page.waitForTimeout(350); await page.waitForFunction(() => document.getElementById('busy').hidden, null, { timeout: 120000 }); await page.waitForTimeout(500); };
  const open = async (name) => {
    await page.setInputFiles('#fileInput', S + name);
    await page.waitForTimeout(700);
    for (const label of ['保存せずに進む', '破棄する']) { const b = page.locator('.dialog .btn', { hasText: label }); if (await b.count()) { await b.click(); await page.waitForTimeout(400); } }
    await idle();
    await page.waitForTimeout(1200);
    const b = page.locator('.dialog .btn', { hasText: '破棄する' }); if (await b.count()) await b.click();
  };
  const at = (x, y, pageIndex = 0) => page.evaluate(([px, py, pi]) => { const w = document.querySelectorAll('.page-wrap')[pi].getBoundingClientRect(); const s = Number(document.querySelector('.page-wrap').style.getPropertyValue('--scale-factor')); return { x: w.left + px * s, y: w.top + py * s }; }, [x, y, pageIndex]);
  const drag = async (a, b) => { await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2); await page.mouse.move(b.x, b.y, { steps: 4 }); await page.mouse.up(); await page.waitForTimeout(300); };
  const step = async (name, fn) => { try { await fn(); } catch (e) { out['FAILED ' + name] = String(e.message).slice(0, 260); for (const sel of ['.dialog .btn:not(.primary)', '.menu']) { if (await page.locator(sel).count()) await page.keyboard.press('Escape'); } } };

  await open('sample.pdf');

  await step('marker colours', async () => {
    await drag(await at(62, 152), await at(200, 154));
    await page.waitForTimeout(500);
    await page.locator('#selectionBar .dot').nth(1).click();
    await page.waitForTimeout(300);
    out.afterDot = await annots();
  });

  await step('text undo', async () => {
    await page.getByTitle('テキスト追加（クリックした場所に文字を書き込む）').first().click();
    const p = await at(300, 60);
    await page.mouse.click(p.x, p.y);
    await page.waitForTimeout(200);
    await page.keyboard.type('最初の文');
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    const q = await at(310, 60);
    await page.mouse.click(q.x, q.y);
    await page.waitForTimeout(250);
    await page.keyboard.press('End');
    await page.keyboard.type('に追記');
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    out.edited = (await annots()).filter((a) => a.t === 'freetext').map((a) => a.text);
    await page.keyboard.press('Control+z');
    out.undone = (await annots()).filter((a) => a.t === 'freetext').map((a) => a.text);
    await page.keyboard.press('Control+y');
    out.redone = (await annots()).filter((a) => a.t === 'freetext').map((a) => a.text);
  });

  await step('snippets', async () => {
    await page.getByTitle(/^定型文/).first().click();
    await page.waitForTimeout(200);
    await page.getByRole('menuitem', { name: '定型文を登録・編集…' }).click();
    await page.waitForTimeout(300);
    await page.locator('.dialog input.input').nth(0).fill('山田 太郎');
    await page.locator('.dialog input.input').nth(2).fill('東京都千代田区1-1-1');
    await page.locator('.dialog .btn.primary').click();
    await page.waitForTimeout(300);
    await page.getByTitle(/^定型文/).first().click();
    await page.waitForTimeout(200);
    out.snippetMenu = await page.locator('.menu button').allTextContents();
    await page.locator('.menu button').first().click();
    await page.waitForTimeout(700);
    out.afterSnippet = (await annots()).filter((a) => a.t === 'freetext').map((a) => a.text);
    await page.keyboard.press('Escape');
  });

  await step('off-screen pages catch up', async () => {
    // add something on page 3 through the model while page 1 is on screen, then go there
    await model(`(m) => { m.addAnnots([{ type: 'square', page: 2, rect: [100, 100, 200, 160], style: { stroke: '#e0403a', width: 2, opacity: 1 }, flags: {} }], { select: false }); return 1; }`);
    await page.waitForTimeout(300);
    out.drawnBeforeScroll = await page.evaluate(() => document.querySelectorAll('.page-wrap')[2].querySelectorAll('.annot').length);
    await page.fill('#pageInput', '3'); await page.press('#pageInput', 'Enter');
    await page.waitForTimeout(1200);
    out.drawnAfterScroll = await page.evaluate(() => document.querySelectorAll('.page-wrap')[2].querySelectorAll('.annot').length);
    await page.fill('#pageInput', '1'); await page.press('#pageInput', 'Enter');
    await page.waitForTimeout(800);
  });

  await step('slideshow', async () => {
    await tab('表示');
    await page.getByTitle(/^スライドショー/).first().click();
    await page.waitForTimeout(900);
    out.presenting = await page.evaluate(() => [document.body.classList.contains('present'), [...document.querySelectorAll('.page-wrap.showing')].map((w) => w.dataset.page)]);
    await page.screenshot({ path: S + 'n28.png' });
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(500);
    out.slide2 = await page.evaluate(() => [...document.querySelectorAll('.page-wrap.showing')].map((w) => w.dataset.page));
    await page.mouse.click(600, 400);
    await page.waitForTimeout(500);
    out.slide3 = await page.evaluate(() => [...document.querySelectorAll('.page-wrap.showing')].map((w) => w.dataset.page));
    await page.keyboard.press('Escape');
    await page.waitForTimeout(700);
    out.afterExit = await page.evaluate(() => [document.body.classList.contains('present'), document.getElementById('pageInput').value, document.querySelectorAll('.page-wrap.showing').length]);
    out.annotsUntouched = (await annots()).length;
  });

  await step('page ops', async () => {
    await tab('ページ');
    await page.getByTitle('ページを複製').first().click(); await idle();
    await page.getByTitle('今のページのあとに白紙を挿入').first().click(); await idle();
    out.pagesAfter = await model('(m) => m.store.pages.length');
    await page.setInputFiles('#mergeInput', [S + 'form.pdf', S + 'scan.pdf']); await idle();
    out.pagesAfterMerge = await model('(m) => m.store.pages.length');
    out.mergeToast = await toastText();
    await page.keyboard.press('Control+z'); await idle();
    out.pagesAfterUndo = await model('(m) => m.store.pages.length');
  });

  await step('redaction', async () => {
    await page.fill('#pageInput', '1'); await page.press('#pageInput', 'Enter'); await page.waitForTimeout(600);
    await tab('校閲');
    await page.getByTitle(/^検索した語句すべてに墨消し/).first().click();
    await page.waitForTimeout(300);
    await page.locator('.dialog input.input').first().fill('山田太郎');
    await page.locator('.dialog .btn.primary').click(); await idle();
    out.redactMarked = await toastText();
    await page.getByTitle(/^指定した墨消しを適用/).first().click();
    await page.waitForTimeout(300);
    await page.locator('.dialog .btn', { hasText: '適用して削除する' }).click(); await idle();
    out.redactApplied = await toastText();
    const response = await page.evaluate(async () => { const m = await import('/js/model.js'); const r = await fetch(`/api/doc/${m.store.docId}/page-text/0`); return (await r.json()).text.includes('山田太郎'); });
    out.nameStillInText = response;
  });

  await step('protected file', async () => {
    await page.locator('.tab.file').click();
    await page.getByRole('menuitem', { name: 'パスワードを付けて書き出す…' }).click();
    await page.waitForTimeout(300);
    await page.locator('.dialog input[type=password]').nth(0).fill('open123');
    await page.locator('.dialog input[type=password]').nth(1).fill('owner456');
    const [d] = await Promise.all([page.waitForEvent('download', { timeout: 60000 }), page.locator('.dialog .btn.primary').click()]);
    await d.saveAs(S + 'protected.pdf');
    await idle();
    await page.setInputFiles('#fileInput', S + 'protected.pdf');
    await page.waitForTimeout(800);
    const discard = page.locator('.dialog .btn', { hasText: '保存せずに進む' });
    if (await discard.count()) await discard.click();
    await page.waitForSelector('.dialog input[type=password]', { timeout: 30000 });
    out.passwordPrompt = await page.locator('.dialog h2').textContent();
    await page.locator('.dialog input[type=password]').fill('wrong');
    await page.locator('.dialog .btn.primary').click();
    await page.waitForTimeout(1500);
    out.wrongPassword = await page.locator('.dialog .warn-box').textContent().catch(() => null);
    await page.locator('.dialog input[type=password]').fill('open123');
    await page.locator('.dialog .btn.primary').click();
    await idle(); await page.waitForTimeout(1000);
    out.openedProtected = await page.evaluate(() => document.getElementById('docName').textContent);
  });

  out.errors = errs;
  return out;
}
