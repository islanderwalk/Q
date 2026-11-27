#!/usr/bin/env python3
"""對 ES 跑 Q5 三個情境，把「顯示的程式碼＝實際執行的程式碼」與 ES 的實際回應錄成 trace.json（給頁面動畫用）。
用法：python3 record_trace.py <ES 位址> <輸出 trace.json>"""
import json
import sys
import time
from datetime import datetime, timezone

import requests

sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 放在 q5/code/backfill/ 旁邊時直接 import 同目錄的 backfill.py
from backfill import GUARD_SCRIPT  # noqa: E402  回補用的同一份 painless 腳本

ES = sys.argv[1].rstrip("/")
OUT = sys.argv[2]
IDX = "q5art-threads"


def ms(s):
    return int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() * 1000)


T1, T2, T3 = ms("2026-05-01T00:00:00"), ms("2026-05-02T00:00:00"), ms("2026-05-03T00:00:00")
NAIVE = ("if (ctx._source.crawled_at != null && ctx._source.crawled_at >= params.at) { ctx.op = 'noop'; } "
         "else { for (def f : params.values.keySet()) { ctx._source.text_post_app_info[f] = params.values[f]; } "
         "ctx._source.crawled_at = params.at; }")

# 重置
requests.delete(f"{ES}/{IDX}", timeout=30)
requests.delete(f"{ES}/{IDX}-naive", timeout=30)

ns = {"requests": requests, "ES": ES, "IDX": IDX, "T1": T1, "T2": T2, "T3": T3,
      "GUARD_SCRIPT": GUARD_SCRIPT, "NAIVE": NAIVE}

# 每一步：(id, 標題, 說明, 程式碼, 之後要讀回的文件 id 清單)
SCENES = [
    ("mapping", "ES mapping：加新欄位", [
        ("m1", "建立只有 post_id 的 strict index", "舊 mapping：dynamic 設 strict，沒宣告的欄位一律拒絕。",
         '''r = requests.put(f"{ES}/{IDX}", json={
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {"dynamic": "strict",
                 "properties": {"post_id": {"type": "keyword"}}}})
for pid in ("T-0001", "T-0002", "T-0003"):
    requests.put(f"{ES}/{IDX}/_doc/{pid}?refresh=true", json={"post_id": pid})''', ["T-0001"]),
        ("m2", "爬蟲在 mapping 還沒加欄位時寫入", "新欄位沒宣告，被 strict mapping 擋下，文件沒被改動。",
         '''r = requests.post(f"{ES}/{IDX}/_update/T-0001", json={"doc": {
    "text_post_app_info": {"repost_count": 20},
    "counts_at": {"repost_count": T2}}})''', ["T-0001"]),
        ("m3", "update mapping 加新欄位（不 reindex）", "三個計數用 long；每欄時間戳用 date＋epoch_millis。",
         '''FIELDS = ("repost_count", "quote_count", "reshare_count")
r = requests.put(f"{ES}/{IDX}/_mapping", json={"properties": {
    "text_post_app_info": {"properties": {f: {"type": "long"} for f in FIELDS}},
    "counts_at": {"properties": {f: {"type": "date", "format": "epoch_millis"}
                                 for f in FIELDS}}}})''', ["T-0001"]),
    ]),
    ("crawler", "爬蟲寫入：計數＋counts_at", [
        ("c1", "寫入整數計數，同時寫該欄的 counts_at", "計數和抓取時間在同一個 update 裡寫，T2 是這次抓取時間（epoch 毫秒）。",
         '''r = requests.post(f"{ES}/{IDX}/_update/T-0001?refresh=true", json={"doc": {
    "text_post_app_info": {"repost_count": 20},
    "counts_at": {"repost_count": T2}}})''', ["T-0001"]),
        ("c2", "畫面沒顯示數字：null 保留成 null", "JSON null 是一次觀測，不是 0；一樣記下抓取時間。",
         '''r = requests.post(f"{ES}/{IDX}/_update/T-0002?refresh=true", json={"doc": {
    "text_post_app_info": {"repost_count": None},
    "counts_at": {"repost_count": T2}}})''', ["T-0002"]),
        ("c3", "counts_at 寫成 ISO 字串", "mapping 是 date＋epoch_millis，字串在寫入時就被擋下。",
         '''r = requests.post(f"{ES}/{IDX}/_update/T-0001", json={"doc": {
    "counts_at": {"repost_count": "2026-05-02T00:00:00Z"}}})''', ["T-0001"]),
    ]),
    ("backfill", "回補：逐欄時間戳保護", [
        ("b1", "回補送出（線上 repost 已有 T2 的新值）", "合併後：repost 取 T1 的 10、quote／reshare 取 T3 的 3。腳本逐欄比對 counts_at。",
         '''values = {"repost_count": 10, "quote_count": 3, "reshare_count": 3}
at     = {"repost_count": T1, "quote_count": T3, "reshare_count": T3}
r = requests.post(f"{ES}/{IDX}/_update/T-0001?refresh=true", json={"script": {
    "lang": "painless", "source": GUARD_SCRIPT,
    "params": {"obj": "text_post_app_info", "values": values, "at": at}}})''', ["T-0001"]),
        ("b2", "同一批重跑", "每欄的線上時間戳都不比回補舊，整個 noop，什麼都不寫。",
         '''r = requests.post(f"{ES}/{IDX}/_update/T-0001?refresh=true", json={"script": {
    "lang": "painless", "source": GUARD_SCRIPT,
    "params": {"obj": "text_post_app_info", "values": values, "at": at}}})''', ["T-0001"]),
        ("b3", "對照：只有整篇一個時間戳的寫法", "這不是交卷的設計。T-0003 先由爬蟲寫 repost=20（T2），再用整篇 T3 回補。",
         '''requests.put(f"{ES}/{IDX}-naive/_doc/T-0003?refresh=true", json={
    "post_id": "T-0003", "crawled_at": T2,
    "text_post_app_info": {"repost_count": 20}})
r = requests.post(f"{ES}/{IDX}-naive/_update/T-0003?refresh=true", json={"script": {
    "lang": "painless", "source": NAIVE,
    "params": {"values": {"repost_count": 10, "quote_count": 3, "reshare_count": 3}, "at": T3}}})''', ["naive:T-0003"]),
    ]),
]


def slim(o):
    if isinstance(o, dict):
        return {k: slim(v) for k, v in o.items() if k not in ("_shards", "_seq_no", "_primary_term", "_version")}
    return o


trace = {"scenes": []}
info = requests.get(ES, timeout=30).json()
trace["es"] = {"version": info["version"]["number"], "build_hash": info["version"]["build_hash"][:12],
               "lucene": info["version"]["lucene_version"], "url": ES,
               "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "T": {"T1": T1, "T2": T2, "T3": T3}}
for sid, stitle, steps in SCENES:
    sc = {"id": sid, "title": stitle, "steps": []}
    for xid, title, note, code, show in steps:
        ns.pop("r", None)
        before = {}
        for d in show:  # 執行前先讀回（文件還不存在就是 None）
            ix, did = (IDX + "-naive", d[6:]) if d.startswith("naive:") else (IDX, d)
            g = requests.get(f"{ES}/{ix}/_doc/{did}", timeout=30).json()
            before[did] = g.get("_source")
        t0 = time.time()
        exec(code, ns)  # 顯示的程式碼＝實際執行的程式碼
        r = ns["r"]
        dt = (time.time() - t0) * 1000
        body = r.json()
        docs = {}
        for d in show:
            ix, did = (IDX + "-naive", d[6:]) if d.startswith("naive:") else (IDX, d)
            g = requests.get(f"{ES}/{ix}/_doc/{did}", timeout=30).json()
            docs[did] = g.get("_source")
        step = {"id": xid, "title": title, "note": note, "code": code, "status": r.status_code,
                "ms": round(dt), "resp": slim(body), "docs_before": before, "docs": docs}
        sc["steps"].append(step)
        print(xid, r.status_code, body.get("result") or body.get("error", {}).get("type") or body.get("acknowledged"))
    trace["scenes"].append(sc)
mp = requests.get(f"{ES}/{IDX}/_mapping?filter_path=*.mappings.properties.counts_at", timeout=30).json()
trace["mapping_counts_at"] = mp
trace["guard_script"] = GUARD_SCRIPT
trace["naive_script"] = NAIVE
json.dump(trace, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("wrote", OUT)
