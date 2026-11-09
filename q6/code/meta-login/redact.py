# Writes shareable copies of the logged-in screenshots to evidence/redacted/, with the operator's own account
# avatar / notification badges / title-bar unread count blurred. Originals are left untouched (local only).
import json, pathlib
from PIL import Image, ImageFilter, ImageDraw

import os  # noqa: E402
D = pathlib.Path(os.environ.get("META_WORKDIR") or pathlib.Path(__file__).parent)  # outputs + profile live here
EV = D / "evidence"
OUT = EV / "redacted"
OUT.mkdir(exist_ok=True)


def boxes(name, w, h):
    win = "_devtools_F12" in name  # OS window grab (browser chrome + page + DevTools)
    b = []
    if win:
        b.append((330, 18, 600, 52))  # tab title: "(9) Facebook" unread counts
    if name.startswith("facebook"):
        b.append((w - 290, 110, w - 20, 195) if win else (w - 290, 0, w, 70))  # messenger/notif/avatar
    elif name.startswith("instagram"):
        b.append((10, 460, 100, 680) if win else (0, 330, 100, h))  # left nav: activity badge + avatar
    elif name.startswith("threads"):
        b.append((10, 250, 300, 420) if win else (0, 250, 300, 420))  # left nav: 訊息/動態/個人檔案 badges
    return b


def main():
    log = []
    for p in sorted(EV.glob("*.png")):
        if p.name.startswith(("compare", "chain_")):
            continue
        im = Image.open(p).convert("RGB")
        bs = boxes(p.name, *im.size)
        for x0, y0, x1, y1 in bs:
            x0, y0, x1, y1 = max(0, x0), max(0, y0), min(im.width, x1), min(im.height, y1)
            if x1 <= x0 or y1 <= y0:
                continue
            reg = im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(14))
            im.paste(reg, (x0, y0))
            ImageDraw.Draw(im).rectangle((x0, y0, x1 - 1, y1 - 1), outline=(255, 64, 64), width=2)
        im.save(OUT / p.name)
        log.append({"file": p.name, "blurred_boxes": bs})
    (OUT / "redaction_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(len(log), "files")


if __name__ == "__main__":
    main()
