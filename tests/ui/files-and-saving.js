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
  const toastText = () => page.evaluate(() => document.getElementById('toast').textContent);
  const idle = async () => { await page.waitForTimeout(350); await page.waitForFunction(() => document.getElementById('busy').hidden, null, { timeout: 120000 }); await page.waitForTimeout(500); };
  const step = async (name, fn) => { try { await fn(); } catch (e) { out['FAILED ' + name] = String(e.message).slice(0, 260); } };
  // Real File objects for synthetic drop / paste events, via a scratch input.
  await page.evaluate(() => { for (const id of ['__pdf', '__png']) { const i = document.createElement('input'); i.type = 'file'; i.id = id; i.hidden = true; document.body.append(i); } });
  await page.setInputFiles('#__pdf', S + 'sample.pdf');
  await page.setInputFiles('#__png', S + 'probe3_None_False.png');

  await step('drop a PDF on the toolbar', async () => {
    await page.evaluate(async () => {
      const dt = new DataTransfer();
      dt.items.add(new File([await document.getElementById('__pdf').files[0].arrayBuffer()], 'dropped.pdf', { type: 'application/pdf' }));
      const target = document.getElementById('ribbon');
      for (const type of ['dragenter', 'dragover', 'drop']) target.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
    });
    await page.waitForFunction(async () => (await import('/js/model.js')).store.docId, null, { timeout: 30000 });
    await idle(); await page.waitForTimeout(1200);
    { const b = page.locator('.dialog .btn', { hasText: '破棄する' }); if (await b.count()) await b.click(); }
    out.dropped = await page.evaluate(() => document.getElementById('docName').textContent);
  });

  await step('escape drops the tool', async () => {
    await page.getByTitle('テキスト追加（クリックした場所に文字を書き込む）').first().click();
    await page.mouse.click(700, 300);
    await page.waitForTimeout(250);
    await page.keyboard.type('Escのテスト');
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(200);
    out.modeAfterTwoEsc = await page.evaluate(() => document.querySelector('#pages').dataset.mode);
  });

  await step('paste an image', async () => {
    await page.evaluate(async () => {
      const dt = new DataTransfer();
      dt.items.add(new File([await document.getElementById('__png').files[0].arrayBuffer()], 'shot.png', { type: 'image/png' }));
      document.body.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
    });
    await page.waitForTimeout(1500);
    out.afterPaste = await model('(m) => m.store.annots.map((a) => a.type)');
  });

  await step('save to a file handle', async () => {
    // A real file handle without a picker: the browser's private file system.
    await page.evaluate(async () => {
      const root = await navigator.storage.getDirectory();
      const handle = await root.getFileHandle('saved-by-test.pdf', { create: true });
      const ctx = await import('/js/ctx.js');
      ctx.state.fileHandle = handle;
    });
    await page.keyboard.press('Control+s');
    await idle(); await page.waitForTimeout(800);
    out.saveToast = await toastText();
    out.titleAfterSave = await page.evaluate(() => [document.title, document.getElementById('saveState').textContent]);
    out.savedFile = await page.evaluate(async () => {
      const root = await navigator.storage.getDirectory();
      const file = await (await root.getFileHandle('saved-by-test.pdf')).getFile();
      const head = new TextDecoder().decode(new Uint8Array(await file.slice(0, 5).arrayBuffer()));
      return [file.size, head];
    });
  });

  await step('autosave', async () => {
    await page.locator('.tab.file').click();
    await page.getByRole('menuitem', { name: /自動保存: オフ/ }).click();
    await page.waitForTimeout(300);
    out.autosaveToast = await toastText();
    await model(`(m) => { m.addAnnots([{ type: 'square', page: 0, rect: [300, 400, 380, 450], style: { stroke: '#2f6df6', width: 2, opacity: 1 }, flags: {} }], { select: false }); return 1; }`);
    await page.waitForTimeout(300);
    out.stateBefore = await page.evaluate(() => document.getElementById('saveState').textContent);
    await page.waitForFunction(() => document.getElementById('saveState').textContent.startsWith('保存済み'), null, { timeout: 60000 });
    out.stateAfter = await page.evaluate(() => document.getElementById('saveState').textContent);
    const size = await page.evaluate(async () => { const root = await navigator.storage.getDirectory(); return (await (await root.getFileHandle('saved-by-test.pdf')).getFile()).size; });
    out.autosavedBytes = size;
    await page.locator('.tab.file').click();
    await page.getByRole('menuitem', { name: /自動保存: オン/ }).click();
  });

  await step('recent files', async () => {
    out.recent = await page.evaluate(async () => { const r = await import('/js/recent.js'); return (await r.recentFiles()).map((x) => x.name); });
  });

  await step('print', async () => {
    await page.keyboard.press('Control+p');
    await idle(); await page.waitForTimeout(1500);
    out.printFrame = await page.evaluate(() => { const f = [...document.querySelectorAll('iframe')].pop(); return f ? f.src.slice(0, 5) : null; });
  });

  await step('annotation export', async () => {
    for (const label of ['Excel用のCSVにする', 'XFDFにする（書き込みだけを渡す）', 'Markdownにする（ノートアプリ用）']) {
      await page.locator('#ribbonTabs .tab', { hasText: '校閲' }).click();
      const [d] = await Promise.all([
        page.waitForEvent('download', { timeout: 60000 }),
        (async () => { await page.getByTitle('書き込みの一覧を書き出す・取り込む').first().click(); await page.getByRole('menuitem', { name: label }).click(); })(),
      ]);
      out['export ' + label.slice(0, 5)] = d.suggestedFilename();
      if (label.startsWith('XFDF')) await d.saveAs(S + 'out.xfdf');
      await idle();
    }
    const before = await model('(m) => m.store.annots.length');
    await page.setInputFiles('#xfdfInput', S + 'out.xfdf');
    await page.waitForTimeout(1500);
    out.xfdfImport = [before, await model('(m) => m.store.annots.length'), await toastText()];
  });

  out.errors = errs;
  return out;
}
