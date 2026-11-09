"""一篇文章 → 詞表。v1 = 舊流程（原文、純字典、HMM 關、不過濾）；v2 = 新流程（渠道清洗 → jieba＋HMM → 過濾）。
回傳去重後的詞（保留首次出現順序）：ES terms 聚合算的是「有幾篇文章提到」，同一篇重複 10 次仍只算 1 篇，
存重複值對文字雲沒有幫助（筆記 §3.4）。"""
import hashlib
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


def _bounds(toks: list):
    return {s for _, s, _ in toks}, {s: e for _, s, e in toks}


def occurrence_kind(i: int, j: int, starts: set, tok_at: dict, in_dict=None) -> str:
    """原文 [i, j) 這一處被怎麼切（must_keep_split 與品質日報共用同一條規則）：
    whole＝剛好一個詞；inside＝整段在更長的詞裡面、沒有內部斷點（「台北市立」）；
    overlap＝交集型歧義，允許：起點是斷點、最後一段往右跨出去（「台北市長」→ 台北／市長）；
      或剛好跨在兩個詞中間、兩個詞各往外跨出去，而且兩個都是字典裡的詞（鏈狀交集型歧義，「大台北市區」→ 大台北／市區）。
      in_dict(起, 迄) 判斷那一段是不是字典詞，沒給就不放行後面這一種；
    split＝不允許：左邊的詞吃進來、右邊只剩碎片（辦好／市多），兩邊往外跨但有一邊不是字典詞（jieba 用 HMM 猜的，搶輝／達來），
      碎成三段以上，或整個切在詞裡面（鳳山／區）。"""
    internal = [b for b in starts if i < b < j]
    if not internal:
        return "whole" if i in starts and tok_at[i] == j else "inside"
    last = max(internal)
    if tok_at[last] > j:
        if i in starts:
            return "overlap"
        left = max((s for s in starts if s < i), default=None)
        if (len(internal) == 1 and in_dict is not None and left is not None and tok_at[left] == last
                and in_dict(left, last) and in_dict(last, tok_at[last])):
            return "overlap"
    return "split"


def dict_freq(dic):
    """斷詞器的詞頻表（jieba FREQ：詞 → 詞頻，字首補 0），給 occurrence_kind 判斷字典詞；沒有斷詞器（v0、測試的假字典）回 None。"""
    return getattr(getattr(dic, "tokenizer", None), "FREQ", None)


def _in_dict(text: str, freq):
    # 詞頻 > 0 才算字典詞：HMM 猜出來的詞不在 FREQ，字首只補 0
    return None if freq is None else (lambda s, e: freq.get(text[s:e], 0) > 0)


def occurrences(text: str, toks: list, word: str, freq=None) -> list:
    """word 在 text 每一處的 (起點, 被怎麼切)。freq：斷詞器的詞頻表（dict_freq）。"""
    starts, tok_at = _bounds(toks)
    in_dict = _in_dict(text, freq)
    out, i = [], text.find(word)
    while i != -1:
        out.append((i, occurrence_kind(i, i + len(word), starts, tok_at, in_dict)))
        i = text.find(word, i + 1)
    return out


def must_keep_split(text: str, toks: list, words, freq=None) -> list:
    """必不可拆詞被切開了嗎？（看斷點位置，不是只看「有沒有一個詞包含它」）

    允許：詞的起點是斷點，且最後一個內部斷點開始的那個詞往右跨出去（交集型歧義，例如「台北市長」→ 台北／市長、
    「台中市民」→ 台中／市民，這是對的）；或剛好跨在兩個字典詞中間、兩邊都往外跨（「大台北市區」→ 大台北／市區）。
    不允許：左邊的詞吃進來（「辦好市多」→ 辦好／市多）、跨在 HMM 猜出來的詞上（「搶輝達來」→ 搶輝／達來），
    或整個切在詞裡面（「鳳山區」→ 鳳山／區）。任何一處是 split 就算這個詞被切開（occurrence_kind）。
    freq：斷詞器的詞頻表（dict_freq）；沒給的話跨在兩個詞中間一律算切開。"""
    hits = [w for w in words if w in text]
    if not hits:
        return []
    starts, tok_at = _bounds(toks)
    in_dict = _in_dict(text, freq)
    bad = []
    for w in hits:
        i = text.find(w)
        while i != -1:
            if occurrence_kind(i, i + len(w), starts, tok_at, in_dict) == "split":
                bad.append(w)
                break
            i = text.find(w, i + 1)
    return bad


def segment_detail(dic, channel: str, text: str):
    """v2 斷詞的完整過程：(清洗後原文, jieba 斷點 [(詞, 起, 迄)], 詞表, 被切開的必不可拆詞)。
    品質日報要看「切在哪裡」才用這個；一般斷詞走 segment_checked。"""
    clean = normalize(channel, text)
    toks = list(dic.tokenizer.tokenize(clean, HMM=True))
    out = []
    for t, _, _ in toks:
        t = t.strip()
        if ASCII_RE.match(t):
            t = t.lower()
        if sg._keep_new(t, dic.stopwords):
            out.append(t)
    return clean, toks, _dedupe(out), must_keep_split(clean, toks, dic.user_words, dict_freq(dic))


def segment_checked(dic, channel: str, text: str):
    """回傳 (詞表, 被切開的必不可拆詞)。v0／v1 不檢查。"""
    if dic.pipeline == "v0":
        from . import legacy
        return legacy.tokens(text or "", dic.legacy_words, dic.legacy_maxlen), []
    if dic.pipeline == "v1":
        return _dedupe(t for t in (x.strip() for x in dic.tokenizer.lcut(text or "", HMM=False)) if t), []
    _, _, terms, bad = segment_detail(dic, channel, text)
    return terms, bad


def segment(dic, channel: str, text: str) -> list:
    return segment_checked(dic, channel, text)[0]


def month_of(published_at: str) -> str:
    return (published_at or "")[:7] or "unknown"


def base_doc(post: dict) -> dict:
    """文章本體欄位（parser 寫入的部分）。斷詞欄位不在這裡，由各版斷詞服務各寫各的。"""
    keys = ("post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at", "author")
    doc = {k: post.get(k) for k in keys}
    doc["content"] = post.get("text") or post.get("content") or ""
    doc["content_sha"] = hashlib.sha256(doc["content"].encode("utf-8")).hexdigest()[:16]   # 同一版斷詞下原文改了也看得出來（api/ops.py 同一定義）
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
