/* 文字雲排版：阿基米德螺線逐詞放置，矩形碰撞檢查（不依賴外部函式庫，離線可用）。
   opts.highlight：這次才進前 N 的詞（畫底線）。opts.min／opts.max：字級尺度的篇數下限／上限，
   A/B 兩欄傳同一組，字級才能互比（同篇數＝同字級）。回傳 { placed, dropped }：放不下的詞不再默默丟掉，呼叫端要顯示。 */
window.layoutCloud = function (svg, words, opts) {
  const W = 640, H = 380, NS = 'http://www.w3.org/2000/svg';
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  if (!words.length) { const t = document.createElementNS(NS, 'text'); t.setAttribute('x', W / 2); t.setAttribute('y', H / 2);
    t.setAttribute('text-anchor', 'middle'); t.setAttribute('fill', 'currentColor'); t.textContent = '沒有符合的文章'; svg.appendChild(t); return { placed: 0, dropped: [] }; }
  const ctx = document.createElement('canvas').getContext('2d');
  const max = opts.max ?? words[0].count, min = opts.min ?? words[words.length - 1].count;
  const scale = c => max === min ? 28 : 12 + Math.max(0, Math.min(1, (c - min) / (max - min))) * 34;
  const placed = [], dropped = [];
  const hit = (a) => placed.some(b => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y);
  const palette = opts.palette;
  words.forEach((w, i) => {
    const fs = Math.round(scale(w.count));
    ctx.font = `${i < 3 ? 700 : 500} ${fs}px "Noto Sans TC","WenQuanYi Zen Hei",sans-serif`;
    const tw = ctx.measureText(w.term).width + 4, th = fs * 1.05;
    let ok = false;
    for (let t = 0; t < 2400; t += 1) {
      const a = 0.18 * t, r = 2.2 * a;
      const x = W / 2 + r * Math.cos(a) * 1.45 - tw / 2, y = H / 2 + r * Math.sin(a) - th / 2;
      if (x < 2 || y < 2 || x + tw > W - 2 || y + th > H - 2) continue;
      const box = { x, y, w: tw, h: th };
      if (hit(box)) continue;
      placed.push(box);
      const el = document.createElementNS(NS, 'text');
      el.setAttribute('x', x + 2); el.setAttribute('y', y + th * 0.82);
      el.setAttribute('font-size', fs); el.setAttribute('font-weight', i < 3 ? 700 : 500);
      el.setAttribute('fill', palette[i % palette.length]);
      const cls = [w.noise ? 'n-' + w.noise : '', opts.highlight && opts.highlight.has(w.term) ? 'hl' : ''].filter(Boolean).join(' ');
      if (cls) el.setAttribute('class', cls);
      el.textContent = w.term;
      const tip = document.createElementNS(NS, 'title');
      tip.textContent = `${w.term}：${w.count.toLocaleString()} 篇` + (w.merged_from ? `（合併：${w.merged_from.join('、')}）` : '') + (w.noise ? `｜雜訊：${w.noise}` : '');
      el.appendChild(tip);
      el.addEventListener('click', () => opts.onClick(w.term));
      svg.appendChild(el);
      ok = true;
      break;
    }
    if (!ok) dropped.push(w.term);
  });
  return { placed: placed.length, dropped };
};
