/* 自寫 RUM（不裝 Faro SDK：零依賴、離線可用；GCP 上可換成 Faro → Alloy faro.receiver，指標名不變）。
   PerformanceObserver 量 LCP／CLS／INP，攔 JS 錯誤；app.js 另外記瀏覽器端 API 往返、首次出雲時間、排版放不下的詞數、
   雲上篇數與明細抽屜不一致的次數。每 15 秒或離開頁面時用 sendBeacon 送 /api/rum（只有秒數與計數，沒有查詢字串、沒有個資）。 */
(function () {
  const q = [];
  const push = (metric, value, extra) => { q.push(Object.assign({ metric, value }, extra || {})); if (q.length > 2000) q.splice(0, q.length - 2000); };
  let lcp = null, cls = 0, inp = 0, lcpSent = false, firstCloud = false;
  const observe = (type, cb, extra) => { try { new PerformanceObserver(l => cb(l.getEntries())).observe(Object.assign({ type, buffered: true }, extra || {})); } catch (e) { /* 瀏覽器不支援就不量 */ } };
  observe('largest-contentful-paint', es => { if (es.length) lcp = es[es.length - 1].startTime; });
  observe('layout-shift', es => { for (const e of es) if (!e.hadRecentInput) cls += e.value; });
  observe('event', es => { for (const e of es) inp = Math.max(inp, e.duration); }, { durationThreshold: 40 });
  window.addEventListener('error', e => push('error', 1, { kind: 'error', msg: String(e && e.message || '').slice(0, 120) }));
  window.addEventListener('unhandledrejection', e => push('error', 1, { kind: 'unhandledrejection', msg: String(e && e.reason && (e.reason.message || e.reason) || '').slice(0, 120) }));
  function flush(final) {
    if (lcp != null && !lcpSent) { push('lcp', lcp / 1000); lcpSent = true; }
    if (final) { push('cls', cls); if (inp) push('inp', inp / 1000); }
    if (!q.length) return;
    const body = JSON.stringify({ page: location.pathname, events: q.splice(0, 500) });
    let sent = false;
    try { sent = !!(navigator.sendBeacon && navigator.sendBeacon('/api/rum', body)); } catch (e) { sent = false; }
    if (!sent) fetch('/api/rum', { method: 'POST', body, keepalive: true, headers: { 'Content-Type': 'application/json' } }).catch(() => {});
  }
  setInterval(() => flush(false), 15000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(true); });
  window.addEventListener('pagehide', () => flush(true));
  window.rum = {
    api: (path, ms, ok) => push('api', ms / 1000, { path: String(path).split('?')[0], ok: !!ok }),
    render: (seg, dropped) => push('render', 1, { seg, dropped }),
    firstCloud: () => { if (!firstCloud) { firstCloud = true; push('ttfc', performance.now() / 1000); } },
    mismatch: seg => push('mismatch', 1, { seg }),
    flush, _queue: q,
  };
})();
