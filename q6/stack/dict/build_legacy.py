"""建 v0「舊流程重現版」的字典 artifact：versions/<版本>/{legacy.dict, manifest.json}。

用法：python dict/build_legacy.py <輸出根目錄> <版本> --moe <moe_revised_words.txt> --jieba-tw <jieba_tw_dict.txt>
版本規格在 dict/releases/<版本>.json：layers 列出每一層的來源、授權、凍結日；layers 的 "arg" 由命令列給檔案，
"file" 是 dict/ 下的 repo 檔。所有層取第一欄的詞、去重合併成一份純詞表（2 到 maxlen 字的中文詞），不帶詞頻、不帶詞性，
因為舊流程是純字典最長匹配。manifest 記每一層的 sha256 與詞數，斷詞服務載入時逐檔比對 sha256（qseg/dictionary.py）。

不會動 current：current 是 v2 的 jieba 字典，v0 服務用 DICT_VERSION 指定這個版本。
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REL = HERE / "releases"
CJK_RE = re.compile(r"^[㐀-䶿一-鿿]+$")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def words_of(path: Path, maxlen: int) -> set:
    """轉成純中文詞集（2 到 maxlen 字）。
    外部詞典（jieba-tw 的 `詞 詞頻 詞性`、教育部區段倒出的一行一詞）只取第一個 token；
    repo 的層檔（company_2019.txt、places_old.txt）是空白分隔的詞表，整行都收。"""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            toks = line.split()
            # 行形如 "詞 詞頻 [詞性]"（第二欄是整數）是外部詞典，只取第一欄；其餘是詞表，整行收
            use = toks[:1] if (len(toks) >= 2 and toks[1].isdigit()) else toks
            for w in use:
                if 2 <= len(w) <= maxlen and CJK_RE.match(w):
                    out.add(w)
    return out


def build(out_root: Path, version: str, sources: dict) -> Path:
    spec = json.loads((REL / f"{version}.json").read_text(encoding="utf-8"))
    maxlen = int(spec["algo"]["maxlen"])
    out = out_root / "versions" / version
    out.mkdir(parents=True, exist_ok=True)
    merged, layers = set(), []
    for layer in spec["layers"]:
        if "arg" in layer:
            if layer["arg"] not in sources:
                raise SystemExit(f"層 {layer['id']} 要用 --{layer['arg'].replace('_', '-')} 給檔案")
            p = Path(sources[layer["arg"]])
        else:
            p = HERE / layer["file"]
        ws = words_of(p, maxlen)
        new = ws - merged
        merged |= ws
        layers.append({k: layer[k] for k in ("id", "source", "url", "license", "frozen_at") if k in layer}
                      | {"file": p.name, "sha256": sha256(p), "words": len(ws), "new_words": len(new)})
        print(f"{layer['id']}: {len(ws)} words, {len(new)} new", file=sys.stderr)
    (out / "legacy.dict").write_text("\n".join(sorted(merged)) + "\n", encoding="utf-8")
    manifest = {
        "dict_version": version,
        "pipeline": spec.get("pipeline", "v0"),
        "note": spec.get("note", ""),
        "algo": spec["algo"],
        "layers": layers,
        "total_words": len(merged),
        "frozen_at": max(l.get("frozen_at", "") for l in layers),
        "files": {"legacy.dict": sha256(out / "legacy.dict")},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_root")
    ap.add_argument("version")
    ap.add_argument("--moe", help="教育部重編國語辭典修訂本的詞表（dict/legacy/extract_moe.py 的輸出）")
    ap.add_argument("--jieba-tw", dest="jieba_tw", help="jieba-zh_TW 的 jieba/dict.txt")
    a = ap.parse_args(argv)
    sources = {k: v for k, v in (("moe", a.moe), ("jieba_tw", a.jieba_tw)) if v}
    out = build(Path(a.out_root), a.version, sources)
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print(f"built {out} ({m['total_words']} words, frozen_at {m['frozen_at']})")


if __name__ == "__main__":
    main()
