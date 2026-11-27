"""宣傳句整行移除，以及「同一句話在一批裡出現在太多篇」的檢查。

宣傳句＝新聞台每則貼文都會貼的固定句子（訂閱頻道、下載 App、追蹤社群、節目播出時間…）。
它們不是貼文在講的事，留著會把「訂閱」「下載」「app」頂上文字雲。

規則刻意做得很窄：
  - 清單跟著字典版本走（promo_lines.tsv，manifest 記 sha256），審核後才發版；程式裡沒有寫死任何一句。
  - 只比「整行」：一行去掉網址、壓掉多餘空白後，要和清單裡某一句一字不差，才把那一行拿掉。
    不做「從某句之後全部切掉」，也不做「重複 N 次就自動刪」（真的新聞標題也會重複幾十次）。
  - 清單是空的（或這個渠道沒有規則）時，原文一個字都不動。
  - 每一句都記「從哪一版字典開始收」：定向重跑只找「含這一句、而且斷詞版本比那一版舊」的貼文，
    PM 頁也用同一個條件數「還有幾篇沒清」。清單沒變的後續版本不會再重跑任何一篇。

LineCounter 是給回補關卡用的：它不看清單，只數「清完之後，同一句話還出現在一批裡幾成的貼文」。
超過門檻就是清單漏掉的新宣傳句（或清單根本沒生效），關卡不放行，並把那幾句列出來給人審。
"""
import hashlib
import html
import os
import re

import segmenter as sg

FILE = "promo_lines.tsv"
MIN_WORD_CHARS = 4          # 一句至少要有 4 個中文字或英數字才能進清單、才會被拿來數（太短的句子會誤傷正文）
# 門檻用真資料定的（2026-11-12 量六個渠道全部月份）：5%／50 篇時，沒套清單的現況有 40 批被擋，套了清單後 0 批被擋；
# 2%／20 篇會把小月份的編輯署名、系列標題、話題標籤也擋下來（Instagram 8 批、Facebook 9 批），那些不是宣傳句。
SUSPECT_SHARE = float(os.environ.get("PROMO_SUSPECT_SHARE", "0.05"))      # 同一句出現在一批裡超過這個比例的貼文 → 疑似宣傳句
SUSPECT_MIN_DOCS = int(os.environ.get("PROMO_SUSPECT_MIN_DOCS", "50"))    # 而且至少這麼多篇（小批次不要被三五篇轉貼觸發）
_KEEP_TEXT_AT = 5           # 同一句出現到第 5 篇才記下原文（省記憶體；門檻一定比它高）

_WORD_CHAR = re.compile(r"[0-9A-Za-z一-鿿]")
_WS = re.compile(r"\s+")
_SINCE_RE = re.compile(r"^.+-(\d{4})\.(\d{2})\.(\d{2})\.(\d{1,2})$")     # 字典版本（與 pipeline.seg_ver 吃的格式相同）
_CJK_RUN = re.compile(r"[一-鿿]{2,}")
# 英數字片語：單字中間的撇號算同一個字（don't），與 ES 對英文的切法一致；否則會挑出「t forget to subscribe」這種永遠比不到的片語
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*(?: [A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*)*")


class PromoListError(ValueError):
    """清單格式不對。載入字典時就擋下來，不讓一份壞清單上線。"""


def key(line: str) -> str:
    """比對基準：去掉網址、把連續空白壓成一格、去頭尾空白。
    同一句宣傳句換了活動短網址、或句尾多一個空白，仍然是同一句。"""
    t = sg.URL_RE.sub(" ", line)          # 先拿掉帶 http 的完整網址，才不會留下孤零零的「https://」
    t = sg.SHORT_URL_RE.sub(" ", t)       # 再拿掉沒寫 http 的短網址（bit.ly/…）
    return _WS.sub(" ", t).strip()


def word_chars(k: str) -> int:
    return len(_WORD_CHAR.findall(k))


def rows(lines):
    """promo_lines.tsv 的每一行：「渠道<TAB>句子<TAB>從哪一版字典開始收」。逐行給 (渠道, 比對基準, 版本)。
    空行、以 # 開頭的行是註解（句子本身以 # 開頭沒關係，因為行首是渠道名）。格式不對就丟 PromoListError。"""
    for n, raw in enumerate(lines, 1):
        raw = raw.lstrip("﻿").rstrip("\r\n")     # 用記事本存檔會在檔頭多一個看不見的 BOM；不拿掉的話第一行的渠道名就對不上
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cols = raw.split("\t")
        if len(cols) != 3 or not cols[0].strip():
            raise PromoListError(f"{FILE} 第 {n} 行：要寫成「渠道<TAB>句子<TAB>從哪一版字典開始收」")
        channel, text, since = cols[0].strip(), cols[1], cols[2].strip()
        m = _SINCE_RE.match(since)
        # 月、日也要在範圍內：pipeline.seg_ver 不收 13 月、0 日，這裡放行的話要到定向重跑才會炸
        if not m or not (1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31):
            raise PromoListError(f"{FILE} 第 {n} 行：第三欄要是字典版本（例 qsearch-q6-2026.11.12.1）：{since!r}")
        k = key(text)
        if word_chars(k) < MIN_WORD_CHARS:
            raise PromoListError(f"{FILE} 第 {n} 行：句子去掉網址後不到 {MIN_WORD_CHARS} 個字，太短會誤刪正文：{text!r}")
        yield channel, k, since


def parse(lines) -> dict:
    """清洗用：回傳 {渠道: frozenset(比對基準)}。"""
    rules = {}
    for channel, k, _ in rows(lines):
        rules.setdefault(channel, set()).add(k)
    return {ch: frozenset(v) for ch, v in rules.items()}


def since_map(lines) -> dict:
    """定向重跑用：回傳 {字典版本: {渠道: [比對基準…]}}，每一句歸在「開始收它的那一版」。"""
    out = {}
    for channel, k, since in rows(lines):
        out.setdefault(since, {}).setdefault(channel, []).append(k)
    return out


def probe(k: str) -> str:
    """拿去 ES 找候選貼文的那一段：句子裡最長的一段中文字（至少兩個字）；沒有中文就用最長的一段英數字。
    content.bigram 是「中文兩字一組、英數整個字」的索引，片語比對這一段＝原文含這一段。
    找到的是「可能含這一句」的貼文（比實際多），真正拿不拿掉仍由 strip() 逐行比對決定。"""
    runs = _CJK_RUN.findall(k)
    if runs:
        return max(runs, key=len)
    runs = [r for r in _ASCII_RUN.findall(k) if len(r) >= 3]
    return max(runs, key=len).lower() if runs else ""


def probes(keys) -> list:
    """一組句子要查的片語（去重、排序，結果固定）。挑不出片語的句子不在裡面——呼叫端要自己數出來、寫進日誌。"""
    return sorted({p for p in (probe(k) for k in keys) if p})


def strip(channel: str, text: str, rules: dict):
    """回傳 (拿掉宣傳句之後的原文, 拿掉幾行)。沒有任何一行命中時，回傳的就是原本那個字串。"""
    keys = (rules or {}).get(channel)
    if not keys or not text:
        return text, 0
    kept, removed = [], 0
    for line in text.splitlines():
        if key(line) in keys:
            removed += 1
        else:
            kept.append(line)
    if not removed:
        return text, 0
    return "\n".join(kept), removed


def _digest(k: str) -> bytes:
    return hashlib.blake2b(k.encode("utf-8"), digest_size=8).digest()


class LineCounter:
    """數「同一句話出現在幾篇貼文」。一篇裡重複出現只算一次；只存 8 位元組的摘要，原文出現夠多次才留。"""

    def __init__(self):
        self.docs = 0
        self._count = {}
        self._text = {}

    def add(self, channel: str, text: str, rules: dict = None) -> None:
        """丟進一篇貼文的原文。先照清單拿掉宣傳句（與清洗同一個函式），剩下的每一句各記一次。"""
        self.docs += 1
        cleaned, _ = strip(channel, html.unescape(text or ""), rules)
        seen = set()
        for line in cleaned.splitlines():
            k = key(line)
            if word_chars(k) < MIN_WORD_CHARS:
                continue
            d = _digest(k)
            if d in seen:
                continue
            seen.add(d)
            c = self._count.get(d, 0) + 1
            self._count[d] = c
            if c == _KEEP_TEXT_AT:
                self._text[d] = k

    def suspects(self, share: float = None, min_docs: int = None, limit: int = 20) -> list:
        """出現在太多篇的句子，多的在前：[{"line", "docs", "share"}]。"""
        share = SUSPECT_SHARE if share is None else share
        min_docs = SUSPECT_MIN_DOCS if min_docs is None else min_docs
        floor = max(min_docs, share * self.docs)
        out = [{"line": self._text.get(d, ""), "docs": c, "share": round(c / self.docs, 4)}
               for d, c in self._count.items() if c >= floor and c >= _KEEP_TEXT_AT]
        out.sort(key=lambda r: (-r["docs"], r["line"]))
        return out[:limit]


def residue(suspects: list) -> int:
    """關卡用的一個數字：最常重複的那一句出現在幾篇（沒有疑似宣傳句就是 0）。"""
    return max((s["docs"] for s in suspects), default=0)
