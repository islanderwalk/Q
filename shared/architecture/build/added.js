  // --- added tabs: Q5 data flow, evidence filters, deploy links ---
  const Q5N = __Q5N__;
  const q5d = $("#pane-q5flow .q5d");
  function q5show(id) {
    $$("#pane-q5flow [data-q5]").forEach(g => g.setAttribute("aria-pressed", g.dataset.q5 === id ? "true" : "false"));
    $$("#pane-q5flow .e[data-from]").forEach(g => g.classList.toggle("on", !!id && (g.dataset.from === id || g.dataset.to === id)));
    q5d.replaceChildren();
    if (!id) {
      const c = { mod: 0, new: 0, keep: 0 };
      $$("#pane-q5flow [data-q5]").forEach(g => { if (g.classList.contains("mod")) c.mod++; else if (g.classList.contains("new")) c.new++; else if (!g.classList.contains("ext")) c.keep++; });
      q5d.append(el("h3", null, "點一個方框看細節"),
        el("p", "p-sum", "要修改 " + c.mod + " 個、新增 " + c.new + " 個、不改 " + c.keep + " 個。每個方框寫了改什麼、誰負責、要幾小時，以及 Playwright 實抓的佐證。"));
      const ul = el("ul", "lanes");
      [["var(--dg-band)", "既有系統，不改"], ["color-mix(in srgb, var(--tk-k) 22%, var(--bg))", "資料工程師"], ["color-mix(in srgb, var(--tk-n) 22%, var(--bg))", "後端"], ["color-mix(in srgb, var(--dg-fe) 22%, var(--bg))", "前端"]].forEach(([c, t]) => {
        const li = el("li"); const i = el("i"); i.style.background = c; li.append(i, document.createTextNode(t)); ul.append(li);
      });
      q5d.append(ul);
      return;
    }
    const n = Q5N[id];
    const head = el("div", "p-head"); const h = el("div");
    h.append(el("h2", null, n.label), el("p", "p-sub", n.who + "　｜　" + n.act));
    head.append(h);
    const back = el("button", "back", "← 全部"); back.type = "button"; back.dataset.q5back = "";
    q5d.append(back, head, el("p", "p-sum", n.what));
    n.ev.forEach(ev => {
      const f = el("figure", "q-ev");
      const a = el("a"); a.href = ev.img; a.target = "_blank"; a.rel = "noopener";
      const i = el("img"); i.src = ev.img; i.alt = ev.cap; i.loading = "lazy";
      a.append(i); f.append(a, el("figcaption", null, ev.cap)); q5d.append(f);
    });
  }
  const q5fig = $("#pane-q5flow .dg");
  if (q5fig && q5d) {
    const pick = e => {
      const g = e.target.closest("[data-q5]");
      if (!g) return;
      if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      q5show(g.getAttribute("aria-pressed") === "true" ? null : g.dataset.q5);
    };
    q5fig.addEventListener("click", pick);
    q5fig.addEventListener("keydown", pick);
    q5d.addEventListener("click", e => { if (e.target.closest("[data-q5back]")) q5show(null); });
    q5show(null);
  }

  const evState = { pf: "all", q: "all" };
  function evFilter(pf, q) {
    if (pf) evState.pf = pf;
    if (q) evState.q = q;
    $$("#pane-evidence .fbar [data-f]").forEach(b => b.setAttribute("aria-pressed", b.dataset.f === evState.pf ? "true" : "false"));
    $$("#pane-evidence .fbar [data-fq]").forEach(b => b.setAttribute("aria-pressed", b.dataset.fq === evState.q ? "true" : "false"));
    let shown = 0;
    $$("#pane-evidence .ev").forEach(f => {
      const on = (evState.pf === "all" || f.dataset.pf === evState.pf) && (evState.q === "all" || f.dataset.q === evState.q);
      f.hidden = !on;
      if (on) shown++;
    });
    const c = $("#pane-evidence .cnt");
    if (c) c.textContent = shown ? "顯示 " + shown + " 張" : "這個組合沒有圖，換一個條件看看";
  }
  const evPane = $("#pane-evidence");
  if (evPane) {
    evPane.addEventListener("click", e => {
      const b = e.target.closest(".fbar button");
      if (!b) return;
      if (b.dataset.f) evFilter(b.dataset.f, null); else evFilter(null, b.dataset.fq);
    });
    evFilter("all", "all");
  }
  const depPane = $("#pane-deploy");
  if (depPane) depPane.addEventListener("click", e => {
    const b = e.target.closest("[data-goto-node]");
    if (b) go({ tab: "shape", v: "int", node: b.dataset.gotoNode, q: null, stage: null, diff: false });
  });
