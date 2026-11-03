"""建字典 artifact（GCS 物件的本機替身）：每一版一個資料夾，內含 jieba 與 IK 兩邊用的檔案和 manifest。

用法：python build_artifact.py <dict.txt.big> <輸出根目錄> [版本 ...]
輸出：<根>/versions/<dict_version>/{base.dict,user.dict,stopwords.txt,soft_stopwords.txt,merges.tsv,
      ik_ext.dic,ik_stop.dic,synonyms.txt,manifest.json}；<根>/current -> 指定的最後一版
設計重點：
- 斷詞（jieba）與搜尋（IK）吃同一份核心詞庫：ik_ext.dic 由 user.dict 的詞直接導出，不另外維護。
- manifest 記下主字典 total（固定 total），斷詞服務載入使用者字典後把 total 設回這個數，
  加詞只影響含該字串的文章（見 q6/code/jieba_experiments 實驗二）。
- 每個檔案記 sha256，斷詞映像啟動時逐一比對，對不上就拒絕啟動。
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REL = HERE / "releases"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_release(version: str) -> dict:
    spec = json.loads((REL / f"{version}.json").read_text(encoding="utf-8"))
    if "extends" in spec:
        base = load_release(spec["extends"])
        merged = dict(base)
        merged["add_words"] = base["add_words"] + spec.get("add_words", [])
        for k, v in spec.items():
            if k not in ("add_words", "extends"):
                merged[k] = v
        return merged
    return spec


def base_freqs(base_dict: Path):
    freq, total = {}, 0
    with open(base_dict, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                freq[parts[0]] = int(parts[1])
                total += int(parts[1])
    return freq, total


def comment_free(path: Path):
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def build(base_dict: Path, out_root: Path, version: str) -> Path:
    spec = load_release(version)
    freq, total = base_freqs(base_dict)
    out = out_root / "versions" / version
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base_dict, out / "base.dict")

    entries = {}
    for line in comment_free((REL / spec["user_dict_from"]).resolve()):
        w, f_, *pos = line.split()
        entries[w] = [int(f_), pos[0] if pos else ""]
    for w, f_, pos in spec.get("add_words", []):
        entries[w] = [int(f_), pos]
    if spec.get("freq_floor_from_base"):
        # 使用者字典的詞頻不低於主字典：避免「高雄市 5」把主字典的 31 蓋掉
        for w, e in entries.items():
            e[0] = max(e[0], freq.get(w, 0))
    (out / "user.dict").write_text("".join(f"{w} {f_} {pos}".rstrip() + "\n" for w, (f_, pos) in entries.items()), encoding="utf-8")
    stop = comment_free((REL / spec["stopwords_from"]).resolve())
    (out / "stopwords.txt").write_text("\n".join(stop) + "\n", encoding="utf-8")
    (out / "soft_stopwords.txt").write_text("\n".join(spec.get("soft_stopwords", [])) + "\n", encoding="utf-8")
    (out / "merges.tsv").write_text("".join(f"{a}\t{b}\n" for a, b in spec.get("merges", {}).items()), encoding="utf-8")
    (out / "synonyms.txt").write_text("\n".join(spec.get("search_synonyms", [])) + "\n", encoding="utf-8")
    # IK 遠端字典：一行一詞，與 jieba 使用者字典同一份詞
    (out / "ik_ext.dic").write_text("\n".join(entries) + "\n", encoding="utf-8")
    (out / "ik_stop.dic").write_text("\n".join(stop) + "\n", encoding="utf-8")

    files = {p.name: sha256(p) for p in sorted(out.iterdir()) if p.name != "manifest.json"}
    manifest = {
        "dict_version": version,
        "note": spec.get("note", ""),
        "base_dict": base_dict.name,
        "total_pinned": total,
        "user_words": len(entries),
        "files": files,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def set_current(out_root: Path, version: str):
    cur = out_root / "current"
    if cur.is_symlink() or cur.exists():
        cur.unlink()
    cur.symlink_to(Path("versions") / version)


if __name__ == "__main__":
    # 用法：build_artifact.py <dict.txt.big> <輸出根目錄> <版本>... [--keep-current]
    # 最後一個版本會成為 current；加 --keep-current 則只建版本、不切 current（先上架、之後再切）
    args = [a for a in sys.argv[1:] if a != "--keep-current"]
    base, root, *versions = args
    root = Path(root)
    for v in versions:
        print("built", build(Path(base), root, v))
    if "--keep-current" not in sys.argv:
        set_current(root, versions[-1])
        print("current ->", versions[-1])
