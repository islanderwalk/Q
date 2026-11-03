"""QSearch 第 6 題：文字雲斷詞 pipeline 原型（新舊並行輸出）。

流程（新）：前處理 → jieba（使用者字典＋HMM）→ 停用詞／長度／純數字過濾 → 輸出 tokens
流程（舊）：jieba 純字典（HMM 關閉，不做前處理、不過濾）→ 輸出 tokens
          ※ 舊團隊的字典檔不在 repo 內，這裡用 jieba 內建字典模擬「純字典斷詞」。

基礎字典：預設是 jieba 內建字典（簡體為主）；build(base_dict=...) 可改用其他主字典
          （例如繁體 dict.txt.big），新舊兩個 Tokenizer 用同一份，差異才只來自流程。
前處理：PTT 標題的方括號只去掉「分類標籤」（dict/ptt_tags.txt 白名單）與「帳號」（純英數，刪文標題的
          「(本文已被刪除) [帳號]」）；其他中文方括號內容保留，例如「[高雄市]今天下雨」的「高雄市」。
單字詞：預設過濾，但 dict/keep_single.txt 列的單字（癌、稅…）保留。
版本：
  dict_version   使用者字典第一行 `# dict_version: ...`
  seg_fingerprint 斷詞「規則全部輸入」的 sha256：jieba 版本、主字典、使用者字典、停用詞、PTT 標籤表、
                  保留單字表、固定的 total、HMM 開關、這個檔案的原始碼。任何一項變了，指紋就變；
                  只看 dict_version 或使用者字典 sha256 會出現「版本相同、輸出不同」。
  seg_build      `<dict_version>@<fingerprint 前 16 碼>`，每筆輸出都帶；內容雜湊 content_sha 另記，
                  同一版斷詞下原文改了也看得出來（版本比大小、內容比雜湊，兩者分開）。
影響範圍：新字典多出的詞只需重算「含該詞字元序列」的文件，但前提是 total 固定（pin_total）；
          jieba 的 add_word 會累加 total，所有詞的分數都跟著變，不固定時不含新詞的文件也會變
          （20 萬篇實測 52 篇，固定後 0 篇，見 q6/code/RESULTS.md）。
"""
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import jieba

HERE = Path(__file__).resolve().parent
DICT_DIR = HERE / "dict"
STOPWORDS_PATH = DICT_DIR / "stopwords.txt"
USER_DICT_PATH = DICT_DIR / "user_dict.txt"
PTT_TAGS_PATH = DICT_DIR / "ptt_tags.txt"
KEEP_SINGLE_PATH = DICT_DIR / "keep_single.txt"

# ---- 前處理 -----------------------------------------------------------------
SHORT_URL_RE = re.compile(
    r"\b(?:bit\.ly|t\.co|goo\.gl|tinyurl\.com|reurl\.cc|lihi\d?\.com|pse\.is|xurl\.es|ppt\.cc|youtu\.be)/\S*",
    re.I,
)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 台灣手機：09xx-xxx-xxx、09xxxxxxxx；市話：0x-xxxxxxx、0xx-xxxxxxxx；國際碼 +886
MOBILE_RE = re.compile(r"(?<!\d)09\d{2}[-\s]?\d{3}[-\s]?\d{3}(?!\d)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?886[-\s]?\d{1,2}|0\d{1,2})[-\s]?\d{3,4}[-\s]?\d{3,4}(?!\d)")
# PTT 標題的方括號：[問卦]、[新聞] 這類分類標籤，以及刪文標題「(本文已被刪除) [帳號]」的帳號
BRACKET_RE = re.compile(r"\[([^\[\]]{1,12})\]")
ASCII_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")   # PTT 帳號只有英數
REPLY_PREFIX_RE = re.compile(r"(?:^|\s)(?:Re|RE|Fw|FW):\s*")
CJK_OR_ALNUM_RE = re.compile(r"[一-鿿㐀-䶿A-Za-z]")
PURE_NUMBER_RE = re.compile(r"^[\d\W_]+$")


def load_lines(path: Path) -> frozenset:
    """每行一個詞，去掉空行與 # 註解行。"""
    words = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.add(line)
    return frozenset(words)


def load_stopwords(path: Path = STOPWORDS_PATH) -> frozenset:
    return load_lines(path)


def load_ptt_tags(path: Path = PTT_TAGS_PATH) -> frozenset:
    return load_lines(path)


def load_keep_single(path: Path = KEEP_SINGLE_PATH) -> frozenset:
    return load_lines(path)


_DEFAULT_TAGS = None


def default_ptt_tags() -> frozenset:
    global _DEFAULT_TAGS
    if _DEFAULT_TAGS is None:
        _DEFAULT_TAGS = load_ptt_tags()
    return _DEFAULT_TAGS


def preprocess(text: str, tags: frozenset = None, bracket_mode: str = "tags") -> str:
    """去 PTT 分類標籤與帳號、回覆前綴、短網址、網址、電子郵件、電話號碼；壓縮空白。

    bracket_mode="tags"（預設）：方括號裡是白名單標籤（[問卦]）或純英數帳號（[ostracize]）才整個去掉，
    其他內容只拆掉括號、文字保留（「[高雄市]今天下雨」→「高雄市 今天下雨」）。
    bracket_mode="all"：10-30 以前的規則，1–8 字的方括號一律去掉（會把「[高雄市]」當成標籤刪掉；
    只留給 recall_eval.py 對照用）。"""
    tags = default_ptt_tags() if tags is None else tags

    def _bracket(m):
        inner = m.group(1).strip()
        if bracket_mode == "all":
            return " " if len(inner) <= 8 else m.group(0)
        if inner in tags or ASCII_ID_RE.match(inner):
            return " "
        return f" {inner} "

    t = BRACKET_RE.sub(_bracket, text or "")
    t = REPLY_PREFIX_RE.sub(" ", t)
    t = SHORT_URL_RE.sub(" ", t)
    t = URL_RE.sub(" ", t)
    t = EMAIL_RE.sub(" ", t)
    t = MOBILE_RE.sub(" ", t)
    t = PHONE_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def read_dict_version(path: Path = USER_DICT_PATH) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# dict_version:"):
            return line.split(":", 1)[1].strip()
    raise ValueError("user_dict.txt 缺少 dict_version 標頭")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dict_content_hash(path: Path = USER_DICT_PATH) -> str:
    return sha256_file(path)[:16]


def content_sha(text: str) -> str:
    """原文的雜湊（前 16 碼）：同一版斷詞下，原文改了才要重算；版本比大小、內容比雜湊。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def user_dict_lines(path: Path = USER_DICT_PATH) -> list:
    """使用者字典的詞條行（去掉空行與 # 註解行）。"""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def load_user_dict(tk: jieba.Tokenizer, path: Path = USER_DICT_PATH) -> None:
    """jieba 的 load_userdict 不認得 # 註解，會把整行註解當成一個詞載入（total 也跟著變），
    所以先濾掉註解行再交給 jieba。"""
    tk.load_userdict(io.StringIO("\n".join(user_dict_lines(path))))


def user_dict_terms(path: Path = USER_DICT_PATH) -> set:
    return {line.split()[0] for line in user_dict_lines(path)}


BUILTIN_BASE = "jieba-builtin"
_BUILTIN_HASH = None


def builtin_dict_hash() -> str:
    """jieba 內建 dict.txt 的 sha256（指紋用；內建字典也會隨 jieba 版本改變）。"""
    global _BUILTIN_HASH
    if _BUILTIN_HASH is None:
        with jieba.get_dict_file() as fp:
            _BUILTIN_HASH = hashlib.sha256(fp.read()).hexdigest()
    return _BUILTIN_HASH


def _tokenizer(base_dict: Path = None) -> jieba.Tokenizer:
    """base_dict=None 用 jieba 內建字典；否則用指定的主字典檔（格式同 jieba dict.txt）。"""
    return jieba.Tokenizer(dictionary=str(base_dict)) if base_dict else jieba.Tokenizer()


def _sorted_sha(words) -> str:
    return hashlib.sha256("\n".join(sorted(words)).encode("utf-8")).hexdigest()


@dataclass
class Segmenters:
    """新舊兩個 Tokenizer 各自獨立，避免 jieba 全域字典互相污染。"""
    new: jieba.Tokenizer = field(default=None)
    legacy: jieba.Tokenizer = field(default=None)
    stopwords: frozenset = field(default_factory=load_stopwords)
    ptt_tags: frozenset = field(default_factory=load_ptt_tags)
    keep_single: frozenset = field(default_factory=load_keep_single)
    dict_version: str = ""
    dict_hash: str = ""
    base_dict: str = BUILTIN_BASE   # 主字典檔名（內建則為 jieba-builtin）
    base_dict_hash: str = ""        # 主字典 sha256 前 16 碼（內建則留空）
    base_dict_sha256: str = ""      # 主字典完整 sha256（內建字典也算，指紋用）
    user_dict_sha256: str = ""
    total_pinned: int = 0           # 0 表示沒有固定（照 jieba 預設累加）
    fingerprint: str = ""
    seg_build: str = ""

    @classmethod
    def build(cls, user_dict: Path = USER_DICT_PATH, base_dict: Path = None, stopwords: Path = STOPWORDS_PATH,
              ptt_tags: Path = PTT_TAGS_PATH, keep_single: Path = KEEP_SINGLE_PATH,
              pin_total: bool = True) -> "Segmenters":
        legacy = _tokenizer(base_dict)  # 純字典：不載入使用者字典
        legacy.initialize()
        new = _tokenizer(base_dict)
        load_user_dict(new, user_dict)
        total_pinned = 0
        if pin_total:
            # 發版時固定 total：加詞只改含該詞的文件（q6/code/RESULTS.md：不固定時 52 篇不含新詞也變）
            new.total = legacy.total
            total_pinned = legacy.total
        seg = cls(new=new, legacy=legacy, stopwords=load_stopwords(stopwords), ptt_tags=load_ptt_tags(ptt_tags),
                  keep_single=load_keep_single(keep_single),
                  dict_version=read_dict_version(user_dict), dict_hash=dict_content_hash(user_dict),
                  base_dict=Path(base_dict).name if base_dict else BUILTIN_BASE,
                  base_dict_hash=dict_content_hash(Path(base_dict)) if base_dict else "",
                  base_dict_sha256=sha256_file(Path(base_dict)) if base_dict else builtin_dict_hash(),
                  user_dict_sha256=sha256_file(user_dict), total_pinned=total_pinned)
        seg.fingerprint = seg_fingerprint(seg)
        seg.seg_build = f"{seg.dict_version}@{seg.fingerprint}"
        return seg


def fingerprint_inputs(seg: "Segmenters") -> dict:
    """指紋涵蓋的每一項輸入；缺任何一項都可能「版本相同、輸出不同」。"""
    return {
        "jieba": jieba.__version__,
        "hmm": True,
        "base_dict": seg.base_dict_sha256,
        "user_dict": seg.user_dict_sha256,
        "stopwords": _sorted_sha(seg.stopwords),
        "ptt_tags": _sorted_sha(seg.ptt_tags),
        "keep_single": _sorted_sha(seg.keep_single),
        "total_pinned": seg.total_pinned,
        "code": sha256_file(Path(__file__)),
    }


def seg_fingerprint(seg: "Segmenters") -> str:
    payload = json.dumps(fingerprint_inputs(seg), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _keep_new(tok: str, stop: frozenset, keep_single: frozenset = frozenset()) -> bool:
    if not tok or tok in stop:
        return False
    if len(tok) < 2 and tok not in keep_single:
        return False
    if PURE_NUMBER_RE.match(tok) or not CJK_OR_ALNUM_RE.search(tok):
        return False
    return True


def segment_new(seg: Segmenters, text: str) -> list:
    clean = preprocess(text, seg.ptt_tags)
    return [t for t in seg.new.lcut(clean, HMM=True) if _keep_new(t.strip(), seg.stopwords, seg.keep_single)]


def segment_legacy(seg: Segmenters, text: str) -> list:
    """舊流程：原文直接純字典斷詞（不做前處理、不過濾停用詞）；只去掉純空白。"""
    return [t for t in seg.legacy.lcut(text or "", HMM=False) if t.strip()]


def dual_output(seg: Segmenters, doc_id: str, text: str) -> dict:
    """A/B 並行輸出：同一筆文件同時給新舊 tokens，並記錄字典版本、斷詞指紋與原文雜湊。"""
    return {
        "doc_id": doc_id,
        "dict_version": seg.dict_version,
        "dict_hash": seg.dict_hash,
        "seg_build": seg.seg_build,
        "content_sha": content_sha(text),
        "seg_legacy": segment_legacy(seg, text),
        "seg_new": segment_new(seg, text),
    }


# ---- 字典版本與影響範圍 ----------------------------------------------------
def new_terms(old_terms: set, new_terms_: set) -> set:
    return set(new_terms_) - set(old_terms)


def _bigrams(s: str):
    return {s[i:i + 2] for i in range(len(s) - 1)}


class BigramIndex:
    """以字元 bigram 建倒排索引，快速找出可能含某詞的文件（再以子字串驗證）。

    這是記憶體內的示範（6,685 篇）。5 億篇的版本不另建索引：ES 的 `content.bigram` 欄位（cjk_bigram）
    就是同一個倒排索引，用 `match_phrase` 找「原文含這個字串」的文章，再用 PIT＋search_after 分頁重算
    （q6/stack/seg/qseg/rerun.py；42.5 萬篇實測 22,271 篇 8.6 秒）。"""

    def __init__(self, docs: dict, tags: frozenset = None):
        self.docs = docs
        self.tags = tags
        self.post = {}
        for doc_id, text in docs.items():
            for bg in _bigrams(preprocess(text, tags)):
                self.post.setdefault(bg, set()).add(doc_id)

    def docs_containing(self, term: str) -> set:
        if len(term) < 2:
            return {d for d, t in self.docs.items() if term in t}
        cand = None
        for bg in _bigrams(term):
            ids = self.post.get(bg, set())
            cand = set(ids) if cand is None else cand & ids
            if not cand:
                return set()
        return {d for d in cand if term in preprocess(self.docs[d], self.tags)}


def affected_doc_ids(index: BigramIndex, added_terms: set) -> set:
    """字典新增的詞，要重算哪些文件：含該詞字元序列的文件。

    這只在 total 固定時成立（Segmenters.build 的 pin_total，預設開）。jieba 的 add_word 會把新詞頻累加到
    total，所有詞的分數 log(freq/total) 都跟著變，不含新詞的文件也可能改切法：20 萬篇 PTT 問答加 1 個詞，
    預設累加時 52 篇不含該詞也變了，固定 total 後 0 篇（q6/code/RESULTS.md「jieba 實驗重跑」）。
    固定 total 之後，不含該字串的文件 DAG 與分數完全相同，切法不會變；含該字串的文件才需要重算。"""
    out = set()
    for term in added_terms:
        out |= index.docs_containing(term)
    return out
