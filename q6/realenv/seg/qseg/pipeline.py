"""一篇文章 → 詞表。v1 = 舊流程（原文、純字典、HMM 關、不過濾）；v2 = 新流程（渠道清洗 → jieba＋HMM → 過濾）。
回傳去重後的詞（保留首次出現順序）：ES terms 聚合算的是「有幾篇文章提到」，同一篇重複 10 次仍只算 1 篇，
存重複值對文字雲沒有幫助（筆記 §3.4）。"""
import re
from datetime import datetime, timezone

import segmenter as sg

from . import config
from .normalize import normalize

ASCII_RE = re.compile(r"^[A-Za-z0-9]+$")


def seg_ver(pipeline: str, dict_version: str) -> int:
    """應用程式自訂的斷詞版本（≠ ES 的 _version）：演算法主版本 × 10^10 + 字典日期序號。
    例：v2 + qsearch-q6-2026.10.30.2 → 22026103002。數字越大越新，寫入只能往前。"""
    # 字典版本固定格式 YYYY.MM.DD.N（N 是當天第幾版，< 100）。逐段解析再補零：
    # 直接把數字串起來的話，2026.10.30.10 → 2026103010 會比 2026.11.01.1 → 202611011 還「大」，版本順序就錯了
    y, m, d, n = (int(x) for x in dict_version.split("-")[-1].split("."))
    if not (1 <= m <= 12 and 1 <= d <= 31 and 0 <= n < 100):
        raise ValueError(f"字典版本格式不對：{dict_version}")
    return config.ALGO[pipeline] * 10 ** 10 + ((y * 100 + m) * 100 + d) * 100 + n


def _dedupe(tokens):
    seen, out = set(), []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def must_keep_split(text: str, toks: list, words) -> list:
    """必不可拆詞被切開了嗎？（看斷點位置，不是只看「有沒有一個詞包含它」）

    允許：詞的起點是斷點，且最後一個內部斷點開始的那個詞往右跨出去（交集型歧義，例如「台北市長」→ 台北／市長、
    「台中市民」→ 台中／市民，這是對的）。
    不允許：左邊的詞吃進來（「辦好市多」→ 辦好／市多），或整個切在詞裡面（「鳳山區」→ 鳳山／區）。"""
    hits = [w for w in words if w in text]
    if not hits:
        return []
    starts = {s for _, s, _ in toks}
    tok_at = {s: e for _, s, e in toks}
    bad = []
    for w in hits:
        i = text.find(w)
        while i != -1:
            j = i + len(w)
            internal = [b for b in starts if i < b < j]
            if internal and not (i in starts and tok_at[max(internal)] > j):
                bad.append(w)
                break
            i = text.find(w, i + 1)
    return bad


def segment_checked(dic, channel: str, text: str):
    """回傳 (詞表, 被切開的必不可拆詞)。v1 不檢查。"""
    if dic.pipeline == "v1":
        return _dedupe(t for t in (x.strip() for x in dic.tokenizer.lcut(text or "", HMM=False)) if t), []
    clean = normalize(channel, text)
    toks = list(dic.tokenizer.tokenize(clean, HMM=True))
    out = []
    for t, _, _ in toks:
        t = t.strip()
        if ASCII_RE.match(t):
            t = t.lower()
        if sg._keep_new(t, dic.stopwords):
            out.append(t)
    return _dedupe(out), must_keep_split(clean, toks, dic.user_words)


def segment(dic, channel: str, text: str) -> list:
    return segment_checked(dic, channel, text)[0]


def month_of(published_at: str) -> str:
    return (published_at or "")[:7] or "unknown"


def base_doc(post: dict) -> dict:
    """文章本體欄位（parser 寫入的部分）。斷詞欄位不在這裡，由各版斷詞服務各寫各的。"""
    keys = ("post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at")
    doc = {k: post.get(k) for k in keys}
    doc["content"] = post.get("text") or post.get("content") or ""
    doc["month"] = month_of(post.get("published_at"))
    return doc


def seg_fields(dic, terms: list) -> dict:
    f = dic.pipeline
    return {
        f"terms_{f}": terms,
        f"seg_ver_{f}": seg_ver(f, dic.version),
        f"dict_version_{f}": dic.version,
        f"seg_build_{f}": config.SEG_BUILD,
        f"seg_at_{f}": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
