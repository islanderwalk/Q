#!/usr/bin/env python3
"""QSearch 第 2 題：把中文語料切成「每則 250 字」的貼文檔，給 run_q2.py --mode corpus 用。

頁面與 repeat／distinct 兩種造資料方式都是隨機 CJK 字，字頻是均勻分布；中文語料是齊夫分布，
索引體積會落在兩者之間。這支腳本產生符合實際字頻的內文，一行一則（UTF-8，行內不含換行與 tab）。

用法：
    # 中文維基（Hugging Face wikimedia/wikipedia 20231101.zh 的 parquet，欄位 text）
    python prepare_corpus.py wiki --parquet a.parquet b.parquet --rows 1000000 --out wiki_250.txt
    # PTT 八卦板問答語料（zake7749/Gossiping-Chinese-Corpus，每行「問<TAB>答」）
    python prepare_corpus.py ptt --txt Gossiping-QA-Dataset.txt --rows 1000000 --how recombine --out ptt_250.txt
    python prepare_corpus.py ptt --txt Gossiping-QA-Dataset.txt --how concat --out ptt_concat_250.txt

--how concat     依序把問答接起來切 250 字，不重複使用任何一句（則數受語料大小限制）
--how recombine  每則隨機抽句子接到 250 字（句子會在不同貼文間重複出現，則數不受限）

語料本身不進 repo；RESULTS.md 只記來源網址與 sha256。
"""
import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

POST_CHARS = 250
SEED = 2026
WS_RE = re.compile(r"\s+")


def is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"


def cjk_share(text: str) -> float:
    return sum(is_cjk(ch) for ch in text) / len(text) if text else 0.0


def normalize(text: str) -> str:
    """空白（含換行、tab）一律收成一個半形空白，讓一則貼文剛好是一行。"""
    return WS_RE.sub(" ", text).strip()


def chunks(text: str, size: int = POST_CHARS, min_cjk: float = 0.5, limit: int = 0) -> list:
    """切成不重疊的 size 字片段；不足 size 的尾段丟掉；CJK 比例低於 min_cjk（表格、數字列表）也丟掉。"""
    text = normalize(text)
    out = []
    for i in range(0, len(text) - size + 1, size):
        piece = text[i:i + size]
        if cjk_share(piece) >= min_cjk:
            out.append(piece)
            if limit and len(out) >= limit:
                break
    return out


def ptt_lines(path: Path) -> list:
    """問答語料：每行「問<TAB>答」，答案「沒有資料」視為空。"""
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            q, _, a = line.rstrip("\n").partition("\t")
            text = normalize(q + " " + ("" if a == "沒有資料" else a))
            if text:
                lines.append(text)
    return lines


def concat_posts(lines: list, size: int = POST_CHARS) -> list:
    """依序接起來切 size 字，每一句只用一次。"""
    return chunks(" ".join(lines), size=size, min_cjk=0.0)


def recombine_posts(lines: list, rows: int, size: int = POST_CHARS, seed: int = SEED) -> list:
    """每則隨機抽句子接到 size 字再截斷；固定 seed，結果可重現。"""
    rng = random.Random(seed)
    posts = []
    for _ in range(rows):
        buf, n = [], -1  # n = 已接好的字數（句間各加一個空白，第一句前面沒有）
        while n < size:
            s = lines[rng.randrange(len(lines))]
            buf.append(s)
            n += len(s) + 1
        posts.append(" ".join(buf)[:size])
    return posts


def wiki_posts(parquet_paths: list, rows: int, size: int = POST_CHARS, per_article: int = 8) -> list:
    import pyarrow.parquet as pq  # 只有 wiki 來源需要

    posts = []
    for path in parquet_paths:
        pf = pq.ParquetFile(path)
        for batch in pf.iter_batches(columns=["text"], batch_size=2000):
            for text in batch.column(0).to_pylist():
                posts.extend(chunks(text or "", size=size, limit=per_article))
            if len(posts) >= rows * 1.05:
                break
        if len(posts) >= rows * 1.05:
            break
    return posts


def write_posts(posts: list, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        for p in posts:
            line = p + "\n"
            f.write(line)
            h.update(line.encode("utf-8"))
    n = len(posts)
    return {
        "out": str(out),
        "rows": n,
        "sha256": h.hexdigest(),
        "avg_chars": round(sum(map(len, posts)) / n, 1) if n else 0,
        "avg_cjk_share": round(sum(map(cjk_share, posts)) / n, 3) if n else 0,
        "distinct_rows": len(set(posts)),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="QSearch Q2：中文語料切成 250 字貼文")
    sub = ap.add_subparsers(dest="source", required=True)
    w = sub.add_parser("wiki")
    w.add_argument("--parquet", nargs="+", required=True)
    w.add_argument("--per-article", type=int, default=8, help="每篇條目最多取幾則，避免同主題連續出現")
    p = sub.add_parser("ptt")
    p.add_argument("--txt", required=True)
    p.add_argument("--how", choices=["concat", "recombine"], default="recombine")
    for s in (w, p):
        s.add_argument("--rows", type=int, default=0, help="要幾則（concat 時 0 代表用完語料）")
        s.add_argument("--chars", type=int, default=POST_CHARS)
        s.add_argument("--seed", type=int, default=SEED)
        s.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.source == "wiki":
        if args.rows <= 0:
            sys.exit("wiki 需要 --rows")
        posts = wiki_posts(args.parquet, args.rows, size=args.chars, per_article=args.per_article)
    else:
        lines = ptt_lines(Path(args.txt))
        if args.how == "concat":
            posts = concat_posts(lines, size=args.chars)
        else:
            if args.rows <= 0:
                sys.exit("recombine 需要 --rows")
            posts = recombine_posts(lines, args.rows, size=args.chars, seed=args.seed)
    # 打散順序：線上貼文表裡相鄰列通常來自不同粉絲頁，不該因為來源順序讓相鄰列共用詞而壓縮得特別好
    random.Random(args.seed).shuffle(posts)
    if args.rows:
        if len(posts) < args.rows:
            sys.exit(f"語料只切得出 {len(posts):,} 則，不足 --rows {args.rows:,}")
        posts = posts[:args.rows]
    info = write_posts(posts, Path(args.out))
    info["source"] = args.source
    print(json.dumps(info, ensure_ascii=False))
    return info


if __name__ == "__main__":
    main()
