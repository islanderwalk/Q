#!/usr/bin/env python3
"""Q5 (c)：Threads 轉發／引用／分享數與 IG Reels 觀看數的歷史回補原型。

流程：
  1. 讀原始資料（GCS 以本機目錄模擬）：*.json 每檔是 {"records": [...]}，*.jsonl 每行一筆 record。
     record = {"crawled_at": 毫秒, "platform": "threads" 或 "instagram"（省略時視為 threads）, "post": {...}}。
     原始檔所在的目錄是一個分區（例如 threads/2026-05-11，對應 GCS 的「平台／週」prefix）。
     壞掉的檔或行不會讓整批中斷：寫進 rejects.jsonl 與 quality.json。
  2. 正規化計數欄位（normalize.py）：null 保留、不補 0，歷史欄位名依別名表兼容。
  3. 同一篇合併多次抓取：基本欄位取 crawled_at 最大的一筆；每個計數欄位取「有值」的最新一次抓取，
     並記下那一次的 crawled_at。JSON null 算有值；key 不存在或型別錯算不知道，不拿來蓋掉較舊抓取的值。
     相同 crawled_at 保留先讀到的（依路徑排序讀取，結果確定）。
  4. 品質關卡（沒過就停，退出碼 3，不產生 bulk 檔）：
     - 壞掉／丟棄的 record 比例超過 --max-reject-rate，或整檔壞掉超過 --max-bad-files；
     - 任一分區的任一計數欄位，「不知道」的比例超過 --max-unknown-rate。某一期改了欄位名而別名表沒有時，
       那一期會接近 100%，在這裡停下，不會默默少寫。quality.json 列出每個分區兩層內的 key 清單，方便補別名表。
  5. 產生 ES _bulk NDJSON，兩種寫法：
     - update（預設）：部分更新既有文件，只寫「知道」的計數欄位。每個欄位各有時間戳 counts_at.<欄位>
       （epoch 毫秒），painless 腳本逐欄比較：線上那一欄一樣新或更新就不動，所以較舊的值不會蓋掉較新的值，
       不同欄位來自不同次抓取也一樣；同一批重跑只會得到 noop。文件不在 index 裡會回 404，記為 not_in_index。
       三個計數都不知道的貼文不產生動作（manifest 的 skipped_no_known_metric）。
       前提：爬蟲每次寫計數也要寫同一欄的 counts_at（同樣是 epoch 毫秒），最好走同一支腳本。
     - index：整份文件寫進全新的 index，version_type=external、version=crawled_at（毫秒）。
       只適用於「回補產生的就是完整文件」；寫進已有其他欄位的 index 會把那些欄位整份蓋掉。
  6. 批次以文件數與位元組雙重上限切分；送出端以 --rate-per-sec 限速（單位是 bulk 請求，不是文件），
     HTTP 429／502／503／504 或單筆 429（es_rejected_execution）以隨機指數退避，只重送被拒的那幾筆；
     update 寫法的單筆 409（retry_on_conflict 用完）也重送，腳本有時間戳保護，重送是冪等的。
  7. 輸出 manifest.json（輸入檔與 bulk 檔的 sha256）、quality.json、rejects.jsonl；
     送出時寫 sent.json，中斷後重跑只送還沒送完的檔。

用法：
    python backfill.py --src <原始資料目錄> --out <輸出目錄> --index threads-posts \
        --batch-docs 500 --max-bytes 5000000
    python backfill.py --src ... --out ... --platform instagram --index ig-posts
    python backfill.py --src ... --out ... --send http://localhost:9200 --rate-per-sec 2
（送出步驟會實際打 ES；原型測試不連網，見 tests/。）

退出碼：0 成功；2 參數錯誤；3 品質關卡沒過（不產生 bulk 檔）；4 送出有失敗（見 send 統計與 send_errors.jsonl）。
"""
import argparse
import hashlib
import json
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from normalize import normalize_counts, normalize_ig_views  # noqa: E402

URLS = {
    "threads": "https://www.threads.com/@{user}/post/{code}",
    "instagram": "https://www.instagram.com/{user}/reel/{code}/",
}
DEFAULT_INDEX = {"threads": "threads-posts", "instagram": "ig-posts"}
# 每個平台要回補的計數欄位，以及它們在 ES 文件裡掛在哪個物件下（None 表示最上層）
METRIC_FIELDS = {"threads": ("repost_count", "quote_count", "reshare_count"), "instagram": ("video_view_count",)}
METRIC_OBJ = {"threads": "text_post_app_info", "instagram": None}
SOURCE_SUFFIXES = (".json", ".jsonl")

# update 寫法的腳本：每個計數欄位各有時間戳 counts_at.<欄位>（epoch 毫秒，放在文件最上層）。
# 線上那一欄的時間戳一樣新或更新就不動那一欄，否則寫入值與時間戳；一欄都沒寫就 noop。
# 線上的時間戳不是數字（例如爬蟲寫了 ISO 字串）就丟錯：這筆回 400、記為失敗，不默默略過。
# params.obj 是巢狀物件名（Threads 為 text_post_app_info；IG 為 null，寫在最上層）。
GUARD_SCRIPT = (
    "def src = ctx._source; def t = src; "
    "if (params.obj != null) { if (!(src[params.obj] instanceof Map)) { src[params.obj] = new HashMap(); } "
    "t = src[params.obj]; } "
    "if (!(src.counts_at instanceof Map)) { src.counts_at = new HashMap(); } "
    "boolean changed = false; "
    "for (def f : params.values.keySet()) { def cur = src.counts_at[f]; "
    "if (cur != null && !(cur instanceof Number)) "
    "{ throw new IllegalArgumentException('counts_at.' + f + ' is not epoch_millis'); } "
    "if (cur == null || cur < params.at[f]) { t[f] = params.values[f]; src.counts_at[f] = params.at[f]; "
    "changed = true; } } "
    "if (!changed) { ctx.op = 'noop'; }"
)


class Drop(Exception):
    """record 不能用：kind 為 dropped（資料有問題）或 skipped（不是這次的平台）。"""

    def __init__(self, kind, reason):
        super().__init__(f"{kind}:{reason}")
        self.kind, self.reason = kind, reason


def list_sources(src: Path):
    return sorted(p for p in src.rglob("*") if p.is_file() and p.suffix in SOURCE_SUFFIXES)


def iter_raw_records(src: Path):
    """依路徑排序讀 src 下的 *.json 與 *.jsonl，逐筆回傳 (分區, 位置, record, 問題)。

    分區是原始檔所在的目錄（相對 src，最上層為 "."）。問題為 None 表示讀取正常；
    壞掉的檔或行回傳 (分區, 位置, None, 原因)，由呼叫端記進 rejects，不中斷整批。
    """
    for path in list_sources(src):
        rel = path.relative_to(src).as_posix()
        part = path.parent.relative_to(src).as_posix()
        raw = path.read_bytes()
        if path.suffix == ".jsonl":
            for no, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line.decode("utf-8"))
                except ValueError:  # 含 UnicodeDecodeError、JSONDecodeError
                    yield part, f"{rel}:{no}", None, "bad_json_line"
                    continue
                yield part, f"{rel}:{no}", rec, None
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            yield part, rel, None, "bad_json_file"  # 整檔壞掉：不知道掉了幾筆，關卡會擋下
            continue
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            yield part, rel, None, "no_records_list"
            continue
        for i, rec in enumerate(records):
            yield part, f"{rel}#{i}", rec, None


def _iso(ts):
    if isinstance(ts, bool) or not isinstance(ts, int):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def build_doc(rec, platform="threads"):
    """回傳 (post_id, crawled_at, 基本欄位, 正規化後的計數)；不能用的 record 丟 Drop。

    計數是 normalize.py 的輸出（含 flags 與 unknown），由 merge_latest 決定哪一次抓取的值寫進文件。
    """
    if not isinstance(rec, dict):
        raise Drop("dropped", "not_object")
    rec_platform = rec.get("platform", "threads")
    if rec_platform != platform:
        raise Drop("skipped", f"platform={rec_platform}")
    post = rec.get("post")
    if not isinstance(post, dict):
        raise Drop("dropped", "no_post")
    code = post.get("code")
    if not isinstance(code, str) or not code:
        raise Drop("dropped", "no_post_id")
    crawled = rec.get("crawled_at")
    if isinstance(crawled, bool) or not isinstance(crawled, int) or crawled <= 0:
        raise Drop("dropped", "bad_crawled_at")  # 例如 ISO 字串：版本號必須是整數毫秒

    user = post.get("user")
    user = (user.get("username") if isinstance(user, dict) else None) or post.get("username")
    caption = post.get("caption")
    counts = normalize_counts(post) if platform == "threads" else normalize_ig_views(post)
    base = {
        "platform": platform,
        "post_id": code,
        "url": URLS[platform].format(user=user, code=code) if isinstance(user, str) and user else None,
        "published_at": _iso(post.get("taken_at")),
        "text": caption.get("text") if isinstance(caption, dict) else None,
    }
    return code, crawled, base, counts


def _key_paths(post):
    """post 物件兩層內的 key（例如 text_post_app_info.repost_count），盤點舊欄位名用；只列名稱，不列值。"""
    for k, v in post.items():
        yield k
        if isinstance(v, dict):
            for k2 in v:
                yield f"{k}.{k2}"


def merge_latest(records, platform="threads"):
    """同一 post_id 合併多次抓取，回傳 (posts, quality, rejects, partitions)。

    posts[post_id] = {"version": 最新 crawled_at, "base": 該次的基本欄位, "where": 位置,
                      "fields": {欄位: (crawled_at, 值)}}；fields 只放「知道」的欄位（JSON null 也算知道）。
    partitions[分區] = {"records": 這個平台的 record 數, "rejected": 壞掉或丟棄的數, "skipped": 其他平台的數,
                        "unknown": {欄位: 這一欄不知道的 record 數}, "keys": {key 路徑: 出現的 record 數}}；
    unknown 以抓取（合併前）計，給分區關卡用。
    """
    fields_of = METRIC_FIELDS[platform]
    posts, quality, rejects, partitions = {}, Counter(), [], {}
    for part, where, rec, problem in records:
        stats = partitions.get(part)
        if stats is None:
            stats = partitions[part] = {"records": 0, "rejected": 0, "skipped": 0,
                                        "unknown": dict.fromkeys(fields_of, 0), "keys": Counter()}
        if problem:
            quality[f"rejected:{problem}"] += 1
            rejects.append({"where": where, "reason": problem})
            stats["rejected"] += 1
            continue
        try:
            code, version, base, counts = build_doc(rec, platform)
        except Drop as e:
            quality[f"{e.kind}:{e.reason}"] += 1
            if e.kind == "dropped":
                rejects.append({"where": where, "reason": e.reason})
                stats["rejected"] += 1
            else:
                stats["skipped"] += 1
            continue
        quality["records"] += 1
        stats["records"] += 1
        stats["keys"].update(_key_paths(rec["post"]))
        for f in counts["unknown"]:
            stats["unknown"][f] += 1
        for f in counts["flags"]:
            quality[f] += 1
        cur = posts.get(code)
        if cur is None:
            cur = posts[code] = {"version": version, "base": base, "where": where, "fields": {}}
        else:
            quality["duplicate_records"] += 1
            if version > cur["version"]:
                cur.update(version=version, base=base, where=where)
            elif version == cur["version"]:
                quality["duplicate_same_crawled_at"] += 1  # 同一次抓取重複落地，保留先讀到的
        for f in fields_of:
            if f in counts["unknown"]:
                continue  # 不知道的值不蓋掉別次抓取
            old = cur["fields"].get(f)
            if old is None or version > old[0]:
                cur["fields"][f] = (version, counts[f])
    quality["docs"] = len(posts)
    for p in posts.values():  # 以「實際要寫入的文件」統計，不含被合併掉的抓取
        for f in fields_of:
            got = p["fields"].get(f)
            if got is None:
                quality[f"unknown:{f}"] += 1
                continue
            if got[1] is None:
                quality[f"null:{f}"] += 1
            if got[0] < p["version"]:
                quality[f"from_older_crawl:{f}"] += 1  # 最新一次抓取缺這個欄位，改用較舊一次的值
    return posts, quality, rejects, partitions


BAD_FILE_KEYS = ("rejected:bad_json_file", "rejected:no_records_list")


def gate_failures(quality, partitions, max_reject_rate, max_bad_files, max_unknown_rate):
    """品質關卡：回傳沒過的原因（空 list 表示通過）。

    - 壞掉的行與丟棄的 record 算比例；整檔壞掉不知道掉了幾筆，另外以檔數計，不混進比例。
    - 每個分區、每個計數欄位：「不知道」（key 不存在、整個容器不見、型別錯、IG 兩個 key 值衝突）的比例
      不能超過 max_unknown_rate。只看全體比例會被稀釋：一週改名、其他週正常時，全體可能只有一兩成。
    """
    bad_files = sum(quality[k] for k in BAD_FILE_KEYS)
    bad = sum(v for k, v in quality.items() if k.startswith(("rejected:", "dropped:"))) - bad_files
    seen = quality["records"] + bad
    failures = []
    if seen == 0:
        failures.append("no_records")
    elif bad / seen > max_reject_rate:
        failures.append(f"reject_rate {bad}/{seen}={bad / seen:.4f} > {max_reject_rate}")
    if bad_files > max_bad_files:
        failures.append(f"bad_files {bad_files} > {max_bad_files}")
    for part, stats in sorted(partitions.items()):
        n = stats["records"]
        for f, k in stats["unknown"].items():
            if n and k / n > max_unknown_rate:
                failures.append(f"unknown_rate {part} {f} {k}/{n}={k / n:.4f} > {max_unknown_rate}")
    return failures


def partition_report(partitions):
    """quality.json 的分區盤點：筆數、每欄不知道的筆數與比例、key 清單（依名稱排序）。"""
    out = {}
    for part, s in sorted(partitions.items()):
        n = s["records"]
        out[part] = {"records": n, "rejected": s["rejected"], "skipped": s["skipped"],
                     "unknown": dict(s["unknown"]),
                     "unknown_rate": {f: round(k / n, 4) if n else None for f, k in s["unknown"].items()},
                     "keys": dict(sorted(s["keys"].items()))}
    return out


def field_stamps(post):
    """每個知道的計數欄位取自哪一次抓取：{欄位: crawled_at}（毫秒）。"""
    return {f: v for f, (v, _) in sorted(post["fields"].items())}


def full_doc(post, platform="threads"):
    """index 寫法用的完整文件：不知道的計數存 null（不是 0），也沒有時間戳。"""
    values = {f: post["fields"].get(f, (None, None))[1] for f in METRIC_FIELDS[platform]}
    obj = METRIC_OBJ[platform]
    return {**post["base"], **({obj: values} if obj else values),
            "crawled_at": post["version"], "counts_at": field_stamps(post)}


def bulk_lines(code, post, index, action, platform="threads"):
    """一篇文件的兩行 NDJSON：action 行 + 內容行。update 寫法沒有任何知道的計數時回傳 None。"""
    if action == "index":
        meta = {"index": {"_index": index, "_id": code, "version": post["version"], "version_type": "external"}}
        body = full_doc(post, platform)
    else:
        values = {f: v for f, (_, v) in sorted(post["fields"].items())}
        if not values:
            return None
        meta = {"update": {"_index": index, "_id": code, "retry_on_conflict": 3}}
        body = {"script": {"lang": "painless", "source": GUARD_SCRIPT,
                           "params": {"obj": METRIC_OBJ[platform], "values": values, "at": field_stamps(post)}}}
    return json.dumps(meta, ensure_ascii=False) + "\n" + json.dumps(body, ensure_ascii=False) + "\n"


def clear_outputs(out: Path):
    """刪掉上一次留下的 bulk 檔與 manifest，避免送出時混進舊批次。"""
    for old in out.glob("bulk_*.ndjson"):
        old.unlink()
    (out / "manifest.json").unlink(missing_ok=True)


def write_bulk(posts, out: Path, index: str, batch_docs: int, max_bytes: int, action="update", platform="threads"):
    """切批寫出 NDJSON（依 post_id 排序，同樣輸入產生位元組相同的檔）。回傳 manifest 內容。"""
    out.mkdir(parents=True, exist_ok=True)
    clear_outputs(out)
    manifest = {"index": index, "action": action, "files": [], "total_docs": 0, "skipped_no_known_metric": 0}
    if action == "index":
        manifest.update(version_type="external", version_source="crawled_at(ms)")
    else:
        manifest.update(guard="counts_at.<field>(ms)", retry_on_conflict=3)
    batch, batch_bytes, file_no = [], 0, 0

    def flush():
        nonlocal batch, batch_bytes, file_no
        if not batch:
            return
        file_no += 1
        path = out / f"bulk_{file_no:05d}.ndjson"
        payload = "".join(batch).encode("utf-8")
        path.write_bytes(payload)
        manifest["files"].append({"file": path.name, "docs": len(batch),
                                  "bytes": len(payload),
                                  "sha256": hashlib.sha256(payload).hexdigest()})
        manifest["total_docs"] += len(batch)
        batch, batch_bytes = [], 0

    for code in sorted(posts):
        lines = bulk_lines(code, posts[code], index, action, platform)
        if lines is None:
            manifest["skipped_no_known_metric"] += 1
            continue
        size = len(lines.encode("utf-8"))
        if batch and (len(batch) >= batch_docs or batch_bytes + size > max_bytes):
            flush()
        batch.append(lines)  # 單篇超過 max_bytes 時自成一檔
        batch_bytes += size
    flush()
    return manifest


def input_manifest(src: Path):
    return [{"file": p.relative_to(src).as_posix(), "bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in list_sources(src)]


def _outcome(status, result):
    if status in (200, 201):
        return "noop" if result.get("result") == "noop" else "ok"
    if status == 409:
        return "version_conflict_skipped"  # index 寫法：線上已有同版或更新版本，屬於冪等的正常結果
    if status == 404:
        return "not_in_index"  # update 寫法：ES 沒有這篇（document_missing），不建立殘缺文件
    return "failed"


RETRY_HTTP = (429, 502, 503, 504)


def send_bulk_files(out: Path, es_url: str, rate_per_sec: float, post=None, sleep=time.sleep,
                    rand=random.random, max_attempts=5, backoff_base=2.0, backoff_cap=120.0):
    """依 manifest 送出 bulk 檔（限速 rate_per_sec 個 bulk 請求/秒），回傳統計。

    - HTTP 429／502／503／504 或單筆 status 429：隨機指數退避後只重送被拒的那幾筆（重送是冪等的）。
    - update 寫法的單筆 409（retry_on_conflict 用完）同樣重送；index 寫法的 409 視為已套用。
    - noop 視為已套用，不算失敗。
    - 每送完一檔就把 sha256 記進 sent.json（依 ES 位址分開）；重跑時跳過已送完的檔。
    post、sleep、rand 可注入（測試用）；預設用 requests.post。
    """
    if post is None:
        import requests

        def post(url, data, headers):
            return requests.post(url, data=data, headers=headers, timeout=60)

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    retry_items = {429, 409} if manifest.get("action") == "update" else {429}
    ledger_path = out / "sent.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    done = ledger.setdefault(es_url, {})
    stats, errors = Counter(), []
    interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
    url = f"{es_url.rstrip('/')}/_bulk"
    for entry in manifest["files"]:
        if entry["sha256"] in done:
            stats["files_skipped_already_sent"] += 1
            continue
        lines = (out / entry["file"]).read_text(encoding="utf-8").splitlines(keepends=True)
        pairs = ["".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
        pending, file_ok, file_failed = list(range(len(pairs))), True, 0
        for attempt in range(1, max_attempts + 1):
            resp = post(url, "".join(pairs[i] for i in pending).encode("utf-8"),
                        {"Content-Type": "application/x-ndjson"})
            stats["requests"] += 1
            if resp.status_code in RETRY_HTTP:
                stats[f"http_retry_{resp.status_code}"] += 1
                retry = pending
            elif resp.status_code != 200:
                stats[f"http_{resp.status_code}"] += 1
                file_ok = False
                break
            else:
                items = resp.json().get("items", [])
                if len(items) != len(pending):
                    stats["item_count_mismatch"] += 1
                    file_ok = False
                    break
                retry = []
                for i, item in zip(pending, items):
                    result = next(iter(item.values()))
                    status = result.get("status")
                    if status in retry_items:
                        retry.append(i)
                        continue
                    outcome = _outcome(status, result)
                    stats[outcome] += 1
                    file_failed += outcome == "failed"
                    if outcome in ("failed", "not_in_index"):
                        errors.append({"file": entry["file"], "_id": result.get("_id"), "status": status,
                                       "error": (result.get("error") or {}).get("type")})
            if not retry:
                break
            if attempt == max_attempts:
                stats["failed_retry_gave_up"] += len(retry)
                file_ok = False
                break
            stats["retried_items"] += len(retry)
            delay = min(backoff_cap, backoff_base * 2 ** (attempt - 1))
            sleep(delay / 2 + rand() * delay / 2)  # 隨機指數退避（一半固定、一半隨機）
            pending = retry
        if file_ok and not file_failed:  # 有單筆失敗的檔不記為已送，重跑時整檔重送（已套用的會是 noop）
            stats["files_sent"] += 1
            done[entry["sha256"]] = entry["file"]
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            stats["files_incomplete"] += 1
        sleep(interval)
    if errors:
        with (out / "send_errors.jsonl").open("a", encoding="utf-8") as fh:
            for e in errors:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    return stats


SEND_FAILURES = ("failed", "failed_retry_gave_up", "item_count_mismatch", "files_incomplete")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Q5 (c) Threads 計數／IG 觀看數回補原型")
    ap.add_argument("--src", required=True, type=Path, help="原始 JSON／JSONL 目錄（GCS 模擬）")
    ap.add_argument("--out", required=True, type=Path, help="輸出目錄（NDJSON、manifest、quality、rejects）")
    ap.add_argument("--index", default=None, help="寫入的 index 或 alias（預設 threads-posts／ig-posts）")
    ap.add_argument("--platform", choices=sorted(URLS), default="threads", help="這次回補的平台，其他平台的 record 略過")
    ap.add_argument("--action", choices=("update", "index"), default="update",
                    help="update＝部分更新既有文件（預設）；index＝整份寫入全新 index")
    ap.add_argument("--batch-docs", type=int, default=500, help="每個 bulk 檔最多幾篇")
    ap.add_argument("--max-bytes", type=int, default=5_000_000, help="每個 bulk 檔位元組上限")
    ap.add_argument("--max-reject-rate", type=float, default=0.01, help="壞掉／丟棄的 record 比例上限")
    ap.add_argument("--max-bad-files", type=int, default=0, help="整檔壞掉的檔數上限")
    ap.add_argument("--max-unknown-rate", type=float, default=0.2,
                    help="每個分區、每個計數欄位「不知道」的 record 比例上限（欄位改名沒進別名表時接近 1）")
    ap.add_argument("--send", metavar="ES_URL", default=None, help="若指定，送出 bulk（會連線）")
    ap.add_argument("--rate-per-sec", type=float, default=2.0, help="送出端每秒最多幾個 bulk 請求")
    args = ap.parse_args(argv)
    if not args.src.is_dir():
        ap.error(f"--src 不是目錄：{args.src}")
    if args.batch_docs < 1 or args.max_bytes < 1:
        ap.error("--batch-docs 與 --max-bytes 必須大於 0")
    index = args.index or DEFAULT_INDEX[args.platform]

    posts, quality, rejects, partitions = merge_latest(iter_raw_records(args.src), args.platform)
    failures = gate_failures(quality, partitions, args.max_reject_rate, args.max_bad_files, args.max_unknown_rate)
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"platform": args.platform,
              "gate": {"max_reject_rate": args.max_reject_rate, "max_bad_files": args.max_bad_files,
                       "max_unknown_rate": args.max_unknown_rate},
              "counts": dict(sorted(quality.items())), "partitions": partition_report(partitions),
              "gate_failures": failures}
    (args.out / "quality.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "rejects.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejects),
                                            encoding="utf-8")
    if failures:
        clear_outputs(args.out)
        print(f"GATE FAILED: {failures} quality={dict(sorted(quality.items()))}", file=sys.stderr)
        return 3

    manifest = write_bulk(posts, args.out, index, args.batch_docs, args.max_bytes, args.action, args.platform)
    manifest["platform"] = args.platform
    manifest["inputs"] = input_manifest(args.src)
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"posts={len(posts)} docs_written={manifest['total_docs']} "
          f"skipped_no_known_metric={manifest['skipped_no_known_metric']} files={len(manifest['files'])} "
          f"action={args.action} quality={dict(sorted(quality.items()))}")
    if args.send:
        stats = send_bulk_files(args.out, args.send, args.rate_per_sec)
        print(f"send={dict(sorted(stats.items()))}")
        if any(stats[k] for k in SEND_FAILURES) or any(k.startswith("http_") and not k.startswith("http_retry_")
                                                      for k in stats):
            return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
