const CH = [['facebook', 'Facebook'], ['instagram', 'Instagram'], ['youtube', 'YouTube'], ['ptt', '論壇 PTT'], ['media', '媒體'], ['threads', 'Threads']];
const NOISE = { stopword: '停用詞', symbol: '標點符號', number: '數字／電話', url_or_code: '網址殘片', single_char: '單字' };
const $ = id => document.getElementById(id);
const on = new Set();
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let last = null;

function params(extra) {
  const p = new URLSearchParams({ q: $('q').value.trim(), channels: [...on].join(','), date_from: $('from').value, date_to: $('to').value,
    size: $('size').value, soft: $('soft').checked ? 1 : 0, merge: $('merge').checked ? 1 : 0, ...(extra || {}) });
  return p.toString();
}

async function overview() {
  const o = await (await fetch('/api/overview')).json();
  $('env').innerHTML = `<span>Elasticsearch <b>${o.es_version}</b></span><span>文章 <b>${o.total.toLocaleString()}</b></span>` +
    `<span>字典 <b>${o.overlay.dict_version || '—'}</b></span><span>seg_ver 最大 <b>${o.max_seg_ver_v2 ?? '—'}</b></span>`;
  const by = Object.fromEntries(o.channels.map(c => [c.channel, c]));
  $('chips').innerHTML = '';
  CH.forEach(([k, label]) => {
    const c = by[k]; const el = document.createElement('span');
    el.className = 'chip' + (on.has(k) ? ' on' : ''); el.dataset.ch = k;
    el.innerHTML = `${label}<small>${c ? c.docs.toLocaleString() : 0}</small>`;
    el.onclick = () => { on.has(k) ? on.delete(k) : on.add(k); el.classList.toggle('on'); run(); };
    $('chips').appendChild(el);
  });
}

function meta(r) {
  const dv = Object.entries(r.dict_versions).map(([k, v]) => `${k} ${v.toLocaleString()}`).join('、');
  return `範圍 ${r.total_docs.toLocaleString()} 篇｜ES took ${r.es_took_ms} ms｜API ${r.duration_ms} ms｜${r.cache === 'hit' ? '快取' : '即時'}<br>字典：${dv}`;
}

function noise(el, r) {
  const n = r.noise_in_top, total = Object.values(n).reduce((a, b) => a + b, 0);
  el.innerHTML = total === 0 ? `<span class="badge ok">前 ${r.buckets.length} 詞沒有雜訊</span>` :
    Object.entries(n).filter(([, v]) => v).map(([k, v]) => `<span class="badge bad">${NOISE[k]} ${v}</span>`).join('');
}

async function run() {
  $('go').disabled = true;
  const t0 = performance.now();
  const r = await (await fetch('/api/compare?' + params())).json();
  last = r;
  const pal1 = [css('--v1'), css('--muted'), css('--text')], pal2 = [css('--accent'), css('--text'), '#0e7490', '#7c3aed', '#b45309'];
  layoutCloud($('cloud-v1'), r.v1.buckets, { palette: pal1, onClick: t => posts(t, 'v1') });
  layoutCloud($('cloud-v2'), r.v2.buckets, { palette: pal2, onClick: t => posts(t, 'v2') });
  $('meta-v1').innerHTML = meta(r.v1); $('meta-v2').innerHTML = meta(r.v2);
  noise($('noise-v1'), r.v1); noise($('noise-v2'), r.v2);
  const li = x => `<li class="${x.noise ? 'n' : ''}">${x.term} <span class="muted">${(x.v1_count ?? x.v2_count).toLocaleString()}</span></li>`;
  $('only-v1').innerHTML = r.only_v1.slice(0, 20).map(li).join('');
  $('only-v2').innerHTML = r.only_v2.slice(0, 20).map(li).join('');
  $('rank').querySelector('tbody').innerHTML = r.diff.filter(x => x.v2_rank && x.v2_rank <= 20).map(x =>
    `<tr><td>${x.term}</td><td>${x.v2_rank}</td><td>${x.v2_count.toLocaleString()}</td><td>${x.v1_rank ?? '—'}</td><td>${x.v1_count?.toLocaleString() ?? '—'}</td></tr>`).join('');
  $('scope').textContent = `同一範圍：舊 ${r.same_scope_docs[0].toLocaleString()} 篇／新 ${r.same_scope_docs[1].toLocaleString()} 篇` +
    (r.v2.excluded_soft_stopwords.length ? `｜查詢時排除軟停用詞 ${r.v2.excluded_soft_stopwords.length} 個` : '');
  $('foot').textContent = `request_id v1=${r.v1.request_id} v2=${r.v2.request_id}｜瀏覽器端總耗時 ${Math.round(performance.now() - t0)} ms｜` +
    `新版詞表 doc_count_error_upper_bound=${r.v2.doc_count_error_upper_bound}`;
  $('go').disabled = false;
}

async function posts(term, seg) {
  const r = await (await fetch('/api/posts?' + params({ term, seg, size: 20 }))).json();
  $('dr-title').textContent = `「${term}」 ${seg === 'v2' ? '新版' : '舊版'}：${r.total.toLocaleString()} 篇`;
  $('dr-body').innerHTML = r.posts.map(p => `<div class="post"><div class="h"><b>${p.channel}</b><span>${p.source || ''}</span>` +
    `<span>${(p.published_at || '').slice(0, 16).replace('T', ' ')}</span>${p.url ? `<a href="${p.url}" target="_blank" rel="noopener">原文</a>` : ''}</div>` +
    `<p>${(p.highlight[0] || (p.content || '').slice(0, 160)).replace(/\n/g, ' ')}</p>` +
    `<div class="terms">新：${(p.terms_v2 || []).slice(0, 14).join('／')}</div><div class="terms">舊：${(p.terms_v1 || []).slice(0, 14).join('／')}</div></div>`).join('');
  $('drawer').hidden = false;
}

$('dr-close').onclick = () => { $('drawer').hidden = true; };
$('go').onclick = run;
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') run(); });
const u = new URLSearchParams(location.search);
if (u.get('q')) $('q').value = u.get('q');
(u.get('channels') || '').split(',').filter(Boolean).forEach(c => on.add(c));
if (u.get('from')) $('from').value = u.get('from');
if (u.get('to')) $('to').value = u.get('to');
overview().then(run).then(() => { if (u.get('term')) posts(u.get('term'), u.get('seg') || 'v2'); });
