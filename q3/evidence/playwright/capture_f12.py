"""用 DevTools Network 面板截圖（headless，不開視窗）。

做法（全部走 Chromium 的 remote debugging 協定，不掛 Playwright 在目標分頁上）：
  1. headless Chromium 開 --remote-debugging-port，建一個 PTT 用的目標分頁。
  2. 再建一個分頁，載入 Chromium 自帶的 DevTools 前端（inspector.html，就是 F12 同一套 UI），
     以 ws= 連到該目標分頁，panel=network，所以 Network 面板真的在記錄請求。
  3. 用 CDP 的 Page.navigate 逐篇載入 PTT；DevTools 面板自己收到實際請求。
  4. 用 Page.captureScreenshot 截 DevTools 分頁。紅框是用 JS 疊在「DevTools 分頁」上畫的，
     框住的是 Network 面板裡實際的那一列（或底部的請求總數）。PTT 頁面本身不被改動。

    python q3/evidence/playwright/capture_f12.py

輸出：
    f12_1_requests.png   正常文章：Network 面板底部的請求總數（紅框）
    f12_2_truncated.png  截斷文章：文件請求那一列（紅框）＋ Preview 分頁
    f12_3_deleted.png    已刪文章：文件請求那一列，回 404（紅框）
    f12.json             每張圖的請求總數文字、紅框框到的列文字、是否出現「連線中斷」對話框（從 DOM 讀）

PTT 只各打 1 次，間隔 ≥ 1.5 秒。
"""
import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websocket
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
PORT = 9333
W, H = 1600, 820
GAP = 1.5
CASES = [
    ("1_requests", "https://www.ptt.cc/bbs/Gossiping/M.1792946240.A.772.html"),
    ("2_truncated", "https://www.ptt.cc/bbs/Gossiping/M.1792462201.A.2BD.html"),
    ("3_deleted", "https://www.ptt.cc/bbs/Gossiping/M.1792887225.A.5F3.html"),
]

# 注入到 DevTools 分頁的共用 JS：走訪整棵 DOM（含 shadow root）取得葉節點
PRE = r"""
function leaves(){
  const out = [];
  (function walk(n){
    if (n.nodeType === 1 && n.childElementCount === 0) out.push(n);
    for (const c of (n.children || [])) walk(c);
    if (n.shadowRoot) walk(n.shadowRoot);
  })(document);
  return out;
}
function rowOf(el){ return el.closest('[role="row"], tr, .data-grid-data-grid-node') || el; }
function txtOf(x){ return (x.textContent || '') + ' ' + (x.getAttribute ? (x.getAttribute('title') || '') : ''); }
// 只在 Network 表格的列裡找（排除視窗標題 "DevTools - ..."）；找不到完整檔名時退回前綴
// 注意：Initiator 欄也會出現文章檔名，所以只比對「每列的第一欄（Name）」開頭
function pickRow(N, P){
  const rows = [];
  leaves().forEach(x => {
    const r = x.closest('.data-grid-data-grid-node, [role="row"], tr');
    if (r && !rows.includes(r)) rows.push(r);
  });
  const key = P || N;
  // 錯誤列（404）的狀態圖示會留下不可見字元加 "role=img" 在最前面，先去掉
  // 檔名在文件列的最前面（前 30 字內）；其他列的 Initiator 欄要在約第 50 字之後才出現
  const head = r => (r.textContent || '').replace(/[​-‍﻿]/g, '').slice(0, 30);
  return rows.find(r => head(r).includes(key)) || null;
}
"""

JS_DIALOG = PRE + r"""
leaves().some(e => (e.textContent || '').includes('Debugging connection was closed'))
"""

JS_SUMMARY = PRE + r"""
(() => {
  const hit = leaves().map(e => (e.textContent || '').trim())
    .find(t => /^\d[\d,]*\s+requests?\b/.test(t) && t.length < 120);
  return hit || null;
})()
"""

JS_CLICK = PRE + r"""
(() => {
  const N = __N__;
  const e = N === 'Preview'
    ? leaves().find(x => (x.textContent || '').trim() === 'Preview')
    : pickRow(N, __P__);
  if (!e) return false;
  (__ASROW__ ? rowOf(e) : e).click();
  return true;
})()
"""

JS_BOX = PRE + r"""
(() => {
  document.querySelectorAll('.qa-f12').forEach(x => x.remove());
  const N = __N__;
  const e = __PICK__;
  if (!e) return null;
  const el = __ASROW__ ? rowOf(e) : e;
  const r = el.getBoundingClientRect();
  const d = document.createElement('div');
  d.className = 'qa-f12';
  Object.assign(d.style, {
    position: 'fixed', zIndex: 2147483647, pointerEvents: 'none',
    left: (r.left - 4) + 'px', top: (r.top - 4) + 'px',
    width: (r.width + 8) + 'px', height: (r.height + 8) + 'px',
    border: '3px solid #ff3b30', borderRadius: '4px', boxSizing: 'border-box',
    boxShadow: '0 0 0 2px rgba(255,59,48,.3)'
  });
  document.body.appendChild(d);
  return { text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 240),
           x: r.left, y: r.top, w: r.width, h: r.height };
})()
"""


def http(method, path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read() or b"null")


class CDP:
    """最小的 CDP 客戶端：送指令、等同一個 id 的回應，其餘事件略過。"""

    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return r.get("result", {}).get("value")

    def close(self):
        self.ws.close()


def wait_port():
    for _ in range(60):
        try:
            http("GET", "/json/version")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("headless Chromium 沒有開起 remote debugging port")


def main():
    with sync_playwright() as p:
        exe = p.chromium.executable_path
    prof = tempfile.mkdtemp(prefix="f12prof_")
    proc = subprocess.Popen(
        [exe, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
         "--no-first-run", "--no-default-browser-check", "--remote-allow-origins=*",
         f"--window-size={W},{H + 80}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = []
    try:
        wait_port()
        page = http("PUT", "/json/new?about:blank")
        pid = page["id"]
        insp = (f"http://127.0.0.1:{PORT}/devtools/inspector.html"
                f"?ws=127.0.0.1:{PORT}/devtools/page/{pid}&panel=network")
        dtt = http("PUT", "/json/new?" + urllib.parse.quote(insp, safe=""))

        pc = CDP(page["webSocketDebuggerUrl"])
        pc.call("Network.enable")
        pc.call("Network.setCookie", {"name": "over18", "value": "1",
                                       "domain": ".ptt.cc", "path": "/"})
        dc = CDP(dtt["webSocketDebuggerUrl"])
        dc.call("Emulation.setDeviceMetricsOverride",
                {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(8)  # DevTools 前端載入需要時間

        last = 0.0
        for name, url in CASES:
            wait = last + GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            pc.call("Page.navigate", {"url": url})
            last = time.monotonic()
            needle = url.rsplit("/", 1)[1]
            time.sleep(5)  # 讓 Network 面板收齊這一頁的請求

            dialog = bool(dc.eval(JS_DIALOG))
            summary = dc.eval(JS_SUMMARY)
            prefix = needle[:9]  # 表格裡檔名會被截斷成「M.1792887…」，用前 9 字比對（列表中只有一列以 M. 開頭）
            if name == "2_truncated":
                dc.eval(JS_CLICK.replace("__N__", json.dumps(needle))
                        .replace("__P__", json.dumps(prefix)).replace("__ASROW__", "true"))
                time.sleep(1)
                dc.eval(JS_CLICK.replace("__N__", json.dumps("Preview"))
                        .replace("__P__", "null").replace("__ASROW__", "false"))
                time.sleep(1.5)

            if name == "1_requests" and summary:
                box = dc.eval(JS_BOX.replace("__N__", json.dumps(summary))
                              .replace("__PICK__", "leaves().find(x => (x.textContent || '').trim() === N)")
                              .replace("__ASROW__", "false"))
            else:
                box = dc.eval(JS_BOX.replace("__N__", json.dumps(needle))
                              .replace("__PICK__", f"pickRow(N, {json.dumps(prefix)})")
                              .replace("__ASROW__", "true"))

            shot = dc.call("Page.captureScreenshot", {
                "format": "png",
                "clip": {"x": 0, "y": 0, "width": W, "height": H, "scale": 1}})
            path = HERE / f"f12_{name}.png"
            path.write_bytes(base64.b64decode(shot["data"]))
            results.append({"file": path.name, "url": url, "dialog_disconnected": dialog,
                            "summary": summary, "boxed": box is not None,
                            "boxed_text": (box or {}).get("text")})
            print(f"{name}: dialog={dialog} summary={summary!r} boxed={box is not None}",
                  file=sys.stderr)
        dc.close()
        pc.close()
    finally:
        proc.terminate()
    (HERE / "f12.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(json.dumps(results, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
