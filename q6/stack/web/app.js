/* PM A/B 頁：每 2 秒讀 /api/status，ES 狀態 token 一變就重查兩版文字雲並標出變化；
   下半部是資料工程端的真實操作（發版、簽核、重播、採集、限速），全部打同一套 API。
   計畫書修正：2 秒重繪不清掉正在輸入的簽核備註（keepInputs＋列簽章不變就不重繪）；A/B 兩欄共用字級尺度；
   排版放不下的詞顯示出來；使用者再按查詢就中止上一次（AbortController）；正在響的告警顯示在頂部（/api/status.alerts）；
   瀏覽器端量到的 API 往返、首次出雲、丟詞數、雲上篇數與抽屜不一致都交給 rum.js 送 /api/rum。 */
const CH = [['facebook', 'Facebook'], ['instagram', 'Instagram'], ['youtube', 'YouTube'], ['ptt', '論壇 PTT'], ['media', '媒體'], ['threads', 'Threads']];
const NOISE = { stopword: '停用詞', symbol: '標點符號', number: '數字／電話', url_or_code: '網址殘片', single_char: '單字' };
const STATUS = { pending: '待回補', running: '回補中', awaiting_pm: '等 PM 簽核', passed: '通過', failed: '閘門沒過' };
const RUN = { queued: '已排入', loading: '載入字典', running: '定向重跑', done: '完成', failed: '失敗', skipped: '略過' };
const $ = id => document.getElementById(id);
const on = new Set();
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const num = n => (n ?? 0).toLocaleString();
const t = iso => iso ? new Date(iso).toLocaleTimeString('zh-TW', { hour12: false }) : '—';
const dt = iso => iso ? new Date(iso).toLocaleString('zh-TW', { hour12: false }).replace(/\//g, '-') : '—';
const ago = iso => { if (!iso) return '—'; const s = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000)); return s < 60 ? `${s} 秒前` : s < 3600 ? `${Math.round(s / 60)} 分前` : `${(s / 3600).toFixed(1)} 小時前`; };
let last = null, token = null, lastChange = null, polling = false, running = false, fullLedger = false, watchRun = null;
let runCtl = null, ledgerSig = null;
const rum = window.rum || { api() {}, render() {}, firstCloud() {}, mismatch() {} };
const prev = { v1: null, v2: null };

async function api(path, opts) {
  const t0 = performance.now();
  let ok = false;
  try {
    const r = await fetch(path, opts);
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    ok = true;
    return j;
  } finally {
    if (!(opts && opts.signal && opts.signal.aborted)) rum.api(path, performance.now() - t0, ok);   // 中止的不算
  }
}
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ by: $('by').value.trim() || 'PM', ...body }) });
const say = (id, msg, bad) => { const el = $(id); el.textContent = msg; el.className = 'muted ' + (bad ? 'err' : 'okmsg'); };

function params(extra) {
  return new URLSearchParams({ q: $('q').value.trim(), channels: [...on].join(','), date_from: $('from').value, date_to: $('to').value,
    size: $('size').value, soft: $('soft').checked ? 1 : 0, merge: $('merge').checked ? 1 : 0, ...(extra || {}) }).toString();
}

function banner(msg) { const b = $('banner'); b.hidden = !msg; b.textContent = msg || ''; }

async function overview() {
  const o = await api('/api/overview');
  $('note-v1').textContent = (($('left') && $('left').value) === 'v1') ? o.v1_note : o.v0_note;
  const by = Object.fromEntries(o.channels.map(c => [c.channel, c]));
  $('chips').innerHTML = '';
  CH.forEach(([k, label]) => {
    const c = by[k]; const el = document.createElement('span');
    el.className = 'chip' + (on.has(k) ? ' on' : ''); el.dataset.ch = k;
    el.innerHTML = `${label}<small>${c ? num(c.docs) : 0}</small>`;
    el.onclick = () => { on.has(k) ? on.delete(k) : on.add(k); el.classList.toggle('on'); run(); };
    $('chips').appendChild(el);
  });
}

function meta(r) {
  const dv = Object.entries(r.dict_versions).map(([k, v]) => `${k} ${num(v)}`).join('、');
  return `範圍 ${num(r.total_docs)} 篇｜ES took ${r.es_took_ms} ms｜API ${r.duration_ms} ms｜${r.cache === 'hit' ? '快取' : '即時'}<br>字典：${dv || '—'}`;
}

function noise(el, r) {
  const n = r.noise_in_top, total = Object.values(n).reduce((a, b) => a + b, 0);
  el.innerHTML = total === 0 ? `<span class="badge ok">前 ${r.buckets.length} 詞沒有雜訊</span>` :
    Object.entries(n).filter(([, v]) => v).map(([k, v]) => `<span class="badge bad">${NOISE[k]} ${v}</span>`).join('');
}

/* 與上一次同一個查詢的結果比：新進前 N 的詞、掉出去的詞、篇數變了的詞 */
function changes(seg, buckets) {
  const old = prev[seg];
  prev[seg] = buckets;
  if (!old) return new Set();
  const oldMap = new Map(old.map(b => [b.term, b.count])), newMap = new Map(buckets.map(b => [b.term, b.count]));
  const added = buckets.filter(b => !oldMap.has(b.term)), gone = old.filter(b => !newMap.has(b.term));
  const moved = buckets.filter(b => oldMap.has(b.term) && oldMap.get(b.term) !== b.count)
    .map(b => ({ term: b.term, from: oldMap.get(b.term), to: b.count })).sort((a, b) => Math.abs(b.to - b.from) - Math.abs(a.to - a.from)).slice(0, 8);
  const el = $('chg-' + seg);
  if (!added.length && !gone.length && !moved.length) { el.innerHTML = ''; return new Set(); }
  el.innerHTML = `<span class="muted">${t(new Date().toISOString())} 變化：</span>` +
    added.map(b => `<span class="chg new">＋${esc(b.term)} <small>${num(b.count)}</small></span>`).join('') +
    gone.map(b => `<span class="chg gone">－${esc(b.term)}</span>`).join('') +
    moved.map(m => `<span class="chg ${m.to > m.from ? 'up' : 'down'}">${esc(m.term)} ${num(m.from)}→${num(m.to)}</span>`).join('');
  return new Set(added.map(b => b.term));
}

function dropped(seg, res) {
  const el = $('drop-' + seg); if (!el) return;
  el.textContent = res.dropped.length ? `排版放不下 ${res.dropped.length} 詞：${res.dropped.join('、')}` : '';
  el.title = res.dropped.length ? '這些詞在前 N 名裡，但 640×380 的版面擺不下；差異清單與排名對照仍然有' : '';
}

async function run(fromPoll) {
  if (running && fromPoll) return;        // 自動重查不打斷使用者正在等的查詢
  if (runCtl) runCtl.abort();             // 使用者再按查詢：中止上一次（最長 180 秒），不用等它跑完
  const ctl = new AbortController();
  runCtl = ctl;
  running = true; $('go').disabled = true;
  const t0 = performance.now();
  try {
    const left = ($('left') && $('left').value) || 'v0';
    const r = await api('/api/compare?' + params({ a: left, b: 'v2' }), { signal: ctl.signal });
    if (runCtl !== ctl) return;           // 已經被更新的查詢取代
    last = r;
    banner(null);
    const hl1 = changes('v1', r.a.buckets), hl2 = changes('v2', r.b.buckets);
    const pal1 = [css('--v1'), css('--muted'), css('--text')], pal2 = [css('--accent'), css('--text'), '#0e7490', '#7c3aed', '#b45309'];
    const counts = [...r.a.buckets, ...r.b.buckets].map(b => b.count);
    const sc = counts.length ? { max: Math.max(...counts), min: Math.min(...counts) } : {};   // 兩欄共用字級尺度：同篇數＝同字級
    const d1 = layoutCloud($('cloud-v1'), r.a.buckets, { ...sc, palette: pal1, highlight: hl1, onClick: t => posts(t, left) });
    const d2 = layoutCloud($('cloud-v2'), r.b.buckets, { ...sc, palette: pal2, highlight: hl2, onClick: t => posts(t, 'v2') });
    dropped('v1', d1); dropped('v2', d2);
    rum.render(left, d1.dropped.length); rum.render('v2', d2.dropped.length); rum.firstCloud();
    $('meta-v1').innerHTML = meta(r.a); $('meta-v2').innerHTML = meta(r.b);
    noise($('noise-v1'), r.a); noise($('noise-v2'), r.b);
    const li = x => `<li class="${x.noise ? 'n' : ''}">${esc(x.term)} <span class="muted">${num(x.a_count ?? x.b_count)}</span></li>`;
    $('only-v1').innerHTML = r.only_a.slice(0, 20).map(li).join('');
    $('only-v2').innerHTML = r.only_b.slice(0, 20).map(li).join('');
    $('rank').querySelector('tbody').innerHTML = r.diff.filter(x => x.b_rank && x.b_rank <= 20).map(x =>
      `<tr><td>${esc(x.term)}</td><td>${x.b_rank}</td><td>${num(x.b_count)}</td><td>${x.a_rank ?? '—'}</td><td>${x.a_count != null ? num(x.a_count) : '—'}</td></tr>`).join('');
    $('scope').textContent = `同一範圍：舊 ${num(r.same_scope_docs[0])} 篇／新 ${num(r.same_scope_docs[1])} 篇` +
      (r.b.excluded_soft_stopwords.length ? `｜查詢時排除軟停用詞 ${r.b.excluded_soft_stopwords.length} 個` : '') + (fromPoll ? '｜資料變了，自動重查' : '');
    $('foot').textContent = `request_id ${left}=${r.a.request_id} v2=${r.b.request_id}｜瀏覽器端總耗時 ${Math.round(performance.now() - t0)} ms｜` +
      `新版詞表 doc_count_error_upper_bound=${r.b.doc_count_error_upper_bound}｜ES 狀態 token=${r.state_token}`;
  } catch (e) {
    if (e.name === 'AbortError') return;
    banner('查不到文字雲：' + e.message);
  } finally {
    if (runCtl === ctl) { running = false; $('go').disabled = false; runCtl = null; }
  }
}

async function posts(term, seg) {
  const r = await api('/api/posts?' + params({ term, seg, size: 20 }));
  const left = ($('left') && $('left').value) || 'v0';
  const shown = last && (seg === 'v2' ? last.b : last.a).buckets.find(b => b.term === term);
  const mismatch = shown && !shown.merged_from && shown.count !== r.total;   // 合併詞的篇數是加總，不能跟單一詞比
  if (mismatch) rum.mismatch(seg);
  $('dr-title').textContent = `「${term}」 ${seg === 'v2' ? '新版' : '舊版'}：${num(r.total)} 篇` + (mismatch ? `（雲上 ${num(shown.count)} 篇：資料在兩次查詢之間變了）` : '');
  $('dr-body').innerHTML = r.posts.map(p => `<div class="post"><div class="h"><b>${esc(p.channel)}</b><span>${esc(p.source || '')}</span>` +
    `<span>${(p.published_at || '').slice(0, 16).replace('T', ' ')}</span>${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">原文</a>` : ''}` +
    `<span class="muted">${esc(p.dict_version_v2 || '')}</span></div>` +
    `<p>${(p.highlight[0] || esc((p.content || '').slice(0, 160))).replace(/\n/g, ' ')}</p>` +
    `<div class="terms">新 v2：${(p.terms_v2 || []).slice(0, 14).map(esc).join('／')}</div><div class="terms">舊 ${left}：${(p['terms_' + left] || []).slice(0, 14).map(esc).join('／')}</div></div>`).join('');
  $('drawer').hidden = false;
}

/* ---------- 即時狀態 ---------- */
function pill(ok, text) { return `<span class="pill ${ok ? 'ok' : 'bad'}">${text}</span>`; }

function renderStatus(s) {
  const es = s.es || {};
  const svc = Object.fromEntries((s.services || []).map(x => [x.pipeline, x]));
  $('env').innerHTML = (es.ok ? `<span>文章 <b>${num(es.total)}</b></span><span>有舊詞表 <b>${num(es.with_v1)}</b></span><span>有新詞表 <b>${num(es.with_v2)}</b></span>` : `<span class="err">${esc(es.error || 'ES 無回應')}</span>`) +
    `<span>字典 current <b>${esc(s.dict.current || s.dict.overlay_version || '—')}</b></span>` +
    ['v1', 'v2'].map(p => { const x = svc[p]; return `<span>seg-${p} ${x ? pill(x.alive, x.alive ? '運作中' : '沒心跳') + ` <b>${esc(x.dict_version)}</b>` : '<span class="pill off">未啟動</span>'}</span>`; }).join('');
  $('dict-current').innerHTML = `<div><span class="k">current</span><b>${esc(s.dict.current || '—')}</b>${s.dict.root_mounted ? '' : ' <span class="err">（API 沒掛 artifact 目錄，不能發版）</span>'}</div>` +
    `<div><span class="k">查詢端 overlay</span>${esc(s.dict.overlay_version || '—')}</div>` +
    `<div><span class="k">最後斷詞</span>v1 ${t(es.last_seg_at_v1)}｜v2 ${t(es.last_seg_at_v2)}</div>`;
  $('services').querySelector('tbody').innerHTML = (s.services || []).map(x =>
    `<tr><td>${esc(x.service)}</td><td>${esc(x.dict_version)}</td><td>${x.seg_ver}</td><td>${pill(x.alive, ago(x.heartbeat_at))}</td></tr>`).join('') || '<tr><td colspan="4" class="muted">斷詞服務還沒啟動（./run.sh live）</td></tr>';
  $('versions').querySelector('tbody').innerHTML = (s.dict.versions || []).slice().reverse().map(v =>
    `<tr class="${v.dict_version === s.dict.current ? 'cur' : ''}"><td>${esc(v.dict_version)}${v.dict_version === s.dict.current ? ' <small>current</small>' : ''}</td>` +
    `<td>${(v.add_words || []).map(w => esc(Array.isArray(w) ? w[0] : w)).join('、') || '<span class="muted">—</span>'}</td>` +
    `<td class="muted">${esc(v.requested_by || '')} ${v.built_at ? dt(v.built_at) : ''}<br>${esc(v.note || '')}</td></tr>`).join('');
  renderAlerts(s.alerts || []);
  renderRun(s.runs || []);
  renderRecent(s.recent || []);
  renderCollector(s.collector || {});
  renderLedger(s.ledger || {}, s.control);
  $('events').innerHTML = (s.events || []).map(e => `<li><span class="muted">${t(e.at)}</span> <b class="k-${esc(e.kind)}">${esc(e.kind)}</b> <span class="muted">${esc(e.actor || '')}</span> ${esc(summary(e))}</li>`).join('');
}

function renderAlerts(list) {
  const el = $('alerts'); if (!el) return;
  if (!list.length) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  el.innerHTML = `<b>告警 ${list.length}</b>` + list.map(a =>
    `<span class="al ${esc(a.severity || 'warn')}" title="${esc(JSON.stringify(a.labels || {}))}"><i>${esc(a.severity || '')}</i>${esc(a.alertname || '')}` +
    `<small>${esc(a.summary || '')}｜${ago(a.starts_at || a.at)}</small></span>`).join('');
}

function summary(e) {
  const d = e.detail || {};
  switch (e.kind) {
    case 'release': return `${d.dict_version} 加詞 ${(d.words || []).map(w => Array.isArray(w) ? w[0] : w).join('、')}（run #${d.run_id}）`;
    case 'approve': return `簽核 ${d.partition}${d.note ? '：' + d.note : ''}`;
    case 'waive': return `豁免 ${d.partition} 的 ${d.gate}：${d.note}`;
    case 'replay': return `重播 ${d.published} 篇（${(d.channels || []).join('、')}），其中 ${d.already_in_es} 篇原本就在`;
    case 'collect': return `${d.feed} HTTP ${d.status ?? '—'} 新 ${d.new ?? 0} 篇${d.error ? '：' + d.error : ''}`;
    case 'collect_request': return `採集器 ${d.action}`;
    case 'control': return `回補限速 ${d.max_docs_per_sec ?? '不變'}／暫停 ${d.paused ?? '不變'}（${d.reason || ''}）`;
    default: return JSON.stringify(d).slice(0, 120);
  }
}

function renderRun(runs) {
  const r = runs.find(x => x.id === watchRun) || runs[0];
  if (!r) { $('run').innerHTML = '<div class="muted">還沒有發過版</div>'; return; }
  const pct = r.candidates ? Math.min(100, Math.round(100 * (r.scanned || 0) / r.candidates)) : (r.status === 'done' ? 100 : 0);
  const ex = (r.examples || []).slice(0, 3).map(x => `<div class="ex"><span class="muted">${esc(x.id)}</span> ${(x.removed || []).map(w => `<s>${esc(w)}</s>`).join(' ')} → ${(x.added || []).map(w => `<b>${esc(w)}</b>`).join(' ')}</div>`).join('');
  $('run').innerHTML = `<div class="runhead"><b>run #${r.id}</b> ${esc(r.dict_version)} 加詞 ${(r.words || []).map(esc).join('、')} <span class="pill ${r.status === 'done' ? 'ok' : r.status === 'failed' ? 'bad' : 'warn'}">${RUN[r.status] || r.status}</span>` +
    ` <span class="muted">${esc(r.worker || '')} ${r.requested_by ? '由 ' + esc(r.requested_by) : ''}</span></div>` +
    `<div class="prog"><i style="width:${pct}%"></i></div>` +
    `<div class="muted">候選 ${num(r.candidates)} 篇｜掃過 ${num(r.scanned)}｜詞表真的變了 ${num(r.terms_changed)}｜寫入 ${num(r.written)}｜noop ${num(r.noop)}｜錯 ${num(r.errors)}` +
    `${r.seconds != null ? `｜${r.seconds} 秒` : ''}｜排入 ${t(r.queued_at)}${r.finished_at ? ' → 完成 ' + t(r.finished_at) : ''}${r.error ? `<br><span class="err">${esc(r.error)}</span>` : ''}</div>` + ex +
    (runs.length > 1 ? `<div class="muted small">之前：${runs.filter(x => x.id !== r.id).map(x => `#${x.id} ${esc(x.dict_version)} ${RUN[x.status] || x.status}`).join('；')}</div>` : '');
}

function renderRecent(rows) {
  $('recent').querySelector('tbody').innerHTML = rows.map(p => {
    const lag = p.seg_at_v2 && p.fetched_at ? Math.round((new Date(p.seg_at_v2) - new Date(p.fetched_at)) / 1000) : null;
    return `<tr><td><b>${esc(p.channel)}</b><br><span class="muted">${esc(p.source || '')}</span></td><td class="muted">${dt(p.published_at)}<br>抓 ${t(p.fetched_at)}</td>` +
      `<td class="content">${p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">` : ''}${esc(p.content)}${p.url ? '</a>' : ''}</td>` +
      `<td class="terms">${(p.terms_v2 || []).map(esc).join('／') || '<span class="muted">（還沒斷）</span>'}<br><span class="muted">${esc(p.dict_version_v2 || '')}</span></td>` +
      `<td class="terms muted">${(p.terms_v1 || []).map(esc).join('／') || '（還沒斷）'}</td>` +
      `<td>${lag == null ? '—' : lag < 0 ? '回補' : lag + ' 秒'}</td></tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">還沒有文章</td></tr>';
}

function renderCollector(c) {
  const ctl = c.control || {};
  $('coll-ctl').textContent = `每 ${ctl.poll_sec ?? 300} 秒輪詢${ctl.paused ? '｜已暫停' : ''}${ctl.run_now ? '｜正在抓' : ''}`;
  $('feeds').querySelector('tbody').innerHTML = (c.feeds || []).map(f =>
    `<tr><td>${esc(f.feed)}</td><td class="muted">${f.last_poll_at ? ago(f.last_poll_at) : '—'}</td><td>${f.last_status ?? '—'}</td><td>${f.last_new}／${f.total_new}</td>` +
    `<td>${f.error ? `<span class="err">${esc(f.error)}${f.next_poll_at ? '，' + t(f.next_poll_at) + ' 再試' : ''}</span>` : f.last_status === 304 ? '<span class="muted">沒變</span>' : f.last_poll_at ? pill(true, `看到 ${f.items_seen ?? 0} 則`) : '<span class="muted">等第一次</span>'}</td></tr>`).join('') ||
    '<tr><td colspan="5" class="muted">採集器還沒啟動（./run.sh live）</td></tr>';
}

function gatesCell(g) {
  if (!g) return '<span class="muted">—</span>';
  return ['G1', 'G2', 'G3', 'G4'].map(k => { const x = g[k]; if (!x) return ''; const ok = x.pass || !!x.waiver;
    return `<span class="pill ${ok ? 'ok' : 'bad'}" title="${esc(JSON.stringify(x))}">${k}${x.waiver ? '豁免' : ''}</span>`; }).join(' ');
}

function ledgerRow(r) {
  const acts = [];
  if (r.status === 'awaiting_pm') acts.push(`<input class="note" placeholder="簽核備註" data-p="${esc(r.partition_id)}"><button class="sm" data-approve="${esc(r.partition_id)}">簽核通過</button>`);
  if (r.status === 'failed') {
    const bad = ['G1', 'G2', 'G3'].filter(k => r.gates && r.gates[k] && !r.gates[k].pass && !r.gates[k].waiver);
    acts.push(`<input class="note" placeholder="豁免說明（必填）" data-p="${esc(r.partition_id)}">` + bad.map(k => `<button class="sm ghost" data-waive="${k}" data-p="${esc(r.partition_id)}">豁免 ${k}</button>`).join(''));
  }
  return `<tr class="st-${esc(r.status)}"><td>${esc(r.partition_id)}</td><td><span class="pill ${r.status === 'passed' ? 'ok' : r.status === 'failed' ? 'bad' : r.status === 'awaiting_pm' ? 'warn' : 'off'}">${STATUS[r.status] || r.status}</span></td>` +
    `<td>${num(r.raw_count)}</td><td class="muted">${num(r.written)}／${num(r.noop)}／${num(r.errors)}${r.docs_per_sec ? `<br>${r.docs_per_sec}/s` : ''}</td><td>${gatesCell(r.gates)}</td><td class="acts">${acts.join(' ')}</td></tr>`;
}

function renderLedger(l, control) {
  const by = l.by_status || {};
  $('ledger-counts').innerHTML = Object.entries(STATUS).map(([k, label]) => `<span class="chip ${by[k] ? 'on' : ''}">${label}<small>${num(by[k])}</small></span>`).join('');
  if (control) {
    if (document.activeElement !== $('rps')) $('rps').value = control.max_docs_per_sec;
    if (document.activeElement !== $('paused')) $('paused').checked = !!control.paused;
    $('ctl-msg').textContent = `${control.paused ? '已暫停' : '進行中'}｜探針基準 ${control.canary_baseline_ms != null ? Math.round(control.canary_baseline_ms) + ' ms' : '—'}｜${control.reason || ''}`;
  }
  if (!fullLedger) {
    const rows = l.attention || [];
    const sig = JSON.stringify([rows.map(r => [r.partition_id, r.status, r.written, r.noop, r.errors, r.gates]), by]);
    if (sig === ledgerSig) return;        // 列沒變就不重繪：正在打的簽核備註不會被清掉
    ledgerSig = sig;
    keepInputs($('ledger').querySelector('tbody'), tb => { tb.innerHTML = rows.map(ledgerRow).join('') ||
      `<tr><td colspan="6" class="muted">沒有在等人的分區（${Object.entries(by).map(([k, v]) => `${STATUS[k] || k} ${v}`).join('、') || '帳本是空的，先跑 ./run.sh seed'}）</td></tr>`; });
  }
}

/* 重繪表格前記下每個備註欄的內容與游標，重繪後放回去（列有變才會走到這裡） */
function keepInputs(tbody, redraw) {
  const saved = {}; let focus = null, sel = null;
  tbody.querySelectorAll('input.note').forEach(i => {
    if (i.value) saved[i.dataset.p] = i.value;
    if (document.activeElement === i) { focus = i.dataset.p; sel = [i.selectionStart, i.selectionEnd]; }
  });
  redraw(tbody);
  tbody.querySelectorAll('input.note').forEach(i => {
    if (saved[i.dataset.p] != null) i.value = saved[i.dataset.p];
    if (focus === i.dataset.p) { i.focus(); try { i.setSelectionRange(sel[0], sel[1]); } catch (e) { /* 沒差 */ } }
  });
}

async function loadFullLedger() {
  const r = await api('/api/ops/ledger');
  keepInputs($('ledger').querySelector('tbody'), tb => { tb.innerHTML = r.rows.map(ledgerRow).join(''); });
  ledgerSig = null;
}

$('ledger').addEventListener('click', async e => {
  const b = e.target.closest('button'); if (!b) return;
  const p = b.dataset.approve || b.dataset.p;
  const note = (b.closest('td').querySelector('input.note') || {}).value || '';
  try {
    if (b.dataset.approve) await post('/api/ops/approve', { partition: p, note });
    else if (b.dataset.waive) { if (!note.trim()) throw new Error('豁免一定要寫說明'); await post('/api/ops/approve', { partition: p, note, waive: b.dataset.waive }); }
    say('ctl-msg', `${p} 已處理`);
    if (fullLedger) loadFullLedger();
  } catch (err) { say('ctl-msg', err.message, true); }
});

async function status() {
  if (polling) return;
  polling = true;
  try {
    const s = await api('/api/status');
    renderStatus(s);
    if (s.es && s.es.ok === false) banner(s.es.error);
    const changed = token !== null && s.token !== token;
    token = s.token;
    if (changed) { lastChange = new Date(); if ($('auto').checked && s.es.ok) run(true); }
    $('live').classList.toggle('changed', !!lastChange && Date.now() - lastChange < 3000);
    $('live-text').textContent = `每 2 秒讀一次｜${s.es.ok ? 'ES 連線正常' : 'ES 無法查詢'}｜最後變化 ${lastChange ? t(lastChange.toISOString()) : '—'}`;
  } catch (e) {
    $('live-text').textContent = '連不上 API：' + e.message;
  }
  polling = false;
}

/* ---------- 操作 ---------- */
$('release').onclick = async () => {
  const words = $('words').value.split('\n').map(l => l.trim()).filter(Boolean).map(l => { const p = l.split(/\s+/); return p.length === 1 ? p[0] : { word: p[0], freq: p[1], pos: p[2] || 'n' }; });
  if (!words.length) return say('rel-msg', '至少一個詞', true);
  $('release').disabled = true; say('rel-msg', '建版中…');
  try {
    const r = await post('/api/ops/release', { words, note: $('rel-note').value });
    watchRun = r.run_id;
    say('rel-msg', `${r.dict_version}（seg_ver ${r.seg_ver}）已切 current，控制訊息已送出，等 seg-v2 載入並重跑…`);
    $('words').value = ''; $('rel-note').value = '';
  } catch (e) { say('rel-msg', e.message, true); }
  $('release').disabled = false;
};

CH.forEach(([k, label]) => { const l = document.createElement('label'); l.className = 'tog'; l.innerHTML = `<input type="checkbox" value="${k}" checked>${label}`; $('replay-ch').appendChild(l); });
$('replay').onclick = async () => {
  const channels = [...$('replay-ch').querySelectorAll('input:checked')].map(i => i.value);
  $('replay').disabled = true; say('replay-msg', '送出中…');
  try {
    const r = await post('/api/ops/replay', { channels, limit: Number($('replay-limit').value) || 0 });
    say('replay-msg', `已送 ${r.published} 篇進 Pub/Sub，${r.new} 篇是新的（${r.already_in_es} 篇原本就在，同版會 noop）`);
  } catch (e) { say('replay-msg', e.message, true); }
  $('replay').disabled = false;
};
['now', 'pause', 'resume'].forEach(a => { $('collect-' + a).onclick = async () => {
  try { await post('/api/ops/collect', { action: a === 'now' ? 'run_now' : a }); say('coll-msg', a === 'now' ? '已要求立刻抓一次（採集器 3 秒內開始）' : a === 'pause' ? '已暫停' : '已恢復'); }
  catch (e) { say('coll-msg', e.message, true); } }; });
$('ctl-apply').onclick = async () => {
  try { await post('/api/ops/control', { paused: $('paused').checked, max_docs_per_sec: Number($('rps').value) }); say('ctl-msg', '已套用，回補 Job 下一批就吃到'); }
  catch (e) { say('ctl-msg', e.message, true); }
};
$('ledger-all').onclick = async () => {
  fullLedger = !fullLedger; $('ledger-all').textContent = fullLedger ? '只看要處理的' : '顯示全部分區';
  if (fullLedger) await loadFullLedger();
};

$('dr-close').onclick = () => { $('drawer').hidden = true; };
$('go').onclick = () => run(false);
if ($('left')) $('left').addEventListener('change', () => { overview(); run(false); });
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') run(false); });
const u = new URLSearchParams(location.search);
if (u.get('q')) $('q').value = u.get('q');
(u.get('channels') || '').split(',').filter(Boolean).forEach(c => on.add(c));
if (u.get('from')) $('from').value = u.get('from');
if (u.get('to')) $('to').value = u.get('to');
overview().then(() => run(false)).then(() => { if (u.get('term')) posts(u.get('term'), u.get('seg') || 'v2'); })
  .catch(e => banner(e.message)).finally(() => { status(); setInterval(status, 2000); });
