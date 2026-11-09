// Evidence images open in an in-page viewer. Inside the published artifact the page runs in a
// sandboxed frame, and a new tab on a relative image URL lands on claude.ai, which refuses to be framed.
(function () {
  const IMG = /\.(png|jpe?g|gif|webp|svg)(\?.*)?$/i;
  let box = null, last = null;
  function close() {
    if (!box || box.hidden) return;
    box.hidden = true; document.body.classList.remove("lb-open");
    if (last) last.focus();
  }
  function build() {
    box = document.createElement("div");
    box.className = "lb"; box.hidden = true;
    box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-label", "圖片放大檢視");
    box.innerHTML = '<div class="lb-bar"><span class="lb-cap"></span><button type="button" class="lb-x" aria-label="關閉">✕</button></div>' +
                    '<div class="lb-scroll"><img alt=""></div>';
    box.addEventListener("click", (e) => { if (e.target === box || e.target.classList.contains("lb-scroll") || e.target.closest(".lb-x")) close(); });
    document.body.append(box);
  }
  function open(a) {
    if (!box) build();
    const fig = a.closest("figure"), cap = fig && fig.querySelector("figcaption");
    const im = a.querySelector("img");
    const text = cap ? Array.from(cap.childNodes, (n) => n.textContent.trim()).filter(Boolean).join(" · ")
                     : (im && im.alt) || a.getAttribute("title") || a.textContent.trim();
    box.querySelector(".lb-cap").textContent = text;
    const big = box.querySelector(".lb-scroll img");
    big.src = a.getAttribute("href"); big.alt = text;
    box.querySelector(".lb-scroll").scrollTop = 0;
    last = a; box.hidden = false; document.body.classList.add("lb-open");
    box.querySelector(".lb-x").focus();
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || !IMG.test(a.getAttribute("href") || "")) return;
    e.preventDefault(); open(a);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
})();
