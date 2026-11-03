"""v0「舊系統替身」：老化字典 ＋ 逆向最長匹配（BMM），不清洗、不過濾、不做網址／電話／標籤前處理，只丟標點。

這是依題目線索推論出來的舊流程重現（字典檔斷詞、字典多年未更新），不是公司真正的程式；
字典的組成與凍結日期寫在 artifact 的 manifest（dict/build_legacy.py），頁面上標示為替身。

規則（刻意保留舊流程的缺陷，不「順手修好」）：
- 中文連續段落用字典做逆向最長匹配，字典沒有的就退回單字 → 「高雄市」在字典沒有「高雄」時被切成 高／雄／市
- 英數字連續段落當一個 token，不切、不小寫 → 短網址後綴（bit.ly/3xYz → https／bit／ly／3xYz）與電話號碼都成詞
- 沒有停用詞表 → 的、了、在 都進詞表
- 標點、空白、表情符號不是中文也不是英數字，直接略過（舊系統的 tokenizer 也不會把逗號當詞）
"""
import re

CJK = "㐀-䶿一-鿿"
RUN_RE = re.compile(rf"[A-Za-z0-9]+|[{CJK}]+")
CJK_RE = re.compile(rf"^[{CJK}]+$")
DEFAULT_MAXLEN = 8


def load_words(path, maxlen: int = DEFAULT_MAXLEN) -> frozenset:
    """一行一詞（# 開頭是註解）。只收 2 到 maxlen 字的純中文詞：單字本來就是退路，不必進字典。"""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            w = line.split()[0] if line.split() else ""
            if w and not w.startswith("#") and 2 <= len(w) <= maxlen and CJK_RE.match(w):
                out.add(w)
    return frozenset(out)


def mm_tokenize(text: str, words, maxlen: int = DEFAULT_MAXLEN, backward: bool = True):
    """回傳 [(token, start, end)]。backward=True 是逆向最長匹配（BMM），False 是正向（FMM）。"""
    out = []
    for m in RUN_RE.finditer(text or ""):
        seg, s = m.group(), m.start()
        if not CJK_RE.match(seg):
            out.append((seg, s, m.end()))
            continue
        n = len(seg)
        if backward:
            j, tmp = n, []
            while j > 0:
                for L in range(min(maxlen, j), 0, -1):
                    if L == 1 or seg[j - L:j] in words:
                        tmp.append((seg[j - L:j], s + j - L, s + j))
                        j -= L
                        break
            out.extend(reversed(tmp))
        else:
            i = 0
            while i < n:
                for L in range(min(maxlen, n - i), 0, -1):
                    if L == 1 or seg[i:i + L] in words:
                        out.append((seg[i:i + L], s + i, s + i + L))
                        i += L
                        break
    return out


def tokens(text: str, words, maxlen: int = DEFAULT_MAXLEN, backward: bool = True) -> list:
    """去重後的詞（保留首次出現順序），與 pipeline.py 的 v1／v2 一樣：terms 聚合算的是文章數。"""
    seen, out = set(), []
    for t, _, _ in mm_tokenize(text, words, maxlen, backward):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
