const { chromium } = require('playwright');
const OUT = process.argv[2], URL = process.argv[3];
(async () => {
  const b = await chromium.launch();
  const res = [];
  for (const [w, h, tag] of [[1440, 900, 'd'], [390, 844, 'm']]) for (const scheme of ['light', 'dark']) {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    const errs = [];
    p.on('pageerror', e => errs.push('pageerror ' + e.message));
    p.on('console', m => { if (m.type() === 'error') errs.push('console ' + m.text()); });
    p.on('requestfailed', r => errs.push('reqfail ' + r.url().slice(0, 120)));
    p.on('response', r => { if (r.status() >= 400) errs.push('http ' + r.status() + ' ' + r.url().slice(0, 120)); });
    await p.goto(URL + '#shape/int', { waitUntil: 'load' });
    await p.waitForTimeout(800);
    for (const tab of ['shape', 'q1', 'q2', 'q3', 'q4', 'q5flow', 'q6', 'evidence']) {
      await p.evaluate(t => { location.hash = '#' + t + (t === 'shape' ? '/int' : ''); }, tab);
      await p.waitForTimeout(tab === 'q2' ? 1500 : 600);
      const ov = await p.evaluate(t => {
        const pane = document.querySelector('#pane-' + t);
        const de = document.documentElement;
        const bad = [];
        pane.querySelectorAll('*').forEach(e => { const r = e.getBoundingClientRect(); if (r.width && r.right > innerWidth + 1 && !e.closest('.scroll,.dg,.fbar,nav,.variants,.qbar,.stages')) bad.push(e.tagName + '.' + e.className + ' ' + Math.round(r.right)); });
        return { doc: de.scrollWidth > innerWidth, pane: pane.scrollWidth > pane.clientWidth + 1, bad: bad.slice(0, 5) };
      }, tab);
      await p.screenshot({ path: `${OUT}/${tag}-${scheme}-${tab}.png` });
      res.push({ tag, scheme, tab, ...ov });
    }
    // interactions
    await p.evaluate(() => { location.hash = '#q5flow'; });
    await p.waitForTimeout(300);
    await p.click('#pane-q5flow [data-q5="threads"]');
    await p.waitForTimeout(500);
    await p.screenshot({ path: `${OUT}/${tag}-${scheme}-q5-threads.png`, fullPage: false });
    await p.evaluate(() => { location.hash = '#evidence'; });
    await p.click('#pane-evidence [data-f="instagram"]');
    const cnt = await p.textContent('#pane-evidence .cnt');
    res.push({ tag, scheme, evInstagram: cnt });
    await p.evaluate(() => { location.hash = '#shape/int/crawl'; });
    await p.waitForTimeout(600);
    await p.screenshot({ path: `${OUT}/${tag}-${scheme}-node-crawl.png` });
    await p.evaluate(() => { location.hash = '#shape/int'; });
    await p.waitForTimeout(300);
    await p.click('.qbar button[data-q="5"]').catch(() => {});
    await p.waitForTimeout(500);
    await p.screenshot({ path: `${OUT}/${tag}-${scheme}-q5panel.png` });
    res.push({ tag, scheme, errs: [...new Set(errs)].slice(0, 10) });
    await ctx.close();
  }
  console.log(JSON.stringify(res, null, 0).replace(/\},\{/g, '},\n{'));
  await b.close();
})();
