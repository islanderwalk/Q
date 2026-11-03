// Q6 整套系統截圖：PM A/B 文字雲、Kibana、Grafana、Prometheus（全部是本機容器，畫面上的數字來自官方 ES／PG）
// 用法：node screenshots.js <輸出目錄> <grafana 起點 epoch ms> <grafana 終點 epoch ms>
const { chromium } = require('playwright');
const path = require('path');
const out = process.argv[2], gFrom = process.argv[3], gTo = process.argv[4];
const shots = [];
async function shot(page, url, file, { wait = 'networkidle', settle = 1500, full = false, before } = {}) {
  await page.goto(url, { waitUntil: wait, timeout: 90000 });
  if (before) await before(page);
  await page.waitForTimeout(settle);
  await page.screenshot({ path: path.join(out, file), fullPage: full });
  shots.push({ file, url, at: new Date().toISOString() });
  console.log('shot', file);
}
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: 'zh-TW', timezoneId: 'Asia/Taipei' });
  const page = await ctx.newPage();
  const only = (process.env.ONLY || '').split(',').filter(Boolean);
  const want = k => !only.length || only.includes(k);
  const pm = 'http://localhost:8080/';
  const waitCloud = async p => { await p.waitForSelector('#cloud-v2 span, #cloud-v2 text, #cloud-v2 *', { timeout: 60000 }); };
  if (want('pm')) {
  await shot(page, pm, 'pm/01_all_channels_all_time.png', { before: waitCloud, full: true });
  await shot(page, pm + '?from=2026-10-01', 'pm/02_ptt_2026-10_pm_spot_check.png', { before: waitCloud, full: true });
  await shot(page, pm + '?q=' + encodeURIComponent('高雄'), 'pm/03_keyword_kaohsiung.png', { before: waitCloud, full: true });
  for (const ch of ['facebook', 'instagram', 'youtube', 'ptt', 'media', 'threads'])
    await shot(page, pm + '?channels=' + ch + (ch === 'ptt' ? '&from=2026-10-01' : ''), `pm/10_channel_${ch}.png`, { before: waitCloud, full: true });
  await shot(page, pm + '?from=2026-10-01&term=' + encodeURIComponent('颱風') + '&seg=v2', 'pm/20_posts_drawer_typhoon.png', { before: waitCloud, settle: 2500, full: true });
  await shot(page, pm + '?term=' + encodeURIComponent('肥宅') + '&seg=v2', 'pm/21_posts_drawer_new_word.png', { before: waitCloud, settle: 2500, full: true });
  // 告警橫幅：Alertmanager → /api/ops/alert → live_events → /api/ops/alerts → #alerts（有告警在響時才會出現）
  await shot(page, pm, 'pm/30_alerts_strip.png', { before: async p => { await waitCloud(p); await p.waitForSelector('#alerts:not([hidden])', { timeout: 30000 }).catch(() => {}); }, settle: 2000, full: false });

  }
  // Kibana：Discover 看同一篇的 v1／v2 詞表與版本欄位
  if (want('kibana')) {
  // Kibana 沒有繁體介面，用英文；關掉「叢集沒開安全功能」提示（本機零成本環境刻意不開 TLS／帳密，GCP 上要開）
  const kctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'en-US', timezoneId: 'Asia/Taipei' });
  await kctx.addInitScript(() => { try { localStorage.setItem('insecureClusterWarningVisibility', JSON.stringify({ show: false })); } catch (e) {} });
  const kpage = await kctx.newPage();
  const kib = 'http://localhost:5601';
  const cols = "!(channel,content,terms_v1,terms_v2,dict_version_v2,seg_ver_v2,seg_build_v2)";
  await shot(kpage, `${kib}/app/discover#/?_g=(time:(from:'2026-10-01T00:00:00.000Z',to:now))&_a=(columns:${cols},dataSource:(dataViewId:posts,type:dataView),query:(language:kuery,query:'terms_v2:肥宅'))`,
    'kibana/01_discover_v1_v2_terms.png', { settle: 6000 });
  await shot(kpage, `${kib}/app/discover#/?_g=(time:(from:'2026-10-25T00:00:00.000Z',to:now))&_a=(columns:${cols},dataSource:(dataViewId:posts,type:dataView),query:(language:kuery,query:'not channel:ptt'))`,
    'kibana/02_discover_six_channels.png', { settle: 6000 });
  await shot(kpage, `${kib}/app/management/data/index_management/indices`, 'kibana/03_index_management.png', { settle: 5000 });

  }
  // Grafana：兩個看板（主看板 9 層、採集看板 6 層；monitoring/grafana/build_dashboard.py 產生）。
  // 面板是捲到才畫，所以用一個很高的視窗一次全部畫出來；畫面上的數字來自 Prometheus／PostgreSQL／Loki
  if (want('grafana')) {
  const tall = await ctx.newPage();
  await tall.setViewportSize({ width: 1600, height: 7200 });
  await shot(tall, `http://localhost:3000/d/qsearch-q6/?orgId=1&from=${gFrom}&to=${gTo}&kiosk`, 'grafana/01_dashboard_main.png', { wait: 'load', settle: 15000 });
  await tall.setViewportSize({ width: 1600, height: 3200 });
  await shot(tall, `http://localhost:3000/d/qsearch-q6-collect/?orgId=1&from=${gFrom}&to=${gTo}&kiosk`, 'grafana/02_dashboard_collect.png', { wait: 'load', settle: 15000 });
  await tall.setViewportSize({ width: 1440, height: 900 });
  await shot(tall, 'http://localhost:3000/alerting/list?view=state&search=', 'grafana/03_alert_rules_from_prometheus.png', { wait: 'load', settle: 6000 });
  await tall.close();
  }
  if (want('alertmanager')) {
  await shot(page, 'http://localhost:9093/#/alerts', 'alertmanager/01_alerts.png', { wait: 'load', settle: 3000 });
  }
  if (want('prometheus')) {
  // Prometheus：告警規則與串流延遲
  await shot(page, 'http://localhost:9090/alerts', 'prometheus/01_alerts.png', { wait: 'load', settle: 3000 });
  await shot(page, 'http://localhost:9090/query?g0.expr=' + encodeURIComponent('histogram_quantile(0.95, sum by (le, pipeline) (rate(qseg_e2e_lag_seconds_bucket[5m])))') + '&g0.tab=graph&g0.range_input=1h',
    'prometheus/02_stream_lag_p95.png', { wait: 'load', settle: 5000 });
  await shot(page, 'http://localhost:9090/query?g0.expr=' + encodeURIComponent('rate(elasticsearch_node_operations_completed_total{operation="index"}[1m])') + '&g0.tab=graph&g0.range_input=1h',
    'prometheus/03_es_index_rate.png', { wait: 'load', settle: 5000 });
  }
  const f = path.join(out, 'screenshots.json');
  const prev = require('fs').existsSync(f) ? JSON.parse(require('fs').readFileSync(f)) : [];
  const keep = Array.isArray(prev) ? prev.filter(x => !shots.some(s => s.file === x.file)) : [];
  require('fs').writeFileSync(f, JSON.stringify([...keep, ...shots], null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
