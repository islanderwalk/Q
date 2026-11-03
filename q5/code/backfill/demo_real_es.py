#!/usr/bin/env python3
"""Q5 回補送出端：對官方 Elasticsearch 跑 backfill.py --send，painless 腳本（GUARD_SCRIPT）實際執行。

demo_send.py 連的是 Python 假 ES（tests/fake_es.py），腳本邏輯是 Python 模擬；這支改連官方 ES。
只連命令列給的 ES 位址（例如本機 Docker 起的單節點），不連外網。線上文件的三種情況與 demo_send.py 相同
（seed()）：沒有計數、爬蟲已寫過三個計數（較新）、爬蟲只寫過轉發數（較新）。

用法：建議直接用 demo_real_es.sh（起容器時帶好下面這些參數，結束時刪掉容器）。手動跑的話（在 q5/code/backfill/ 底下）：
    docker run -d --name q5es -p 127.0.0.1:19200:9200 -e discovery.type=single-node \\
        -e xpack.security.enabled=false -e xpack.ml.enabled=false \\
        -e cluster.routing.allocation.disk.threshold_enabled=false -e ES_JAVA_OPTS="-Xms1g -Xmx1g" elasticsearch:8.19.23
    python make_sample.py --out <RUN>/sample --posts 2000 --reels 200 --seed 5
    python demo_real_es.py <RUN> http://127.0.0.1:19200
情境：
  R0 舊 mapping（dynamic: strict，只有 post_id）放入線上文件；先送 1 筆回補，應被 mapping 擋下；
     再用 update mapping API 加新欄位（不 reindex），寫入爬蟲較新的計數
  R1 首次送出；R2 同目錄重跑（帳本跳過）；R3 新目錄整批重送（應全部 noop）
  R4 時間戳寫錯：ISO 字串被 epoch_millis mapping 擋在寫入時；數字字串寫得進去，但腳本丟錯（400、退出碼 4）
  R5 IG：video_view_count 回補到 ig-posts
R1、R3 後用 _mget 讀回全部 Threads 文件：計數值和 make_sample.py 的 expected.json 比對；expected.json 沒有時間戳，
counts_at 和 bulk 檔的 params.at 比對（爬蟲較新的欄位則是爬蟲寫的時間）。R4 只讀回被改壞的那篇；R5 比對 IG 的值，
counts_at 只看有沒有。
退出碼：0 全部符合預期；1 有不符。
"""
import json
import os
from collections import Counter
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from demo_send import CRAWLER, FAR, seed, want_after  # noqa: E402

BF = str(HERE / "backfill.py")
INDEX, IG_INDEX = "threads-posts", "ig-posts"
FIELDS = ("quote_count", "repost_count", "reshare_count")
LONGS = {f: {"type": "long"} for f in FIELDS}
STAMPS = {f: {"type": "date", "format": "epoch_millis"} for f in FIELDS}
NDJSON = {"Content-Type": "application/x-ndjson"}


class ES:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.s = requests.Session()

    def call(self, method, path, body=None, ndjson=None):
        kw = {"data": ndjson.encode("utf-8"), "headers": NDJSON} if ndjson is not None else {"json": body}
        r = self.s.request(method, f"{self.url}{path}", timeout=60, **kw)
        return r.status_code, r.json()

    def bulk(self, lines):
        status, resp = self.call("POST", "/_bulk?refresh=wait_for", ndjson="".join(lines))
        assert status == 200, resp
        return resp

    def mget(self, index, ids):
        docs = {}
        for i in range(0, len(ids), 500):
            status, resp = self.call("POST", f"/{index}/_mget", {"ids": ids[i:i + 500]})
            assert status == 200, resp
            docs.update({d["_id"]: d.get("_source") if d["found"] else None for d in resp["docs"]})
        return docs


def backfill(args, label):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    t = time.time()
    p = subprocess.run([sys.executable, BF, *args], capture_output=True, text=True, env=env)
    send = [line for line in p.stdout.splitlines() if line.startswith("send=")]
    print(f"[{label}] exit={p.returncode} elapsed={time.time() - t:.2f}s")
    first = p.stdout.splitlines()[0].split(" quality=")[0] if p.stdout.strip() else p.stderr.strip()[-400:]
    print("   ", send[0] if send else first)
    return p.returncode, (json.loads(send[0][5:].replace("'", '"')) if send else {})


def check_threads(es, expected, groups, stamps):
    """回傳不符的篇數。stamps：{post_id: {欄位: 回補帶的 crawled_at}}（從 bulk 檔讀出）。"""
    docs, bad = es.mget(INDEX, sorted(expected)), []
    for c, v in expected.items():
        doc = docs[c]
        if c in groups["missing"]:
            if doc is not None:
                bad.append((c, "missing doc was created"))
            continue
        want = want_after(c, v, groups)
        if doc.get("text_post_app_info") != want:
            bad.append((c, doc.get("text_post_app_info"), want))
            continue
        at = doc.get("counts_at") or {}
        if c in groups["newer"]:
            want_at = dict.fromkeys(CRAWLER, FAR)
        elif c in groups["partial"]:
            want_at = {**stamps.get(c, {}), "repost_count": FAR}
        else:
            want_at = stamps.get(c, {})
        if at != want_at:
            bad.append((c, at, want_at))
    return bad


def error_counts(out: Path) -> dict:
    """send_errors.jsonl 依「status error」計數；不在 ES 的文件是 404 document_missing_exception。"""
    f = out / "send_errors.jsonl"
    if not f.exists():
        return {}
    return dict(Counter(f"{e['status']} {e['error']}" for e in map(json.loads, f.read_text(encoding="utf-8").splitlines())))


def bulk_stamps(out: Path):
    stamps = {}
    for f in sorted(out.glob("bulk_*.ndjson")):
        lines = f.read_text(encoding="utf-8").splitlines()
        for i in range(0, len(lines), 2):
            stamps[json.loads(lines[i])["update"]["_id"]] = json.loads(lines[i + 1])["script"]["params"]["at"]
    return stamps


def main(argv=None):
    argv = argv or sys.argv[1:]
    run_dir, es = Path(argv[0]), ES(argv[1])
    src = run_dir / "sample" / "src"
    exp_all = json.loads((run_dir / "sample" / "expected.json").read_text(encoding="utf-8"))
    expected = exp_all["threads"]
    _, info = es.call("GET", "/")
    print(f"ES {info['version']['number']} (build {info['version']['build_hash'][:12]}, lucene "
          f"{info['version']['lucene_version']}) at {es.url}")
    for idx in (INDEX, IG_INDEX):
        es.call("DELETE", f"/{idx}")
    for old in run_dir.glob("real_es_*"):  # 帳本 sent.json 也在裡面，不清掉 R1 會被當成已送
        shutil.rmtree(old)
    ok = True

    def expect(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        print(f"    {'OK ' if cond else 'BAD'} {msg}")

    # R0：舊 mapping（strict）→ 放線上文件 → 送 1 筆應被擋 → update mapping → 爬蟲較新的計數
    print("\n[R0] 舊 mapping（dynamic: strict，只有 post_id）")
    status, resp = es.call("PUT", f"/{INDEX}", {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
                                                "mappings": {"dynamic": "strict",
                                                             "properties": {"post_id": {"type": "keyword"}}}})
    expect(status == 200 and resp.get("acknowledged"), f"create {INDEX}: HTTP {status}")
    docs, groups = seed(expected)
    base = [json.dumps({"index": {"_index": INDEX, "_id": c}}) + "\n" + json.dumps({"post_id": c}) + "\n"
            for (_, c) in sorted(docs)]
    resp = es.bulk(base)
    expect(not resp["errors"], f"seeded base docs={len(base)} not_in_es={len(groups['missing'])}")

    pre = run_dir / "real_es_r0"
    code, _ = backfill(["--src", str(src), "--out", str(pre)], "R0 產生 bulk 檔（不送）")
    lines = (pre / "bulk_00001.ndjson").read_text(encoding="utf-8").splitlines(keepends=True)
    k = next(i for i in range(0, len(lines), 2) if json.loads(lines[i])["update"]["_id"] not in groups["missing"])
    first_id = json.loads(lines[k])["update"]["_id"]  # 挑一篇 ES 裡有的
    resp = es.bulk(lines[k:k + 2])
    item = resp["items"][0]["update"]
    expect(item["status"] == 400 and item["error"]["type"] == "strict_dynamic_mapping_exception",
           f"mapping 還沒加新欄位時送 1 筆：status {item['status']} {item['error']['type']}")
    expect(es.mget(INDEX, [first_id])[first_id] == {"post_id": first_id}, f"{first_id} 沒有被改動")

    status, resp = es.call("PUT", f"/{INDEX}/_mapping", {"properties": {
        "text_post_app_info": {"properties": LONGS}, "counts_at": {"properties": STAMPS}}})
    expect(status == 200 and resp.get("acknowledged"), f"update mapping API 加新欄位（不 reindex）：HTTP {status}")
    _, mp = es.call("GET", f"/{INDEX}/_mapping")
    props = mp[INDEX]["mappings"]["properties"]
    print("    mapping counts_at =", json.dumps(props["counts_at"]["properties"], sort_keys=True))
    crawler = []
    for c in sorted(groups["newer"] | groups["partial"]):
        src_doc = docs[(INDEX, c)]["_source"]
        crawler.append(json.dumps({"update": {"_index": INDEX, "_id": c}}) + "\n"
                       + json.dumps({"doc": {k: src_doc[k] for k in ("text_post_app_info", "counts_at")}}) + "\n")
    resp = es.bulk(crawler)
    expect(not resp["errors"], f"爬蟲較新的計數：三欄 {len(groups['newer'])} 篇、只有轉發數 {len(groups['partial'])} 篇")

    send = ["--send", es.url, "--rate-per-sec", "4"]
    print()
    code, st = backfill(["--src", str(src), "--out", str(run_dir / "real_es_s1"), *send], "R1 首次送出")
    stamps = bulk_stamps(run_dir / "real_es_s1")
    expect(code == 0, "退出碼 0")
    expect(st.get("not_in_index") == len(groups["missing"] & set(stamps)),
           f"not_in_index={st.get('not_in_index')}（ES 沒有、回補有值的篇數）")
    errs1 = error_counts(run_dir / "real_es_s1")
    expect(errs1 == {"404 document_missing_exception": st.get("not_in_index")},
           f"send_errors.jsonl 只有不在 ES 的那幾篇：{errs1}")
    bad = check_threads(es, expected, groups, stamps)
    expect(not bad, f"_mget 比對（值對 expected.json，counts_at 對 bulk 檔與爬蟲的時間戳）：不符 {len(bad)} 篇 {bad[:3]}")

    code, st = backfill(["--src", str(src), "--out", str(run_dir / "real_es_s1"), *send], "R2 同目錄重跑（帳本跳過）")
    expect(code == 0 and st.get("requests", 0) == 0, f"沒有送任何請求，跳過 {st.get('files_skipped_already_sent')} 檔")

    code, st = backfill(["--src", str(src), "--out", str(run_dir / "real_es_s3"), *send], "R3 新目錄整批重送")
    expect(code == 0 and not st.get("ok") and st.get("noop") == len(set(stamps) - groups["missing"]),
           f"ok={st.get('ok', 0)} noop={st.get('noop')}（應全部 noop）")
    bad = check_threads(es, expected, groups, stamps)
    expect(not bad, f"重送後比對：不符 {len(bad)} 篇")

    # R4：時間戳型別錯誤
    print("\n[R4] 線上 counts_at 寫錯型別")
    victim = next(c for c in sorted(stamps) if c not in groups["missing"] and "repost_count" in stamps[c]
                  and c not in groups["newer"] | groups["partial"])
    status, resp = es.call("POST", f"/{INDEX}/_update/{victim}", {"doc": {"counts_at": {"repost_count": "2026-05-01T00:00:00Z"}}})
    expect(status == 400, f"ISO 字串寫進 epoch_millis 欄位：HTTP {status} {resp.get('error', {}).get('type')}（寫入時就擋下）")
    status, resp = es.call("POST", f"/{INDEX}/_update/{victim}?refresh=wait_for",
                           {"doc": {"counts_at": {"repost_count": str(FAR)}}})
    expect(status == 200, f"數字字串 \"{FAR}\" 寫得進去：HTTP {status}")
    before = es.mget(INDEX, [victim])[victim]
    expect(isinstance(before["counts_at"]["repost_count"], str),
           f"_source 的 counts_at.repost_count 是字串：{before['counts_at']['repost_count']!r}")
    code, st = backfill(["--src", str(src), "--out", str(run_dir / "real_es_s4"), *send], "R4 整批重送")
    all_errs = [json.loads(x) for x in (run_dir / "real_es_s4" / "send_errors.jsonl").read_text(encoding="utf-8").splitlines()]
    errs = [e for e in all_errs if e["status"] != 404]
    expect(code == 4 and st.get("failed") == 1, f"退出碼 {code}、failed={st.get('failed')}")
    expect([(e["_id"], e["status"]) for e in errs] == [(victim, 400)],
           f"send_errors.jsonl：404（不在 ES）{len(all_errs) - len(errs)} 筆，其餘 {errs}")
    expect(es.mget(INDEX, [victim])[victim] == before, f"{victim} 沒有被改動")
    script = next(json.loads(lines[i + 1]) for f in sorted((run_dir / "real_es_s4").glob("bulk_*.ndjson"))
                  for lines in [f.read_text(encoding="utf-8").splitlines()]
                  for i in range(0, len(lines), 2) if json.loads(lines[i])["update"]["_id"] == victim)
    status, resp = es.call("POST", f"/{INDEX}/_update/{victim}", script)  # 同一份腳本單篇送，看完整錯誤
    err = resp.get("error", {})
    cause = err.get("caused_by") or {}
    print(f"    單篇送同一份腳本：HTTP {status} {err.get('type')} ← {cause.get('type')}: "
          f"{(cause.get('caused_by') or {}).get('reason') or cause.get('reason')}")

    # R5：IG
    print("\n[R5] IG：video_view_count 回補到", IG_INDEX)
    ig = exp_all["instagram"]
    status, _ = es.call("PUT", f"/{IG_INDEX}", {"settings": {"number_of_shards": 1, "number_of_replicas": 0},
                                                "mappings": {"dynamic": "strict", "properties": {
                                                    "post_id": {"type": "keyword"}, "video_view_count": {"type": "long"},
                                                    "counts_at": {"properties": {"video_view_count": STAMPS["repost_count"]}}}}})
    ig_missing = {c for c in ig if int(c[-6:]) % 100 == 0}
    resp = es.bulk([json.dumps({"index": {"_index": IG_INDEX, "_id": c}}) + "\n" + json.dumps({"post_id": c}) + "\n"
                    for c in sorted(ig) if c not in ig_missing])
    expect(status == 200 and not resp["errors"], f"create {IG_INDEX}、放 {len(ig) - len(ig_missing)} 篇（{len(ig_missing)} 篇不在 ES）")
    code, st = backfill(["--src", str(src / "instagram"), "--out", str(run_dir / "real_es_ig"), "--platform", "instagram",
                         *send], "R5 IG 送出")
    got = es.mget(IG_INDEX, sorted(ig))
    bad = [c for c, v in ig.items() if c not in ig_missing
           and (got[c].get("video_view_count") != v.get("video_view_count") or ("counts_at" in got[c]) != bool(v))]
    bad += [c for c in ig_missing if got[c] is not None]
    expect(code == 0 and not bad, f"退出碼 {code}；值比對 expected.json、counts_at 只看有沒有：不符 {len(bad)} 篇 {bad[:3]}")

    print("\nALL OK" if ok else "\nSOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
