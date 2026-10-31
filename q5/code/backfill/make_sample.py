#!/usr/bin/env python3
"""產生 Q5 回補示範用的原始資料目錄（模擬 GCS），並附上「正確答案」expected.json。

不連網、固定亂數種子，同樣參數產生位元組相同的檔。資料是合成的，規則取自 q5/evidence 的實測：
- Threads 計數在 post.text_post_app_info.{repost_count, quote_count, reshare_count}；
  畫面轉發數 = repost_count + quote_count；分享旁沒有數字時 reshare_count 是 null（不是 0）。
- IG Reels 觀看數的 JSON key 是 play_count（縮圖 4.2M = 4,298,087 無條件捨去）。
- 舊欄位名（textPostAppInfo、repostCount、repost_cnt、IG 的 video_view_count）是假設，
  這裡故意混進去，用來測別名表；不代表 GCS 上真的有這些名稱。

目錄（每週一個分區，各週用不同時期的欄位名）：
  threads/2026-05-04/batch_0001.json   舊名 A：textPostAppInfo + repostCount／quoteCount／reshareCount（假設）
  threads/2026-05-11/batch_0001.json   舊名 B：text_post_app_info + repost_cnt／quote_cnt／reshare_cnt（假設）
  threads/2026-05-18/part-0000.jsonl   現行名稱（實抓看到的）
  threads/2026-05-25/part-0000.jsonl   現行名稱，另含 1 行壞掉的 JSON、1 筆 ISO 字串 crawled_at
  instagram/2026-05-04/reels.jsonl     play_count（實抓）＋ 部分 video_view_count（假設的舊名）

每篇貼文被抓 1–4 次（不同週），計數只增不減；每筆抓取可能被「弄壞」：
  缺 reshare key（不知道）、整個容器不見（三個都不知道）、轉發數寫成畫面字串「1.2萬」（不知道）、
  同一次抓取重複落地（同 crawled_at 兩筆）。
expected.json 依「每個欄位取有值的最新一次抓取，null 也算有值」算出每篇的正確值，
不呼叫 backfill.py，可用來獨立核對回補結果。

用法：python make_sample.py --out <目錄> [--posts 2000] [--reels 200] [--seed 5]
"""
import argparse
import json
import random
from pathlib import Path

WEEKS = ("2026-05-04", "2026-05-11", "2026-05-18", "2026-05-25")
WEEK0_MS = 1_777_852_800_000  # 2026-05-04T00:00:00Z
WEEK_MS = 7 * 24 * 3600 * 1000
NAMES = {  # 各週的容器與欄位名；前兩週是假設的舊名
    0: ("textPostAppInfo", {"repost_count": "repostCount", "quote_count": "quoteCount", "reshare_count": "reshareCount"}),
    1: ("text_post_app_info", {"repost_count": "repost_cnt", "quote_count": "quote_cnt", "reshare_count": "reshare_cnt"}),
    2: ("text_post_app_info", {"repost_count": "repost_count", "quote_count": "quote_count", "reshare_count": "reshare_count"}),
    3: ("text_post_app_info", {"repost_count": "repost_count", "quote_count": "quote_count", "reshare_count": "reshare_count"}),
}
FIELDS = ("repost_count", "quote_count", "reshare_count")


def _code(prefix, i):
    return f"{prefix}{i:06d}"


def make_threads(rng, posts):
    """回傳 (各週的 record list, expected, 注入統計)。"""
    weeks = {w: [] for w in range(4)}
    expected, injected = {}, {"missing_reshare_key": 0, "missing_container": 0, "display_string": 0,
                              "same_crawl_duplicate": 0, "null_reshare_observed": 0}
    for i in range(posts):
        code = _code("S5T", i)
        first = rng.randrange(4)
        crawl_weeks = sorted(rng.sample(range(first, 4), rng.randint(1, 4 - first)))
        taken = (WEEK0_MS + first * WEEK_MS) // 1000 + rng.randrange(0, 6 * 24 * 3600)
        repost, quote, shares = rng.randrange(0, 40), rng.randrange(0, 30), rng.choice((0, 0, 0, 2, 15))
        known = {}  # 欄位 -> (crawled_at, 值)：最新一次「有值」的抓取
        for w in crawl_weeks:
            repost += rng.randrange(0, 10)
            quote += rng.randrange(0, 5)
            shares += rng.choice((0, 0, 1, 3))
            crawled = WEEK0_MS + w * WEEK_MS + rng.randrange(1, 6 * 24 * 3600) * 1000
            truth = {"repost_count": repost, "quote_count": quote,
                     "reshare_count": shares or None}  # 分享 0 時畫面沒數字，JSON 是 null
            container, names = NAMES[w]
            info = {names[f]: truth[f] for f in FIELDS}
            unknown = set()
            roll = rng.random()
            if roll < 0.04:
                del info[names["reshare_count"]]
                unknown.add("reshare_count")
                injected["missing_reshare_key"] += 1
            elif roll < 0.06:
                unknown.update(FIELDS)
                injected["missing_container"] += 1
            elif roll < 0.065:
                info[names["repost_count"]] = "1.2萬"
                unknown.add("repost_count")
                injected["display_string"] += 1
            post = {"code": code, "taken_at": taken, "user": {"username": f"sample_user_{i % 37}"},
                    "caption": {"text": f"sample post {i}"}}
            if len(unknown) < 3:
                post[container] = info
            if truth["reshare_count"] is None and "reshare_count" not in unknown:
                injected["null_reshare_observed"] += 1
            rec = {"crawled_at": crawled, "platform": "threads", "post": post}
            weeks[w].append(rec)
            if rng.random() < 0.01:
                weeks[w].append(json.loads(json.dumps(rec)))  # 同一次抓取重複落地
                injected["same_crawl_duplicate"] += 1
            for f in FIELDS:
                if f not in unknown and (f not in known or crawled > known[f][0]):
                    known[f] = (crawled, truth[f])
        expected[code] = {f: known[f][1] for f in FIELDS if f in known}
    for w in weeks:
        rng.shuffle(weeks[w])
    return weeks, expected, injected


def make_reels(rng, reels):
    recs, expected = [], {}
    for i in range(reels):
        code = _code("S5R", i)
        plays = rng.randrange(100_000, 12_000_000)
        crawled = WEEK0_MS + rng.randrange(1, 6 * 24 * 3600) * 1000
        node = {"code": code, "taken_at": crawled // 1000 - 86_400, "user": {"username": "sample_reels"}}
        roll = rng.random()
        if roll < 0.10:
            node["video_view_count"] = plays  # 假設的舊名
            expected[code] = {"video_view_count": plays}
        elif roll < 0.13:
            expected[code] = {}  # 兩個 key 都沒有：不知道
        else:
            node["play_count"] = plays
            expected[code] = {"video_view_count": plays}
        recs.append({"crawled_at": crawled, "platform": "instagram", "post": node})
    return recs, expected


def write_sample(out: Path, posts=2000, reels=200, seed=5, faults=True):
    """寫出原始資料目錄，回傳 expected（同時寫成 out 旁邊的 expected.json）。"""
    rng = random.Random(seed)
    src = out / "src"
    weeks, exp_threads, injected = make_threads(rng, posts)
    reel_recs, exp_reels = make_reels(rng, reels)

    for w in (0, 1):
        d = src / "threads" / WEEKS[w]
        d.mkdir(parents=True, exist_ok=True)
        (d / "batch_0001.json").write_text(json.dumps({"records": weeks[w]}, ensure_ascii=False), encoding="utf-8")
    for w in (2, 3):
        d = src / "threads" / WEEKS[w]
        d.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r, ensure_ascii=False) for r in weeks[w]]
        if faults and w == 3:
            lines.insert(len(lines) // 2, '{"crawled_at": 1779667200000, "post": {"code": "S5TBROKEN"')  # 截斷的行
            lines.append(json.dumps({"crawled_at": "2026-05-25T10:00:00Z", "platform": "threads",
                                     "post": {"code": "S5TISOTIME", "text_post_app_info": {"repost_count": 1}}}))
        (d / "part-0000.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    d = src / "instagram" / WEEKS[0]
    d.mkdir(parents=True, exist_ok=True)
    (d / "reels.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reel_recs),
                                   encoding="utf-8")

    expected = {"threads": exp_threads, "instagram": exp_reels, "injected": injected,
                "faults": {"bad_json_line": int(faults), "bad_crawled_at": int(faults)},
                "records": {"threads": sum(len(v) for v in weeks.values()), "instagram": len(reel_recs)}}
    (out / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1, sort_keys=True),
                                       encoding="utf-8")
    return expected


def main(argv=None):
    ap = argparse.ArgumentParser(description="產生 Q5 回補示範用的合成原始資料")
    ap.add_argument("--out", required=True, type=Path, help="輸出目錄（會建立 src/ 與 expected.json）")
    ap.add_argument("--posts", type=int, default=2000)
    ap.add_argument("--reels", type=int, default=200)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--no-faults", action="store_true", help="不注入壞行與 ISO 時間")
    args = ap.parse_args(argv)
    exp = write_sample(args.out, args.posts, args.reels, args.seed, faults=not args.no_faults)
    print(json.dumps({"records": exp["records"], "injected": exp["injected"], "faults": exp["faults"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
