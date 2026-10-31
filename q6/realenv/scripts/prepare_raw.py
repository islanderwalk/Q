"""建「原文湖」：RAW_ROOT/<channel>/<yyyy-mm>.jsonl（GCS／BigQuery 匯出的本機替身），給回補 Job 用。

來源（都是真實文字）：
1. q6/data/ptt_titles_7d.jsonl：6,685 則 PTT 八卦板標題，時間取自文章代碼 M.<unix 秒>（真實時間）
2. Gossiping-QA-Dataset.txt：PTT 八卦板問答 418,202 組（zake7749/Gossiping-Chinese-Corpus），文字真實；
   語料沒有發文時間，這裡用編號雜湊把它們平均分到 2023-01～2026-09 的 45 個月（時間是模擬的，只用來切分區）
用法：python prepare_raw.py <Gossiping-QA-Dataset.txt> <RAW_ROOT>
"""
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

TW = timezone(timedelta(hours=8))
HERE = Path(__file__).resolve().parent
TITLES = HERE.parent.parent / "data" / "ptt_titles_7d.jsonl"
MONTHS = [f"{y}-{m:02d}" for y in range(2023, 2027) for m in range(1, 13)][:45]   # 2023-01 … 2026-09


def main(qa_path, root):
    root = Path(root)
    parts = defaultdict(list)
    fetched = datetime.now(TW).isoformat(timespec="seconds")
    last_ts = None
    for line in TITLES.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        # 已刪除文章沒有 M.<unix> 代碼（deleted-…），時間沿用列表中前一篇（time_basis=approx）
        basis = "real" if r["doc_id"].startswith("M.") else "approx"
        ts = int(r["doc_id"].split(".")[1]) if basis == "real" else last_ts
        last_ts = ts
        pub = datetime.fromtimestamp(ts, TW)
        parts[("ptt", pub.strftime("%Y-%m"))].append({
            "post_id": f"ptt-{r['doc_id']}", "channel": "ptt", "sub_type": "title", "source": "Gossiping",
            "url": f"https://www.ptt.cc/bbs/Gossiping/{r['doc_id']}.html" if basis == "real" else None, "published_at": pub.isoformat(),
            "fetched_at": fetched, "text": r["text"], "time_basis": basis})
    with open(qa_path, encoding="utf-8") as f:
        for n, line in enumerate(f):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            h = int(hashlib.sha1(f"gqa-{n}".encode()).hexdigest(), 16)
            month = MONTHS[h % len(MONTHS)]
            y, m = map(int, month.split("-"))
            pub = datetime(y, m, 1 + (h >> 8) % 28, (h >> 16) % 24, (h >> 24) % 60, tzinfo=TW)
            q, _, a = line.partition("\t")
            parts[("ptt", month)].append({
                "post_id": f"gqa-{n}", "channel": "ptt", "sub_type": "qa_pair", "source": "Gossiping-QA",
                "url": None, "published_at": pub.isoformat(), "fetched_at": fetched,
                "text": f"{q}\n{a}".strip(), "time_basis": "synthetic"})
    total = 0
    for (ch, month), rows in sorted(parts.items()):
        d = root / ch
        d.mkdir(parents=True, exist_ok=True)
        rows.sort(key=lambda r: r["post_id"])
        with open(d / f"{month}.jsonl", "w", encoding="utf-8") as out:
            for r in rows:
                out.write(json.dumps(r, ensure_ascii=False) + "\n")
        total += len(rows)
    print(json.dumps({"partitions": len(parts), "docs": total,
                      "by_month": {f"{c}/{m}": len(v) for (c, m), v in sorted(parts.items())}}, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
