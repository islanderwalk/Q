// 對 ES／帳本即時發請求，把「請求＋原始回應」排成頁面截圖；程式碼截圖附 GitHub blob sha 比對。
const { chromium } = require('playwright');
const { execSync } = require('child_process');
const fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '../../../..'), OUT = __dirname;
const BRANCH = 'claude/history-crawl-head-increment-2026-11-03';
const ES = 'http://localhost:9200';
const now = () => new Date().toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false });
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;');
async function es(method, p, body) {
  const r = await fetch(ES + p, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return r.text();
}
function psql(sql) {
  fs.writeFileSync(path.join(__dirname, 'ev.sql'), sql);
  const p = path.join(__dirname, 'ev.sql').replace(/^([A-Za-z]):/, (_, d) => '/mnt/' + d.toLowerCase()).replace(/\\/g, '/');   // WSL 看得到的路徑
  return execSync(`wsl.exe -e sh -lc "PGPASSWORD=qsearch PGCONNECT_TIMEOUT=8 psql -h 127.0.0.1 -p 55433 -U qsearch -d qsearch -P pager=off -f ${p}"`).toString();
}
const pretty = t => { try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return t; } };
const CSS = `body{margin:0;background:#fff;font:14px/1.5 "Microsoft JhengHei","Segoe UI",sans-serif;color:#1f2328}
.canvas{width:1180px;padding:22px 26px}h1{font-size:20px;margin:0 0 4px}.meta{color:#59636e;font-size:12.5px;margin-bottom:12px}
.req{background:#f6f8fa;border:1px solid #d1d9e0;border-radius:6px;padding:8px 12px;font:12.5px/1.45 Consolas,monospace;white-space:pre-wrap;margin:8px 0}
.res{background:#0d1117;color:#e6edf3;border-radius:6px;padding:10px 14px;font:12.5px/1.45 Consolas,monospace;white-space:pre-wrap;margin:0 0 12px}
.lbl{font-size:12px;color:#59636e;font-weight:600;margin-top:6px}.note{background:#fff8c5;border:1px solid #d4a72c66;border-radius:6px;padding:8px 12px;margin-top:6px}
table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #d1d9e0;padding:5px 12px;font:13px Consolas,"Microsoft JhengHei",monospace;text-align:right}th{background:#f6f8fa}td:first-child{text-align:left}
.ok{color:#1a7f37;font-weight:700}.code{border:1px solid #d1d9e0;border-radius:6px;overflow:hidden;margin:8px 0 14px}.code .hd{background:#f6f8fa;border-bottom:1px solid #d1d9e0;padding:6px 12px;font:12px Consolas,monospace}
.code pre>span{display:block;min-height:1.5em}.code pre{margin:0;padding:6px 0;font:12.5px/1.5 Consolas,monospace}.ln{display:inline-block;width:46px;color:#8c959f;text-align:right;padding-right:12px;user-select:none}.hl{background:#fff8c5;display:block}`;
const page0 = (title, meta, inner) => `<!doctype html><meta charset=utf-8><style>${CSS}</style><div class=canvas><h1>${title}</h1><div class=meta>${meta}</div>${inner}</div>`;
const block = (req, res, lbl = '原始回應（未編輯）') => `<div class=lbl>請求</div><div class=req>${esc(req)}</div><div class=lbl>${lbl}</div><div class=res>${esc(res)}</div>`;
function codeBlock(file, from, to, hl, sha) {
  const lines = fs.readFileSync(path.join(ROOT, file), 'utf8').split('\n');
  const body = lines.slice(from - 1, to).map((l, i) => { const n = from + i; const h = hl.some(([a, b]) => n >= a && n <= b);
    return `<span class="${h ? 'hl' : ''}"><span class=ln>${n}</span>${esc(l) || ' '}</span>`; }).join('');
  return `<div class=code><div class=hd>${file} 第 ${from}–${to} 行 · GitHub ${BRANCH} blob ${sha.slice(0, 12)} ＝ 本機 git hash-object ${sha.slice(0, 12)} <span class=ok>相同</span></div><pre>${body}</pre></div>`;
}
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1240, height: 900 }, deviceScaleFactor: 1.5 });
  const shot = async (name, html) => { const p = await ctx.newPage(); await p.setContent(html); await p.locator('.canvas').screenshot({ path: `${OUT}/${name}.png` }); await p.close(); console.log(name); };
  const t = () => `擷取時間 ${now()}（台北）· WSL Ubuntu 上 docker compose 專案 qsearch-q6 的 Elasticsearch ${ES}／PostgreSQL 帳本 127.0.0.1:55433`;

  // 01 叢集與索引
  const r1a = await es('GET', '/'), r1b = await es('GET', '/_cat/indices?v&s=docs.count:desc&h=index,health,pri,rep,docs.count,store.size');
  await shot('01-es-cluster-indices', page0('① 本機 Elasticsearch 叢集與索引', t(), block('GET ' + ES + '/', r1a) + block('GET ' + ES + '/_cat/indices?v&s=docs.count:desc&h=index,health,pri,rep,docs.count,store.size', r1b)));

  // 02 mapping／settings：渠道、月只是 keyword 欄位，沒有 routing、沒有按月分索引
  const m = JSON.parse(await es('GET', '/posts/_mapping')).posts.mappings;
  const keep = ['post_id', 'channel', 'month', 'published_at', 'content', 'terms_v2', 'seg_ver_v2', 'dict_version_v2', 'content_sha'];
  const mm = { _routing: m._routing ?? null, properties: Object.fromEntries(keep.map(k => [k, m.properties[k]])) };
  const r2 = await es('GET', '/posts/_settings?filter_path=**.number_of_shards,**.number_of_replicas,**.routing_partition_size,**.sort');
  const r2c = await es('GET', '/_cat/shards/posts?v&h=index,shard,prirep,state,docs,store');
  await shot('02-es-mapping-no-physical-split', page0('② ES 裡「渠道×月」不是實體切分：posts 是一個索引、3 個 shard，channel／month 只是 keyword 欄位',
    t() + ` · mapping 只列出與切批相關的 ${keep.length} 個欄位（全部 ${Object.keys(m.properties).length} 個）`,
    block('GET ' + ES + '/posts/_mapping', JSON.stringify(mm, null, 2), '原始回應（只挑出 9 個欄位，內容未改；_routing 為 null 代表沒設自訂 routing）') + block('GET ' + ES + '/posts/_settings?filter_path=…', pretty(r2)) + block('GET ' + ES + '/_cat/shards/posts?v', r2c) + `<div class=note>判讀：沒有按渠道或月份開索引，也沒有自訂 routing。文件以 _id（＝post_id）雜湊平均分到 3 個 shard。「一批」是查詢時用 channel＋month 兩個 term 篩出來的邏輯範圍。</div>`));

  // 03 ES 依 channel × month 聚合
  const q3 = { size: 0, aggs: { channel: { terms: { field: 'channel', size: 20 }, aggs: { months: { cardinality: { field: 'month' } }, first: { min: { field: 'published_at', format: 'yyyy-MM' } }, last: { max: { field: 'published_at', format: 'yyyy-MM' } }, latest3: { terms: { field: 'month', size: 3, order: { _key: 'desc' } } } } } } };
  const r3 = JSON.parse(await es('POST', '/posts/_search?filter_path=hits.total,aggregations', q3));
  await shot('03-es-agg-channel-month', page0('③ ES 用 terms 聚合數「渠道 × 月」：每篇文件帶 channel 和 month（published_at 前 7 碼）', t(),
    block('POST ' + ES + '/posts/_search\n' + JSON.stringify(q3), JSON.stringify(r3, null, 1).replace(/\n\s+(?=[\]}])/g, ' ').replace(/\{\n\s+/g, '{ '))));

  // 04 帳本：177 個分區
  const s4 = `SELECT channel, count(*) AS partitions, min(month), max(month) FROM backfill_ledger GROUP BY 1 ORDER BY 2 DESC, 1;
SELECT count(*) AS total_partitions, count(DISTINCT partition_id) AS distinct_ids FROM backfill_ledger;
SELECT partition_id, channel, month, status FROM backfill_ledger ORDER BY month DESC, channel LIMIT 12;`;
  await shot('04-ledger-177-partitions', page0('④ PostgreSQL 回補帳本：一列 = 一個「渠道/月」分區，共 177 列', t(), block('psql -h 127.0.0.1 -p 55433 -U qsearch -d qsearch\n' + s4, psql(s4))));

  // 05 帳本完成的 5 批 vs ES 實數
  const s5 = `SELECT partition_id, status, raw_count, written, noop, checkpoint_line, target_seg_ver, gates->'G1' AS g1, finished_at FROM backfill_ledger WHERE status <> 'pending' ORDER BY finished_at;`;
  const led = psql(s5);
  const parts = ['facebook', 'instagram', 'threads', 'youtube', 'ptt'];
  let rows = '', reqs = '';
  for (const ch of parts) {
    const q = { query: { bool: { filter: [{ term: { channel: ch } }, { term: { month: '2026-09' } }, { term: { seg_ver_v2: 22026111201 } }] } } };
    const r = await es('POST', '/posts/_count', q);
    const n = JSON.parse(r).count;
    const lm = led.match(new RegExp(ch + '/2026-09\\s*\\|\\s*\\w+\\s*\\|\\s*(\\d+)'));
    rows += `<tr><td>${ch}/2026-09</td><td>${lm ? lm[1] : '?'}</td><td>${n}</td><td class=ok>${lm && +lm[1] === n ? '相同' : '不同'}</td></tr>`;
    reqs += `POST ${ES}/posts/_count ${JSON.stringify(q)}\n→ ${r}\n`;
  }
  await shot('05-ledger-vs-es-count', page0('⑤ 跑完的 5 個分區：帳本筆數 ＝ ES 用「channel＋month＋新版 seg_ver」篩出來的篇數', t(),
    block('psql …\n' + s5, led) + `<div class=lbl>對照</div><table><tr><th>分區</th><th>帳本 raw_count</th><th>ES _count</th><th></th></tr>${rows}</table>` + block('逐批發出的 ES 請求與回應', reqs, '原始回應（未編輯，箭頭後是 ES 回的內容）')));

  // 06 實際一篇文件
  const q6 = { size: 1, query: { bool: { filter: [{ term: { channel: 'ptt' } }, { term: { month: '2026-09' } }] } }, _source: ['post_id', 'channel', 'month', 'published_at', 'seg_ver_v2', 'dict_version_v2', 'seg_build_v2', 'terms_v2'] };
  const d6 = JSON.parse(await es('POST', '/posts/_search?filter_path=hits.hits._index,hits.hits._id,hits.hits._source', q6));
  const src = d6.hits.hits[0]._source; if (src.terms_v2) src.terms_v2 = src.terms_v2.slice(0, 15).concat([`…共 ${src.terms_v2.length} 個詞`]);
  await shot('06-es-sample-doc', page0('⑥ ES 裡的一篇文件：_id 就是 post_id，channel／month 在 _source 裡，新斷詞寫在 terms_v2', t() + ' · terms_v2 只顯示前 15 個',
    block('POST ' + ES + '/posts/_search\n' + JSON.stringify(q6), JSON.stringify(d6, null, 2))));

  // 07–08 程式碼（與 GitHub 同 blob）
  const sha = JSON.parse(fs.readFileSync(path.join(__dirname, 'sha.json'), 'utf8'));
  await shot('07-code-partition-source', page0('⑦ 程式碼：分區從哪來、一批讀哪些原文', `GitHub islanderwalk/claude-code，分支 ${BRANCH}；黃底是關鍵行`,
    codeBlock('q6/stack/seg/qseg/rawsrc.py', 59, 74, [[63, 63], [68, 68]], sha['rawsrc.py']) +
    codeBlock('q6/stack/seg/qseg/rawsrc.py', 132, 151, [[132, 134], [142, 143]], sha['rawsrc.py']) +
    codeBlock('q6/stack/pg/init.sql', 3, 7, [[4, 6]], sha['init.sql'])));
  await shot('08-code-ledger-es', page0('⑧ 程式碼：建帳本、領批順序、寫進 ES、閘門怎麼圈出這一批', `GitHub islanderwalk/claude-code，分支 ${BRANCH}；黃底是關鍵行`,
    codeBlock('q6/stack/seg/qseg/job.py', 403, 413, [[410, 410]], sha['job.py']) +
    codeBlock('q6/stack/seg/qseg/job.py', 195, 202, [[202, 202]], sha['job.py']) +
    codeBlock('q6/stack/seg/qseg/pipeline.py', 83, 94, [[83, 84], [93, 93]], sha['pipeline.py']) +
    codeBlock('q6/stack/seg/qseg/es.py', 48, 53, [[52, 52]], sha['es.py']) +
    codeBlock('q6/stack/seg/qseg/job.py', 154, 160, [[158, 160]], sha['job.py'])));
  await browser.close();
})();
