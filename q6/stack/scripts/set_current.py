"""把字典 artifact 的 current 原子切到某一版（GCS 上就是改 current 物件）。
用法：python set_current.py <runtime/dictsrv> <版本|latest>   latest = versions/ 裡 seg_ver 最大的那一版"""
import json
import os
import sys
from pathlib import Path


def ver_key(v: str) -> int:
    y, m, d, n = (int(x) for x in v.split("-")[-1].split("."))
    return ((y * 100 + m) * 100 + d) * 100 + n


def latest(root: Path) -> str:
    vs = [json.loads((d / "manifest.json").read_text(encoding="utf-8"))["dict_version"]
          for d in (root / "versions").iterdir() if (d / "manifest.json").exists()]
    if not vs:
        raise SystemExit("versions/ 裡沒有任何一版")
    return max(vs, key=ver_key)


def switch(root: Path, version: str) -> str:
    if version == "latest":
        version = latest(root)
    if not (root / "versions" / version / "manifest.json").exists():
        raise SystemExit(f"沒有這一版：{version}")
    tmp = root / ".current.tmp"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(Path("versions") / version)
    os.replace(tmp, root / "current")   # rename 是原子的：nginx 不會讀到一半
    return version


if __name__ == "__main__":
    print("current ->", switch(Path(sys.argv[1]), sys.argv[2]))
