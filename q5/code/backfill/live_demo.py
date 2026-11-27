#!/usr/bin/env python3
"""用 Threads 實際數字（2026-11-16 抓的頁面內嵌 JSON）對 ES 走一遍：爬蟲寫入 → 回補 → 重跑。
顯示在頁面上的 Python ＝ 這裡實際 exec 的字串。分階段跑，方便在每一階段之間截 Kibana。
用法：python live_demo.py <ES 位址> <階段: setup|crawl|prep|backfill|rerun> [log.json]"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backfill import GUARD_SCRIPT  # noqa: E402  回補用的同一份 painless 腳本

ES, STAGE = sys.argv[1].rstrip("/"), sys.argv[2]
LOG = Path(sys.argv[3]) if len(sys.argv) > 3 else None
IDX = "q5live-threads"
STATE = Path(__file__).resolve().parent.parent / "logs" / "live_demo_state.json"
now = int(json.loads(STATE.read_text())["now"]) if STATE.exists() else int(time.time() * 1000)
HIST = int(datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp() * 1000)  # GCS 歷史抓取時間（示範資料）
ns = {"requests": requests, "ES": ES, "IDX": IDX, "now": now, "HIST": HIST, "GUARD_SCRIPT": GUARD_SCRIPT}

CODE = {
"setup": '''FIELDS = ("repost_count", "quote_count", "reshare_count")
requests.put(f"{ES}/{IDX}", json={
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {"dynamic": "strict", "properties": {
        "post_id": {"type": "keyword"},
        "text_post_app_info": {"properties": {f: {"type": "long"} for f in FIELDS}},
        "counts_at": {"properties": {f: {"type": "date", "format": "epoch_millis"} for f in FIELDS}}}}})
r = requests.put(f"{ES}/{IDX}/_doc/DfsCg3vpUH6?refresh=true", json={"post_id": "DfsCg3vpUH6"})''',
"crawl": '''def crawl_write(post_id, j, at):            # j＝頁面內嵌 JSON 的 text_post_app_info
    fields = ("repost_count", "quote_count", "reshare_count")
    return requests.post(f"{ES}/{IDX}/_update/{post_id}?refresh=true", json={
        "doc": {"post_id": post_id,
                "text_post_app_info": {f: j[f] for f in fields},   # 整數照寫，null 保留成 null
                "counts_at": {f: at for f in fields}},             # 每欄附抓取時間（epoch 毫秒）
        "doc_as_upsert": True})

crawl_write("DfsCgPwpdE3", {"repost_count": 12, "quote_count": 1, "reshare_count": 5}, now)
r = crawl_write("Dfk_SPpH-sH", {"repost_count": 1, "quote_count": 0, "reshare_count": None}, now)''',
"backfill": '''history = {  # GCS 歷史抓取（示範資料），每欄已取「有值的最新一次」
    "DfsCgPwpdE3": {"repost_count": 9, "quote_count": 1, "reshare_count": 3},   # 線上更新 → 不該被蓋
    "DfsCg3vpUH6": {"repost_count": 1, "quote_count": 0, "reshare_count": 14},  # 線上還空 → 補齊
}
def backfill(post_id, values):
    return requests.post(f"{ES}/{IDX}/_update/{post_id}?refresh=true", json={"script": {
        "lang": "painless", "source": GUARD_SCRIPT,      # 逐欄：線上時間戳較舊或沒有才寫
        "params": {"obj": "text_post_app_info", "values": values,
                   "at": {f: HIST for f in values}}}})

res = {pid: backfill(pid, v).json()["result"] for pid, v in history.items()}
r = requests.get(f"{ES}/{IDX}/_doc/DfsCg3vpUH6")''',
"rerun": '''res = {pid: backfill(pid, v).json()["result"] for pid, v in history.items()}   # 同一批再送一次
r = requests.post(f"{ES}/{IDX}/_update/DfsCg3vpUH6", json={"script": {
    "lang": "painless", "source": GUARD_SCRIPT,
    "params": {"obj": "text_post_app_info", "values": history["DfsCg3vpUH6"],
               "at": {f: HIST for f in history["DfsCg3vpUH6"]}}}})''',
}

if STAGE == "setup":
    STATE.write_text(json.dumps({"now": now}))
    requests.delete(f"{ES}/{IDX}", timeout=30)
if STAGE in ("rerun",):  # 重跑要沿用上一階段定義的 history / backfill
    exec(CODE["backfill"].split("res = ")[0], ns)
if STAGE == "prep":
    sys.exit(0)
exec(CODE[STAGE], ns)
r = ns["r"]
print(STAGE, r.status_code, json.dumps(r.json(), ensure_ascii=False)[:300], ns.get("res"))
if LOG:
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {"stages": []}
    log["stages"] = [s for s in log["stages"] if s["stage"] != STAGE]
    log["stages"].append({"stage": STAGE, "code": CODE[STAGE], "status": r.status_code,
                          "resp": r.json(), "res": ns.get("res"), "now": now, "HIST": HIST})
    LOG.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
