# -*- coding: utf-8 -*-
"""補 data/channels/instagram.jsonl 的 published_at。

2026-10-31 未登入抓的 48 篇全是 null：未登入主頁的內嵌 JSON 不帶 taken_at（capture_q6_fb_ig.py），重播進 ES 後 month 是 unknown。
兩個來源，依序：
1. 登入爬取的副本 q6/data/meta-login/instagram_clean.jsonl（2026-11-01，1,134 列，post_id = in-<shortcode>，每列都有
   taken_at 轉成的 published_at）：shortcode（url 最後一段）對得上就照抄，到秒。
2. 副本裡沒有的：shortcode 以 base64url（A-Z a-z 0-9 - _）解成 media id，media id 的高位是建立時間：
   ms = (id >> 23) + 1314220021721。建立時間通常比 taken_at 早幾十秒；對副本每一列的驗證數字寫進 _meta。
只補 null 或還是 shortcode 推算值的列（副本有精確值就換成精確值）；其他 8 個欄位、列的順序、換行字元（CRLF）都不動。
逐列的來源（loggedin_crawl／shortcode／page）與驗證數字寫在 _meta/instagram_published_at.json，重跑結果相同。

用法：python3 fill_ig_published_at.py [樣本 jsonl，預設 data/channels/instagram.jsonl] [登入爬取副本 jsonl]
"""
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

TW = timezone(timedelta(hours=8))
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SAMPLE = HERE.parent / "data" / "channels" / "instagram.jsonl"
CRAWL = REPO / "q6" / "data" / "meta-login" / "instagram_clean.jsonl"
B64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
EPOCH_MS = 1314220021721      # media id 時間部分的起點（epoch 毫秒）


def shortcode(url: str) -> str:
    """https://www.instagram.com/p/<shortcode>/（或 /reel/<shortcode>/）→ shortcode。"""
    return (url or "").rstrip("/").rsplit("/", 1)[-1]


def shortcode_ms(code: str):
    """shortcode → 建立時間（epoch 毫秒）；空字串或含 base64url 以外的字元回 None。"""
    if not code:
        return None
    n = 0
    for ch in code:
        i = B64URL.find(ch)
        if i < 0:
            return None
        n = n * 64 + i
    return (n >> 23) + EPOCH_MS


def shortcode_time(code: str):
    """shortcode → +08:00 ISO 時間（到秒，捨去毫秒）；解不出來回 None。"""
    ms = shortcode_ms(code)
    return None if ms is None else datetime.fromtimestamp(ms // 1000, TW).isoformat(timespec="seconds")


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def validate(crawl_rows: list) -> dict:
    """shortcode 推算值減登入爬取的 published_at（秒），副本每一列都算；負的代表推算值比較早。"""
    d = sorted(ms / 1000 - _ts(r["published_at"]) for r in crawl_rows
               if r.get("published_at") and (ms := shortcode_ms(shortcode(r.get("url")))) is not None)
    return {"rows": len(d), "within_60s": sum(abs(x) <= 60 for x in d), "within_1h": sum(abs(x) <= 3600 for x in d),
            "median_s": round(statistics.median(d), 1) if d else None, "beyond_1h_s": [round(x) for x in d if abs(x) > 3600],
            "later_by_over_60s": sum(x > 60 for x in d)}


def fill(rows: list, crawl_rows: list) -> list:
    """就地補 rows 的 published_at，回傳逐列的來源 [{post_id, published_at, basis[, shortcode_delta_s]}]。"""
    exact = {shortcode(r.get("url")): r["published_at"] for r in crawl_rows if r.get("published_at")}
    out = []
    for r in rows:
        code = shortcode(r.get("url"))
        derived = shortcode_time(code)
        if not r.get("published_at") or r["published_at"] == derived:    # 沒有時間，或還是推算值：副本有精確值就換掉
            r["published_at"] = exact.get(code) or derived
        cur = r["published_at"]
        basis = ("missing" if not cur else "loggedin_crawl" if cur == exact.get(code) else "shortcode" if cur == derived else "page")
        rec = {"post_id": r["post_id"], "published_at": cur, "basis": basis}
        if basis == "loggedin_crawl" and derived:
            rec["shortcode_delta_s"] = round(_ts(derived) - _ts(cur))      # 逐列對照：推算值減精確值
        out.append(rec)
    return out


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO).as_posix()
    except ValueError:
        return p.name


def main(argv) -> int:
    sample = Path(argv[1]) if len(argv) > 1 else SAMPLE
    crawl = Path(argv[2]) if len(argv) > 2 else CRAWL
    raw = sample.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"                  # 本機（Windows）抓的檔案是 CRLF，照原樣寫回
    rows = [json.loads(l) for l in raw.splitlines() if l.strip()]
    crawl_rows = [json.loads(l) for l in crawl.read_text(encoding="utf-8").splitlines() if l.strip()]
    per_row = fill(rows, crawl_rows)
    sample.write_bytes("".join(json.dumps(r, ensure_ascii=False) + newline for r in rows).encode("utf-8"))
    meta = {
        "file": sample.name,
        "sources": {
            "loggedin_crawl": f"{_rel(crawl)}：shortcode 相同的列照抄 published_at（taken_at 轉 +08:00，到秒）",
            "shortcode": f"media id = shortcode 以 base64url（{B64URL}）解碼；ms = (id >> 23) + {EPOCH_MS}；捨去毫秒、轉 +08:00",
            "page": "抓取時頁面本身就有的時間（未登入主頁有給 taken_at 才會有）",
        },
        "counts": dict(Counter(r["basis"] for r in per_row)),
        "by_month": dict(sorted(Counter((r["published_at"] or "unknown")[:7] for r in per_row).items())),
        "validation": validate(crawl_rows),
        "rows": per_row,
    }
    out = sample.parent / "_meta" / "instagram_published_at.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("counts", "by_month", "validation")}, ensure_ascii=False))
    return 0 if "missing" not in meta["counts"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
