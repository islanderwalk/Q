"""把實拍截圖（kibana_raw.png、psql_raw.png）疊上紅框與說明字，兩張各自存檔；--embed 嵌進頁面，由按鈕手動切換（不自動輪播）。
截圖本身不改；只多疊紅框與紅字（標註）。
"""
import base64, os, re, sys
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_W, W, H = 1100, 1500, 830
FONT = ImageFont.truetype(r"C:\Windows\Fonts\msjhbd.ttc", 30)
RED = (225, 29, 29)

def annotate(im, box, text, tpos):
    im = im.convert("RGB")
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(box, radius=10, outline=RED, width=6)
    # 白底紅字標籤，避免壓到原畫面文字
    x, y = tpos
    l, t, r, b = d.textbbox((x, y), text, font=FONT)
    d.rounded_rectangle((l - 12, t - 8, r + 12, b + 10), radius=8, fill=(255, 255, 255), outline=RED, width=4)
    d.text((x, y), text, font=FONT, fill=RED)
    return im

def fit(im):
    im = im.convert("RGB")
    if im.size != (W, H):
        c = Image.new("RGB", (W, H), (12, 12, 12))
        im = im.resize((W, round(im.height * W / im.width)), Image.LANCZOS)
        c.paste(im.crop((0, 0, W, H)), (0, 0))
        im = c
    return im

psql = fit(Image.open(os.path.join(HERE, "psql_raw.png")))
kib = fit(Image.open(os.path.join(HERE, "kibana_raw.png")))
A = annotate(psql, (14, 124, 1440, 476), "LIKE 只回答「有沒有」：9 筆都符合，沒有分數、沒有名次", (60, 580))
B = annotate(kib, (766, 328, 1476, 770), "ES 依 _score 排：整篇都在講颱風的排最前面", (40, 560))
A.save(os.path.join(HERE, "screen_like.png")); B.save(os.path.join(HERE, "screen_es.png"))

h_out = round(OUT_W * H / W)
import io
def uri(im):
    buf = io.BytesIO()
    im.resize((OUT_W, h_out), Image.LANCZOS).save(buf, "WEBP", quality=88, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()

if "--embed" in sys.argv:
    path = sys.argv[sys.argv.index("--embed") + 1]
    img = lambda id_, im, alt, hidden: (f'<img id="{id_}" alt="{alt}" width="{OUT_W}" height="{h_out}"' + (" hidden" if hidden else "") + f' src="{uri(im)}">')
    block = ('<!-- RELEVANCE_ANIM:BEGIN --><div class="rel-anim">'
             '<div class="rel-tabs" role="group" aria-label="切換畫面">'
             '<button type="button" class="rel-tab" aria-pressed="true" data-t="rel-like">① Postgres LIKE</button>'
             '<button type="button" class="rel-tab" aria-pressed="false" data-t="rel-es">② Elasticsearch</button></div>'
             + img("rel-like", A, "Postgres 的 LIKE（psql 視窗實拍）只回傳符合的貼文、沒有分數", False)
             + img("rel-es", B, "Elasticsearch（Kibana Dev Tools 實拍）依 _score 由高到低排序", True)
             + '</div><script>document.querySelectorAll(".rel-tab").forEach(function(b){b.onclick=function(){'
               'document.querySelectorAll(".rel-tab").forEach(function(x){x.setAttribute("aria-pressed",x===b);'
               'document.getElementById(x.dataset.t).hidden=x!==b;});};});</script><!-- RELEVANCE_ANIM:END -->')
    src = open(path, encoding="utf-8").read()
    src = re.sub(r"<!-- RELEVANCE_ANIM:BEGIN -->.*?<!-- RELEVANCE_ANIM:END -->", lambda m: block, src, flags=re.S)
    open(path, "w", encoding="utf-8", newline="").write(src)
    print("embedded", len(block) // 1024, "KB")
