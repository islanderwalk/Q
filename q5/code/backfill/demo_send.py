#!/usr/bin/env python3
"""Q5 回補送出端示範：本機 127.0.0.1 起一個假 ES（tests/fake_es.py），用 HTTP 跑 backfill.py --send。

不是真的 Elasticsearch：painless 腳本沒有執行，guard 邏輯是 Python 模擬。只連本機，不連外網。
線上 index 預先放三種文件：沒有計數、爬蟲已寫過三個計數（較新）、爬蟲只寫過轉發數（較新），
最後一種用來看逐欄時間戳：回補要補上另外兩欄，但不能動轉發數。

用法：
    python make_sample.py --out <RUN>/sample
    python demo_send.py <RUN>
情境：首次送出（含 HTTP 429 與單筆 429）、同目錄重跑、新目錄整批重送、第 3 個請求 500 中斷後續跑。
每個情境印出 backfill.py 的退出碼與 send 統計，並和 make_sample.py 的 expected.json 比對 ES 內容。
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tests"))

from fake_es import FakeES  # noqa: E402

BF = str(HERE / "backfill.py")
INDEX = "threads-posts"
FAR = 1_900_000_000_000  # 線上爬蟲較新的寫入時間（毫秒）
CRAWLER = {"repost_count": 999, "quote_count": 99, "reshare_count": None}  # 線上爬蟲寫過的值


class FailingES(FakeES):
    fail_on = None  # 第幾個請求回 HTTP 500（模擬中斷）

    def bulk(self, payload):
        if self.fail_on is not None and self.requests + 1 == self.fail_on:
            self.requests += 1
            return 500, {"error": {"type": "simulated_outage"}}
        return super().bulk(payload)


def seed(expected):
    """線上 index：1% 的貼文不在 ES；1% 已被爬蟲寫過三個計數（都比回補新）；1% 爬蟲只寫過轉發數（比回補新）；
    其餘有文件但沒有計數。回傳 (docs, groups)。"""
    codes = sorted(expected)
    groups = {"missing": {c for c in codes if int(c[-6:]) % 100 == 0},
              "newer": {c for c in codes if int(c[-6:]) % 100 == 1},
              "partial": {c for c in codes if int(c[-6:]) % 100 == 2}}
    docs = {(INDEX, c): {"_source": {"post_id": c}, "_version": 1} for c in codes if c not in groups["missing"]}
    for c in groups["newer"]:
        docs[(INDEX, c)]["_source"].update(text_post_app_info=dict(CRAWLER), counts_at=dict.fromkeys(CRAWLER, FAR))
    for c in groups["partial"]:
        docs[(INDEX, c)]["_source"].update(text_post_app_info={"repost_count": 999}, counts_at={"repost_count": FAR})
    return docs, groups


def want_after(code, values, groups):
    """回補送完之後，線上這篇的 text_post_app_info 應該是什麼（None 表示沒有這個物件）。"""
    if code in groups["newer"]:
        return CRAWLER  # 三欄線上都比較新：全部不動
    if code in groups["partial"]:
        return {**values, "repost_count": 999}  # 只有轉發數線上比較新；另外兩欄照回補寫入
    return values or None


def mismatches(es, expected, groups):
    bad = 0
    for c, v in expected.items():
        doc = es.docs.get((INDEX, c))
        if c in groups["missing"]:
            bad += doc is not None
            continue
        bad += doc["_source"].get("text_post_app_info") != want_after(c, v, groups)
    return bad


def main(argv=None):
    run_dir = Path((argv or sys.argv[1:])[0])
    src = run_dir / "sample" / "src"
    expected = json.loads((run_dir / "sample" / "expected.json").read_text(encoding="utf-8"))["threads"]
    state = {"es": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            status, resp = state["es"].bulk(body) if self.path == "/_bulk" else (404, {})
            data = json.dumps(resp).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    def run(name, out):
        t = time.time()
        p = subprocess.run([sys.executable, BF, "--src", str(src), "--out", str(run_dir / out),
                            "--send", url, "--rate-per-sec", "4"], capture_output=True, text=True, env=env)
        send = [line for line in p.stdout.splitlines() if line.startswith("send=")]
        print(f"[{name}] exit={p.returncode} elapsed={time.time() - t:.2f}s requests_seen_by_es={state['es'].requests}")
        print("   ", send[0] if send else p.stderr.strip()[-300:])

    docs, groups = seed(expected)
    es = state["es"] = FailingES(docs, reject_items_once={c for c in sorted(expected) if int(c[-6:]) % 250 == 3},
                                 http_429_times=1)
    print(f"seeded docs={len(docs)} not_in_es={len(groups['missing'])} online_newer_all={len(groups['newer'])} "
          f"online_newer_repost_only={len(groups['partial'])} item_429_once={len(es.reject_items_once)} "
          f"http_429_first_requests=1")
    run("S1 首次送出", "send1")
    print("    mismatches vs expected:", mismatches(es, expected, groups))
    run("S2 同目錄重跑（帳本跳過）", "send1")
    run("S3 新目錄整批重送（應全部 noop）", "send2")
    print("    mismatches vs expected:", mismatches(es, expected, groups))

    docs, groups = seed(expected)
    es = state["es"] = FailingES(docs)
    es.fail_on = 3
    print(f"\nreseeded docs={len(docs)}; HTTP 500 on request #3")
    run("S4 中斷（第 3 個請求 500）", "send3")
    ledger = json.loads((run_dir / "send3" / "sent.json").read_text(encoding="utf-8"))
    print("    ledger files:", sorted(ledger[url].values()))
    es.fail_on = None
    run("S5 修好後重跑（只送沒送完的檔）", "send3")
    print("    mismatches vs expected:", mismatches(es, expected, groups))
    srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
