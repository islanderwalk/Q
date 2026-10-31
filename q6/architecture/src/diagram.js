// Renders a diagram spec into an absolutely positioned stage, then draws the connectors
// from the laid-out boxes, so arrows always land on the real node edges.
(function () {
  const NS = 'http://www.w3.org/2000/svg';
  const COLORS = { muted: '#5b6a74', accent: '#0c6b63', warn: '#9a5806', bad: '#b0223d', violet: '#6a3cc0', ink: '#16222b' };

  const el = (tag, cls, html) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  };
  const svgEl = (tag, attrs) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    return e;
  };
  const rectOf = n => ({ x: n.offsetLeft, y: n.offsetTop, w: n.offsetWidth, h: n.offsetHeight });

  function anchor(r, side, t) {
    t = t == null ? 0.5 : t;
    if (side === 'right') return [r.x + r.w, r.y + r.h * t];
    if (side === 'left') return [r.x, r.y + r.h * t];
    if (side === 'top') return [r.x + r.w * t, r.y];
    return [r.x + r.w * t, r.y + r.h];
  }

  function autoSides(a, b) {
    if (b.x >= a.x + a.w) return ['right', 'left'];
    if (b.x + b.w <= a.x) return ['left', 'right'];
    if (b.y >= a.y + a.h) return ['bottom', 'top'];
    return ['top', 'bottom'];
  }

  function route(E, s, e, fs) {
    const r = E.route || (fs === 'left' || fs === 'right' ? 'h' : 'v');
    if (r === 'points') {
      // null in a waypoint means "keep the previous point's coordinate"
      const out = [s];
      for (const [px, py] of E.points) {
        const prev = out[out.length - 1];
        out.push([px == null ? prev[0] : px, py == null ? prev[1] : py]);
      }
      out.push(e);
      return out;
    }
    if (r === 'straight') return [s, e];
    if (r === 'h') { const mx = E.mx != null ? E.mx : (s[0] + e[0]) / 2; return [s, [mx, s[1]], [mx, e[1]], e]; }
    if (r === 'v') { const my = E.my != null ? E.my : (s[1] + e[1]) / 2; return [s, [s[0], my], [e[0], my], e]; }
    if (r === 'hv') return [s, [e[0], s[1]], e];
    if (r === 'vh') return [s, [s[0], e[1]], e];
    return [s, e];
  }

  const dedupe = pts => pts.filter((p, i) => i === 0 || Math.abs(p[0] - pts[i - 1][0]) > 0.5 || Math.abs(p[1] - pts[i - 1][1]) > 0.5);

  function pathD(pts, radius) {
    pts = dedupe(pts);
    let d = `M${pts[0][0]},${pts[0][1]}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const [px, py] = pts[i - 1], [cx, cy] = pts[i], [nx, ny] = pts[i + 1];
      const l1 = Math.hypot(cx - px, cy - py), l2 = Math.hypot(nx - cx, ny - cy);
      const rr = Math.min(radius, l1 / 2, l2 / 2);
      const ax = cx - (cx - px) / l1 * rr, ay = cy - (cy - py) / l1 * rr;
      const bx = cx + (nx - cx) / l2 * rr, by = cy + (ny - cy) / l2 * rr;
      d += ` L${ax},${ay} Q${cx},${cy} ${bx},${by}`;
    }
    const last = pts[pts.length - 1];
    return d + ` L${last[0]},${last[1]}`;
  }

  function longestMid(pts, frac) {
    pts = dedupe(pts);
    const f = frac == null ? 0.5 : frac;
    let best = -1, mid = pts[0];
    for (let i = 1; i < pts.length; i++) {
      const l = Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
      if (l > best) {
        best = l;
        mid = [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f];
      }
    }
    return mid;
  }

  window.renderDiagram = async function (D) {
    const canvas = el('div', 'canvas');
    canvas.style.width = (D.width || 1600) + 'px';
    const head = el('header', 'head');
    head.appendChild(el('div', 'eyebrow', D.eyebrow || ''));
    head.appendChild(el('h1', '', D.title));
    if (D.sub) head.appendChild(el('p', '', D.sub));
    canvas.appendChild(head);

    const stage = el('div', 'stage');
    stage.style.height = D.height + 'px';
    canvas.appendChild(stage);
    for (const L of D.lanes || []) {
      const e = el('div', 'lane' + (L.cls ? ' ' + L.cls : ''));
      Object.assign(e.style, { left: L.x + 'px', top: L.y + 'px', width: L.w + 'px', height: L.h + 'px' });
      if (L.label) e.appendChild(el('span', 'lane-label', L.label));
      stage.appendChild(e);
    }
    const svg = svgEl('svg', { class: 'wires' });
    stage.appendChild(svg);
    for (const B of D.blocks || []) {
      const e = el('div', 'block' + (B.cls ? ' ' + B.cls : ''), B.html);
      if (B.id) e.id = B.id;
      Object.assign(e.style, { left: B.x + 'px', top: B.y + 'px' });
      if (B.w) e.style.width = B.w + 'px';
      stage.appendChild(e);
    }
    for (const N of D.nodes || []) {
      const e = el('div', 'node' + (N.cls ? ' ' + N.cls : ''));
      e.id = N.id;
      Object.assign(e.style, { left: N.x + 'px', top: N.y + 'px', width: N.w + 'px' });
      if (N.h) e.style.minHeight = N.h + 'px';
      e.innerHTML = (N.tag ? `<span class="tag">${N.tag}</span>` : '') + `<b>${N.title}</b>` + (N.sub ? `<div class="sub">${N.sub}</div>` : '');
      stage.appendChild(e);
    }
    canvas.appendChild(el('footer', 'foot', `<span>${D.footL || ''}</span><span>${D.footR || ''}</span>`));
    document.body.appendChild(canvas);

    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));

    const W = stage.offsetWidth, H = stage.offsetHeight;
    Object.entries({ width: W, height: H, viewBox: `0 0 ${W} ${H}` }).forEach(([k, v]) => svg.setAttribute(k, v));
    const defs = svgEl('defs');
    svg.appendChild(defs);
    for (const [k, c] of Object.entries(COLORS)) {
      const m = svgEl('marker', { id: 'ah-' + k, viewBox: '0 0 10 10', refX: '9', refY: '5', markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse' });
      m.appendChild(svgEl('path', { d: 'M0,0 L10,5 L0,10 z', fill: c }));
      defs.appendChild(m);
    }
    const labels = svgEl('g');
    for (const E of D.edges || []) {
      const A = document.getElementById(E.from), B = document.getElementById(E.to);
      if (!A || !B) { console.error('missing node', E.from, E.to); continue; }
      const ra = rectOf(A), rb = rectOf(B);
      const [fs, ts] = E.sides || autoSides(ra, rb);
      const s = anchor(ra, fs, E.fromT);
      let e = anchor(rb, ts, E.toT);
      if (E.align === 'y') e = [e[0], Math.min(Math.max(s[1], rb.y + 10), rb.y + rb.h - 10)];
      if (E.align === 'x') e = [Math.min(Math.max(s[0], rb.x + 10), rb.x + rb.w - 10), e[1]];
      const pts = route(E, s, e, fs);
      const key = E.color || 'muted';
      const p = svgEl('path', {
        d: pathD(pts, 10), fill: 'none', stroke: COLORS[key], 'stroke-width': E.width || 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round'
      });
      if (!E.noArrow) p.setAttribute('marker-end', `url(#ah-${key})`);
      if (E.dashed) p.setAttribute('stroke-dasharray', '7 6');
      svg.appendChild(p);
      if (E.label) {
        const [lx, ly] = E.labelAt || longestMid(pts, E.labelFrac);
        const g = svgEl('g');
        const lines = String(E.label).split('\n');
        const t = svgEl('text', { x: lx, y: ly - (lines.length - 1) * 9, 'text-anchor': 'middle', 'dominant-baseline': 'middle', class: 'wire-label', fill: COLORS[key] });
        lines.forEach((ln, i) => {
          const ts2 = svgEl('tspan', { x: lx, dy: i === 0 ? 0 : 18 });
          ts2.textContent = ln;
          t.appendChild(ts2);
        });
        g.appendChild(t);
        labels.appendChild(g);
      }
    }
    svg.appendChild(labels);
    for (const g of labels.querySelectorAll('g')) {
      const t = g.querySelector('text');
      const b = t.getBBox();
      g.insertBefore(svgEl('rect', { x: b.x - 7, y: b.y - 3, width: b.width + 14, height: b.height + 6, rx: 6, fill: '#ffffff', stroke: '#d6dee2' }), t);
    }
    window.__done = true;
  };
})();
