# export_text.py <built page.html> <out dir>
# Plain-text copy of each sub-page (diagram labels, notes) so the README can link a text version.
import base64, html.parser, json, os, sys

page, out = sys.argv[1:3]
s = open(page, encoding="utf8").read()
i = s.index("const PAGES = ") + len("const PAGES = ")
PAGES = json.JSONDecoder().raw_decode(s[i:])[0]

class Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.lines = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "title"): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "title"): self.skip -= 1
    def handle_data(self, d):
        d = " ".join(d.split())
        if d and not self.skip: self.lines.append(d)

NAME = {"int": "一張圖（整合）", "seq": "請求時序", "uvicorn": "Uvicorn 背景", "notes": "推演筆記"}
os.makedirs(out, exist_ok=True)
for k, v in PAGES.items():
    if k == "q2":  # the Q2 page is q2/answer/fb_q2_posts_table.html, kept as is
        continue
    t = Text(); t.feed(base64.b64decode(v).decode("utf8"))
    head = f"# 總架構「{NAME.get(k, k)}」分頁的純文字版，由 shared/architecture/build/export_text.py 從 qsearch-architecture.html 匯出"
    open(os.path.join(out, k + ".txt"), "w", encoding="utf8").write(head + "\n" + "\n".join(t.lines) + "\n")
    print(k, len(t.lines), "lines")
