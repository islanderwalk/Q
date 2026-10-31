#!/usr/bin/env python3
"""demo_cli.sh 用的小工具：比對、製造壞資料、對舊版程式重現問題。不連網。

子命令：
  compare <out> <expected.json> [--platform threads]   update bulk 的值對 make_sample 的正確答案逐篇比對
  ids <out>...                                          每個輸出目錄的 doc id 數、不重複數、清單雜湊
  truncate-lines <file.jsonl> [--every 10]              每 N 行把 1 行截成一半（壞行）
  rename-keys <file> old=new ...                        把 post 兩層內的 key 改名（模擬別名表沒有的舊名）
  item429 --code <backfill 目錄> --fake-es <fake_es.py> --work <目錄>   單筆 429 時送出端怎麼處理
  guard-mixed --code <backfill 目錄> --fake-es <fake_es.py> --work <目錄>
      線上 T2 已有較新的轉發數，回補輸入只有 T1（三欄）與 T3（缺轉發 key）：看較舊的值會不會蓋掉較新的值

item429、guard-mixed 從 --code 指定的目錄載入 backfill.py，可以對 git 取出的舊版跑；每次只載入一個版本。
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path


def bulk_values(out: Path):
    """update 寫法的 bulk 檔：{_id: params.values}。"""
    got = {}
    for f in sorted(out.glob("bulk_*.ndjson")):
        lines = f.read_text(encoding="utf-8").splitlines()
        for i in range(0, len(lines), 2):
            got[json.loads(lines[i])["update"]["_id"]] = json.loads(lines[i + 1])["script"]["params"]["values"]
    return got


def compare(out: Path, expected_path: Path, platform="threads"):
    """回傳 (寫入篇數, 應寫入篇數, 不符篇數)。三個計數都不知道的貼文不應寫入。"""
    want = {c: v for c, v in json.loads(expected_path.read_text(encoding="utf-8"))[platform].items() if v}
    got = bulk_values(out)
    bad = sum(1 for c in set(want) | set(got) if got.get(c) != want.get(c))
    return len(got), len(want), bad


def ids_digest(out: Path):
    ids = sorted(bulk_values(out))
    return len(ids), len(set(ids)), hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]


def truncate_lines(path: Path, every=10):
    lines = path.read_text(encoding="utf-8").splitlines()
    for i in range(0, len(lines), every):
        lines[i] = lines[i][: len(lines[i]) // 2]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(range(0, len(lines), every))


def _rename(d: dict, mapping):
    n = 0
    for old, new in mapping.items():
        if old in d:
            d[new] = d.pop(old)
            n += 1
    return n


def rename_keys(path: Path, mapping):
    """.json（{"records": [...]}）或 .jsonl：每筆 post 的第一層與第二層 key 依 mapping 改名，回傳改了幾個。"""
    jsonl = path.suffix == ".jsonl"
    text = path.read_text(encoding="utf-8")
    recs = [json.loads(line) for line in text.splitlines() if line.strip()] if jsonl else json.loads(text)["records"]
    n = 0
    for rec in recs:
        post = rec.get("post") if isinstance(rec, dict) else None
        if not isinstance(post, dict):
            continue
        n += _rename(post, mapping)
        for v in post.values():
            if isinstance(v, dict):
                n += _rename(v, mapping)
    if jsonl:
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    else:
        path.write_text(json.dumps({"records": recs}, ensure_ascii=False), encoding="utf-8")
    return n


def _load(name, path: Path):
    sys.path.insert(0, str(path.parent))  # 讓它 import 同目錄的 normalize
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def item429(code: Path, fake_es: Path, work: Path):
    """兩篇 index 動作，其中一篇第一次回單筆 429。回傳 (送出統計, ES 請求數, 被拒那篇最後有沒有寫進去)。"""
    bf, fe = _load("bf_under_test", code / "backfill.py"), _load("fake_es_under_test", fake_es)
    out = work / "out"
    out.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"index": {"_index": "i", "_id": c, "version": 1, "version_type": "external"}}) + "\n"
             + json.dumps({"post_id": c}) + "\n" for c in ("A", "B")]
    payload = "".join(lines).encode("utf-8")
    (out / "bulk_00001.ndjson").write_bytes(payload)
    (out / "manifest.json").write_text(json.dumps({
        "index": "i", "action": "index",
        "files": [{"file": "bulk_00001.ndjson", "docs": 2, "sha256": hashlib.sha256(payload).hexdigest()}]}))
    es = fe.FakeES(reject_items_once={"B"})
    stats = bf.send_bulk_files(out, "http://x", 0, post=fe.poster(es), sleep=lambda s: None)
    return dict(sorted(stats.items())), es.requests, ("i", "B") in es.docs


T1, T2, T3 = 1_777_900_000_000, 1_778_000_000_000, 1_778_100_000_000


def guard_mixed(code: Path, fake_es: Path, work: Path):
    """回傳 (送出統計, 線上文件)。線上：爬蟲在 T2 寫了 repost 20；回補輸入缺 T2 那次抓取。"""
    bf, fe = _load("bf_under_test", code / "backfill.py"), _load("fake_es_under_test", fake_es)
    recs = [{"crawled_at": T1, "post": {"code": "P1", "text_post_app_info":
                                        {"repost_count": 10, "quote_count": 1, "reshare_count": 1}}},
            {"crawled_at": T3, "post": {"code": "P1", "text_post_app_info": {"quote_count": 3, "reshare_count": 3}}}]
    (work / "src").mkdir(parents=True, exist_ok=True)
    (work / "src" / "batch_0001.json").write_text(json.dumps({"records": recs}), encoding="utf-8")
    posts = bf.merge_latest(bf.iter_raw_records(work / "src"))[0]  # 舊版回傳 3 個值、新版 4 個
    manifest = bf.write_bulk(posts, work / "out", "threads-posts", 500, 5_000_000)
    (work / "out" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    online = {"post_id": "P1", "text_post_app_info": {"repost_count": 20}}
    if "counts_at" in bf.GUARD_SCRIPT:
        online["counts_at"] = {"repost_count": T2}  # 逐欄時間戳
    else:
        online["counts_crawled_at"] = T2  # 整篇一個時間戳（fc0b075 版）
    es = fe.FakeES({("threads-posts", "P1"): {"_source": online, "_version": 1}})
    stats = bf.send_bulk_files(work / "out", "http://x", 0, post=fe.poster(es), sleep=lambda s: None)
    return dict(sorted(stats.items())), es.docs[("threads-posts", "P1")]["_source"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="demo_cli.sh 的比對與壞資料工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compare")
    p.add_argument("out", type=Path)
    p.add_argument("expected", type=Path)
    p.add_argument("--platform", default="threads")
    p = sub.add_parser("ids")
    p.add_argument("outs", type=Path, nargs="+")
    p = sub.add_parser("truncate-lines")
    p.add_argument("file", type=Path)
    p.add_argument("--every", type=int, default=10)
    p = sub.add_parser("rename-keys")
    p.add_argument("file", type=Path)
    p.add_argument("pairs", nargs="+", help="old=new")
    for name in ("item429", "guard-mixed"):
        p = sub.add_parser(name)
        p.add_argument("--code", type=Path, required=True)
        p.add_argument("--fake-es", type=Path, required=True)
        p.add_argument("--work", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "compare":
        written, want, bad = compare(a.out, a.expected, a.platform)
        print(f"written={written} expected={want} mismatches={bad}")
        return 0 if bad == 0 else 1
    if a.cmd == "ids":
        for out in a.outs:
            n, uniq, digest = ids_digest(out)
            print(f"{out.name} ids={n} unique={uniq} sha256[:16]={digest}")
        return 0
    if a.cmd == "truncate-lines":
        print(f"truncated_lines={truncate_lines(a.file, a.every)}")
        return 0
    if a.cmd == "rename-keys":
        print(f"renamed_keys={rename_keys(a.file, dict(kv.split('=', 1) for kv in a.pairs))}")
        return 0
    if a.cmd == "item429":
        stats, requests, stored = item429(a.code, a.fake_es, a.work)
        print(f"send={stats} es_requests={requests} rejected_item_stored={stored}")
        return 0
    stats, doc = guard_mixed(a.code, a.fake_es, a.work)
    print(f"send={stats}")
    print(f"online_after={json.dumps(doc, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
