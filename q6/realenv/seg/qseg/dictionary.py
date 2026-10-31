"""從字典 artifact 伺服器（GCS 的本機替身）下載指定版本，逐檔比對 sha256，建 jieba Tokenizer。

v2：主字典＋使用者字典，載入後把 total 設回 manifest 的 total_pinned（固定 total，加詞不會牽動不含該詞的文章）
v1：只用主字典（舊流程「純字典斷詞」的模擬：HMM 關、不清洗、不過濾）
"""
import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path

import jieba
import requests

from . import config

CACHE = Path(os.environ.get("DICT_CACHE", "/tmp/qseg-dict"))


class DictIntegrityError(RuntimeError):
    pass


def _get(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def fetch(version: str = None, base_url: str = None) -> Path:
    version = version or config.DICT_VERSION
    base_url = (base_url or config.DICT_BASE_URL).rstrip("/")
    prefix = f"{base_url}/current" if version == "current" else f"{base_url}/versions/{version}"
    manifest = json.loads(_get(f"{prefix}/manifest.json"))
    out = CACHE / manifest["dict_version"]
    out.mkdir(parents=True, exist_ok=True)
    for name, digest in manifest["files"].items():
        p = out / name
        if not (p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == digest):
            data = _get(f"{prefix}/{name}")
            if hashlib.sha256(data).hexdigest() != digest:
                raise DictIntegrityError(f"{name} sha256 不符 manifest，拒絕啟動")
            p.write_bytes(data)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def _lines(p: Path):
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


@dataclass
class Dictionary:
    version: str
    manifest: dict
    tokenizer: jieba.Tokenizer
    stopwords: frozenset
    soft_stopwords: frozenset
    merges: dict
    user_words: frozenset
    pipeline: str

    @property
    def sha(self) -> str:
        return self.manifest["files"]["user.dict"][:16]


def load(version: str = None, pipeline: str = None, base_url: str = None, pin_total: bool = True) -> Dictionary:
    pipeline = pipeline or config.PIPELINE
    d = fetch(version, base_url)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    tk = jieba.Tokenizer(dictionary=str(d / "base.dict"))
    tk.tmp_dir = str(CACHE)
    tk.initialize()
    words = set()
    if pipeline == "v2":
        lines = _lines(d / "user.dict")
        words = {l.split()[0] for l in lines}
        tk.load_userdict(io.StringIO("\n".join(lines)))
        if pin_total:
            tk.total = manifest["total_pinned"]
    merges = dict(l.split("\t") for l in (d / "merges.tsv").read_text(encoding="utf-8").splitlines() if "\t" in l)
    return Dictionary(version=manifest["dict_version"], manifest=manifest, tokenizer=tk,
                      stopwords=frozenset(_lines(d / "stopwords.txt")),
                      soft_stopwords=frozenset(_lines(d / "soft_stopwords.txt")),
                      merges=merges, user_words=frozenset(words), pipeline=pipeline)
