"""把 q2/evidence/ 的截圖裁成小圖，嵌進 Q2 頁（fb_q2_posts_table.html）的 TIP_IMG。

Q2 總覽圖滑過 ③ 的 page_id、creation_time、7 種心情、page_name，以及 ⑤ 的 page_id 時，
會顯示 NASA 這則貼文的畫面與 F12 截圖。這些圖以 data URI 放在頁面裡，
所以頁面單獨開、或嵌在架構頁的 Q2 分頁裡都看得到。

用法（在 repo 根目錄）：
    python3 q2/answer/build_tip_images.py q2/answer/fb_q2_posts_table.html [其他同版頁面 …]

只改檔案裡 /* TIP_IMG:BEGIN */ 和 /* TIP_IMG:END */ 之間的內容。
"""
import base64
import io
import json
import os
import re
import sys

from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
F12 = "q2/evidence/f12/"
PW = "q2/evidence/playwright/"

# 名稱: (來源截圖, 裁切框 (左, 上, 右, 下)，原圖像素)
CROPS = {
    "pid_screen": (PW + "fb_page_transparency_id.png", (575, 703, 1140, 822)),    # 粉專透明度頁的 Page ID
    "pid_f12": (F12 + "fb_f12_page_id.png", (765, 815, 1440, 1030)),             # Sources 搜尋 delegate_page.id
    "pname_screen": (F12 + "fb_post_annotated.png", (180, 236, 890, 308)),       # 貼文標頭的粉專名稱
    "pname_f12": (F12 + "fb_f12_page_name.png", (765, 815, 1730, 1030)),         # Sources 搜尋 owning_profile.name
    "ctime_screen": (F12 + "fb_post_time_tooltip.png", (180, 240, 690, 352)),    # 滑過發文時間的完整時間
    "ctime_f12": (F12 + "fb_f12_creation_time.png", (765, 930, 1500, 1150)),     # Sources 搜尋 creation_time
    "react_screen": (F12 + "fb_post_reactions_dialog.png", (270, 335, 975, 620)),  # 心情對話框：只有名稱
    "post": (F12 + "fb_post_annotated.png", (178, 236, 1050, 992)),              # 整則貼文
}
# 7 種心情的 F12 搜尋結果列（黃底），由上到下照 Like、Love、Care、Wow、Haha、Sad、Angry 疊成一張
REACT_ROWS = ["like", "love", "care", "wow", "haha", "sad", "angry"]
REACT_BOX = (972, 998, 1764, 1026)
REACT_GAP = 3


def webp(im):
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=80, method=6)
    return buf.getvalue()


def entry(im):
    return {"w": im.width, "h": im.height,
            "src": "data:image/webp;base64," + base64.b64encode(webp(im)).decode()}


def build():
    out = {}
    for k, (p, box) in CROPS.items():
        out[k] = entry(Image.open(os.path.join(REPO, p)).convert("RGB").crop(box))
    rows = [Image.open(os.path.join(REPO, F12 + f"fb_f12_reaction_{r}.png")).convert("RGB").crop(REACT_BOX)
            for r in REACT_ROWS]
    w, h = rows[0].size
    sheet = Image.new("RGB", (w, len(rows) * h + (len(rows) - 1) * REACT_GAP), (40, 40, 40))
    for i, r in enumerate(rows):
        sheet.paste(r, (0, i * (h + REACT_GAP)))
    out["react_f12"] = entry(sheet)
    return out


def main(paths):
    imgs = build()
    block = "/* TIP_IMG:BEGIN */const TIP_IMG = " + json.dumps(imgs, separators=(",", ":")) + ";/* TIP_IMG:END */"
    for p in paths:
        s = open(p, encoding="utf8").read()
        n = len(re.findall(r"/\* TIP_IMG:BEGIN \*/.*?/\* TIP_IMG:END \*/", s, flags=re.S))
        if n != 1:
            sys.exit(f"{p}: expected one TIP_IMG block, found {n}")
        s = re.sub(r"/\* TIP_IMG:BEGIN \*/.*?/\* TIP_IMG:END \*/", lambda m: block, s, flags=re.S)
        open(p, "w", encoding="utf8").write(s)
        print(p, f"{len(imgs)} images, {len(block):,} chars")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
