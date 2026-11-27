"""PM 頁「資料工程端」的實際操作，給 app.py 與 collector.py 用；不依賴 fastapi，可單獨測（q6/stack/tests）。

- 字典發版：從 artifact 的 current 版複製一份、加詞、逐檔算 sha256、寫 manifest，再原子切換 current
  （GCS 上就是改 current 物件）。不需要 dict.txt.big：主字典就在 current 版的 base.dict 裡。
- 控制訊息：發到 Pub/Sub 主題 dict-releases，斷詞服務 v2 收到後重載字典並定向重跑（seg/qseg/control.py）。
- 串流：把一篇貼文（渠道樣本重播，或收集器抓到的新文）當 parser 的輸出送進 parsed-posts，
  同時把文章本體寫進 ES（parser 自己那一段）。
"""
import hashlib
import json
import os
import re
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TW = timezone(timedelta(hours=8))
ALGO = {"v0": 0, "v1": 1, "v2": 2}
VER_RE = re.compile(r"^(?P<prefix>.+)-(?P<y>\d{4})\.(?P<m>\d{2})\.(?P<d>\d{2})\.(?P<n>\d{1,2})$")
WORD_RE = re.compile(r"^[A-Za-z0-9㐀-䶿一-鿿豈-﫿]{1,20}$")
CHANNELS = ("facebook", "instagram", "youtube", "ptt", "media", "threads")
BASE_KEYS = ("post_id", "channel", "sub_type", "source", "url", "published_at", "fetched_at", "author")
DICT_FILES = ("base.dict", "user.dict", "stopwords.txt", "soft_stopwords.txt", "merges.tsv", "synonyms.txt", "ik_ext.dic", "ik_stop.dic")
PROMO_FILE = "promo_lines.tsv"       # 宣傳句清單（seg/qseg/promo.py）：有的版本才有，PM 頁發版時原封不動帶到下一版
OPTIONAL_FILES = (PROMO_FILE,)
# 下面四條要與斷詞那邊一字不差（q6/code/segmenter.py 的 URL_RE、SHORT_URL_RE；seg/qseg/promo.py 的 _CJK_RUN、_ASCII_RUN）。
# 這支檔只用標準函式庫、不能 import 斷詞程式，所以照抄一份；tests/test_promo.py 逐句比對兩邊挑出來的片語。
_PROMO_URL = re.compile(r"(?:https?://|www\.)\S+", re.I)
_PROMO_SHORT = re.compile(
    r"\b(?:bit\.ly|t\.co|goo\.gl|tinyurl\.com|reurl\.cc|lihi\d?\.com|pse\.is|xurl\.es|ppt\.cc|youtu\.be)/\S*", re.I)
_PROMO_CJK = re.compile(r"[一-鿿]{2,}")
_PROMO_WORD = re.compile(r"[0-9A-Za-z一-鿿]")
PROMO_MIN_WORD_CHARS = 4
_PROMO_SINCE = re.compile(r"^.+-(\d{4})\.(\d{2})\.(\d{2})\.(\d{1,2})$")   # 與 seg/qseg/promo.py 的 _SINCE_RE 同一條規則
_PROMO_ASCII = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*(?: [A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*)*")


def now_iso() -> str:
    return datetime.now(TW).isoformat(timespec="seconds")


# ---------- 版本 ----------
def seg_ver(pipeline: str, dict_version: str) -> int:
    """與 seg/qseg/pipeline.py 同一個算法：演算法主版本 × 10^10 ＋ 字典日期序號。"""
    y, m, d, n = (int(x) for x in dict_version.split("-")[-1].split("."))
    if not (1 <= m <= 12 and 1 <= d <= 31 and 0 <= n < 100):
        raise ValueError(f"字典版本格式不對：{dict_version}")
    return ALGO[pipeline] * 10 ** 10 + ((y * 100 + m) * 100 + d) * 100 + n


def parse_version(v: str):
    m = VER_RE.match(v or "")
    if not m:
        raise ValueError(f"字典版本格式不對：{v}")
    return m["prefix"], int(m["y"]), int(m["m"]), int(m["d"]), int(m["n"])


def next_version(current: str, today: date = None) -> str:
    """同一天就把序號加一；換了日期就從 .1 開始。永遠比 current 大（seg_ver 只能前進）。"""
    prefix, y, m, d, n = parse_version(current)
    today = today or datetime.now(TW).date()
    if today > date(y, m, d):
        return f"{prefix}-{today.year}.{today.month:02d}.{today.day:02d}.1"
    if n + 1 >= 100:
        raise ValueError("同一天的字典版本序號用完（上限 99）")
    return f"{prefix}-{y}.{m:02d}.{d:02d}.{n + 1}"


# ---------- 字典 artifact ----------
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(root: Path, version: str = "current") -> dict:
    p = (root / "current" if version == "current" else root / "versions" / version) / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8"))


def current_version(root: Path) -> str:
    return read_manifest(root)["dict_version"]


def list_versions(root: Path) -> list:
    out = []
    for d in sorted((root / "versions").iterdir()):
        mp = d / "manifest.json"
        if mp.exists():
            m = json.loads(mp.read_text(encoding="utf-8"))
            out.append({"dict_version": m["dict_version"], "seg_ver": seg_ver("v2", m["dict_version"]), "note": m.get("note", ""),
                        "user_words": m.get("user_words"), "built_at": m.get("built_at"), "extends": m.get("extends"),
                        "add_words": m.get("add_words", []), "requested_by": m.get("requested_by")})
    return sorted(out, key=lambda x: x["seg_ver"])


def promo_probe(sentence: str) -> str:
    """與 seg/qseg/promo.py 的 probe 同一個挑法（tests/test_promo.py 逐句比對兩邊結果）：
    最長的一段中文字（至少兩個字）；沒有中文就用最長的一段英數字（至少三個字元，轉小寫）。"""
    s = " ".join(_PROMO_SHORT.sub(" ", _PROMO_URL.sub(" ", sentence)).split())
    runs = _PROMO_CJK.findall(s)
    if runs:
        return max(runs, key=len)
    runs = [r for r in _PROMO_ASCII.findall(s) if len(r) >= 3]
    return max(runs, key=len).lower() if runs else ""


def promo_pending_filter(lines, current=None):
    """PM 頁用：回傳 (ES 條件 或 None, 清單句數)。條件＝「原文可能含清單裡某一句、而且斷詞版本比開始收那一句的字典版本舊」，
    也就是定向重跑（seg/qseg/rerun.py 的 promo_scope）還沒處理到的貼文；重跑完就是 0 篇。
    current＝現在上線的字典版本：版本門檻不超過它（與 promo_scope 的 min(target, since) 同一個算法），
    否則清單裡第三欄寫了比現在還新的版本時，頁面會一直顯示重跑也清不掉的篇數。"""
    groups, n = {}, 0
    cap = None if current is None else seg_ver("v2", current)
    for raw in lines:
        raw = raw.lstrip("﻿").rstrip("\r\n")     # 檔頭的 BOM 不算渠道名的一部分
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cols = raw.split("\t")
        if len(cols) != 3:
            raise ValueError(f"{PROMO_FILE}：要寫成「渠道<TAB>句子<TAB>從哪一版字典開始收」：{raw[:60]!r}")
        if not cols[0].strip():
            raise ValueError(f"{PROMO_FILE}：第一欄（渠道）不能空白：{raw[:60]!r}")
        key = " ".join(_PROMO_SHORT.sub(" ", _PROMO_URL.sub(" ", cols[1])).split())
        if len(_PROMO_WORD.findall(key)) < PROMO_MIN_WORD_CHARS:   # 與 seg/qseg/promo.py 的 MIN_WORD_CHARS 同一條規則
            raise ValueError(f"{PROMO_FILE}：句子去掉網址後不到 {PROMO_MIN_WORD_CHARS} 個字，太短會誤刪正文：{cols[1][:60]!r}")
        since = cols[2].strip()
        m = _PROMO_SINCE.match(since)
        # 每一行都驗（不只驗挑得出片語的那幾行）：載入字典時會擋的版本寫法，這裡也要擋
        if not m or not (1 <= int(m[2]) <= 12 and 1 <= int(m[3]) <= 31):
            raise ValueError(f"{PROMO_FILE}：第三欄要是字典版本（例 qsearch-q6-2026.11.12.1）：{since[:60]!r}")
        n += 1
        p = promo_probe(cols[1])
        if p:
            groups.setdefault((since, cols[0].strip()), set()).add(p)
    def floor(since):
        v = seg_ver("v2", since)
        return v if cap is None else min(cap, v)

    clauses = [{"bool": {"filter": [{"term": {"channel": ch}}, {"range": {"seg_ver_v2": {"lt": floor(since)}}}],
                         "should": [{"match_phrase": {"content.bigram": p}} for p in sorted(ps)], "minimum_should_match": 1}}
               for (since, ch), ps in sorted(groups.items())]
    return ({"bool": {"should": clauses, "minimum_should_match": 1}} if clauses else None), n


def validate_words(words) -> list:
    """接受 ["詞", {"word":..,"freq":..,"pos":..}, ["詞", 20, "n"]]，回傳 [(詞, 詞頻, 詞性)]；去重、最多 50 個。"""
    out, seen = [], set()
    for w in words or []:
        if isinstance(w, str):
            word, freq, pos = w, 20, "n"
        elif isinstance(w, dict):
            word, freq, pos = w.get("word", ""), w.get("freq", 20), w.get("pos", "n")
        elif isinstance(w, (list, tuple)) and w:
            word = w[0]
            freq = w[1] if len(w) > 1 else 20
            pos = w[2] if len(w) > 2 else "n"
        else:
            raise ValueError(f"看不懂的詞條：{w!r}")
        word = str(word).strip()
        if not WORD_RE.match(word):
            raise ValueError(f"詞只能是 1–20 個中英數字、不能有空白或標點：{word!r}")
        try:
            freq = int(freq)
        except (TypeError, ValueError):
            raise ValueError(f"詞頻要是整數：{word} {freq!r}")
        if not 1 <= freq <= 100000:
            raise ValueError(f"詞頻要在 1–100000：{word} {freq}")
        pos = str(pos or "n").strip()[:8]
        if word not in seen:
            seen.add(word)
            out.append((word, freq, pos))
    if not out:
        raise ValueError("至少要一個詞")
    if len(out) > 50:
        raise ValueError("一次最多 50 個詞")
    return out


def _entries(lines):
    out = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        w, f_, *pos = line.split()
        out[w] = [int(f_), pos[0] if pos else ""]
    return out


def _base_freq(base_dict: Path, words: set) -> dict:
    found = {}
    with open(base_dict, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2 and parts[0] in words:
                found[parts[0]] = int(parts[1])
    return found


def build_release(root: Path, words: list, note: str = "", requested_by: str = "") -> dict:
    """從 current 版建下一版：複製全部檔案、使用者字典加詞（詞頻不低於主字典）、IK 擴充字典同步加詞、寫 manifest。
    current 版有宣傳句清單就原封不動帶過去（清單要改走人工審核後的發版，不在 PM 頁改）。
    不切 current（讓呼叫端在發出控制訊息前後決定）。回傳新版 manifest。"""
    cur = read_manifest(root)
    cur_v = cur["dict_version"]
    new_v = next_version(cur_v)
    src, out = root / "versions" / cur_v, root / "versions" / new_v
    if out.exists():
        raise FileExistsError(f"{new_v} 已存在")
    out.mkdir(parents=True)
    names = DICT_FILES + tuple(name for name in OPTIONAL_FILES if name in cur.get("files", {}))
    for name in names:
        shutil.copyfile(src / name, out / name)
    entries = _entries((src / "user.dict").read_text(encoding="utf-8").splitlines())
    floor = _base_freq(src / "base.dict", {w for w, _, _ in words})
    added = []
    for w, f_, pos in words:
        f_ = max(f_, floor.get(w, 0))      # 使用者字典的詞頻不低於主字典（避免「高雄市 5」蓋掉 31）
        entries[w] = [f_, pos]
        added.append([w, f_, pos])
    (out / "user.dict").write_text("".join(f"{w} {f_} {pos}".rstrip() + "\n" for w, (f_, pos) in entries.items()), encoding="utf-8")
    (out / "ik_ext.dic").write_text("\n".join(entries) + "\n", encoding="utf-8")
    files = {name: sha256_file(out / name) for name in names}
    manifest = {
        "dict_version": new_v, "note": note or f"PM 頁發版：加 {len(added)} 個詞", "base_dict": cur.get("base_dict"),
        "total_pinned": cur["total_pinned"], "user_words": len(entries), "files": files,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extends": cur_v, "add_words": added, "requested_by": requested_by or "pm-page",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = root / "releases"
    rel.mkdir(exist_ok=True)
    (rel / f"{new_v}.json").write_text(json.dumps({"dict_version": new_v, "extends": cur_v, "note": manifest["note"],
                                                   "add_words": added, "requested_by": manifest["requested_by"],
                                                   "built_at": manifest["built_at"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def switch_current(root: Path, version: str) -> None:
    """原子切換：先建暫時的 symlink 再 rename（nginx 不會讀到一半）。"""
    if not (root / "versions" / version / "manifest.json").exists():
        raise FileNotFoundError(version)
    tmp = root / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(Path("versions") / version)
    os.replace(tmp, root / "current")


# ---------- Pub/Sub ----------
class PubSub:
    """發佈端。本機連官方模擬器（PUBSUB_EMULATOR_HOST）；連真 GCP 時 client 程式碼相同，只是不自己建主題（由 Terraform 管）。"""

    def __init__(self, project: str, emulator_host: str = None):
        self.project = project
        self.emulator = emulator_host
        self._pub = None

    def ensure(self, topic: str, subscription: str = None, ack_deadline: int = 600) -> None:
        if not self.emulator:
            return
        import requests
        base = f"http://{self.emulator}/v1/projects/{self.project}"
        r = requests.put(f"{base}/topics/{topic}", json={}, timeout=15)
        if r.status_code not in (200, 409):
            raise RuntimeError(f"建主題 {topic} 失敗：{r.status_code} {r.text[:200]}")
        if subscription:
            r = requests.put(f"{base}/subscriptions/{subscription}", timeout=15,
                             json={"topic": f"projects/{self.project}/topics/{topic}", "ackDeadlineSeconds": ack_deadline})
            if r.status_code not in (200, 409):
                raise RuntimeError(f"建訂閱 {subscription} 失敗：{r.status_code} {r.text[:200]}")

    def publish(self, topic: str, payload: dict, **attrs) -> str:
        from google.cloud import pubsub_v1
        if self._pub is None:
            self._pub = pubsub_v1.PublisherClient()
        path = self._pub.topic_path(self.project, topic)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._pub.publish(path, data, **{k: str(v) for k, v in attrs.items() if v is not None}).result(timeout=30)


# ---------- 貼文 ----------
def content_sha(text: str) -> str:
    """原文的雜湊（sha256 前 16 碼）：同一版斷詞下原文改了才要重算；版本比大小、內容比雜湊（與 q6/code/segmenter.py 同一個定義）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def make_post(channel: str, text: str, url: str = None, source: str = None, published_at: str = None,
              post_id: str = None, sub_type: str = "post", collect_method: str = "live", author: str = None) -> dict:
    if channel not in CHANNELS:
        raise ValueError(f"渠道要是 {'/'.join(CHANNELS)}：{channel!r}")
    text = (text or "").strip()
    if not 1 <= len(text) <= 20000:
        raise ValueError("內文要 1–20000 字")
    if published_at:
        datetime.fromisoformat(published_at.replace("Z", "+00:00"))   # 格式不對就讓它丟錯
    return {"post_id": post_id or f"live-{channel}-{hashlib.sha1((url or text).encode('utf-8')).hexdigest()[:16]}",
            "channel": channel, "sub_type": sub_type, "source": source, "url": url,
            "published_at": published_at or now_iso(), "fetched_at": now_iso(), "text": text, "collect_method": collect_method,
            "author": (author or None)}


def base_doc(post: dict) -> dict:
    """文章本體（parser 寫入的部分）；斷詞欄位由各版服務各自寫。與 seg/qseg/pipeline.py 的 base_doc 一致。"""
    doc = {k: post.get(k) for k in BASE_KEYS}
    doc["content"] = post.get("text") or post.get("content") or ""
    doc["content_sha"] = content_sha(doc["content"])
    doc["month"] = (post.get("published_at") or "")[:7] or "unknown"
    return doc


def load_channel_samples(folder: Path) -> list:
    """data/channels/*.jsonl 的六渠道公開頁樣本（2026-10-31 未登入公開頁抓的），依發文時間由舊到新。"""
    posts = []
    for ch in CHANNELS:
        p = folder / f"{ch}.jsonl"
        if p.exists():
            posts += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return sorted(posts, key=lambda x: x.get("published_at") or "")
