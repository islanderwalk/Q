# 純 HTTP：抓 HTML → 取出所有 rsrc.php JS 網址（含多層跳脫的 preload）→ 下載 → 找 doc_id 模組
import sys, re, requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

html = Path(sys.argv[1]).read_text(encoding="utf-8")
out = Path(sys.argv[2]); out.mkdir(exist_ok=True)
NAME = "BarcelonaProfileThreadsTabRefetchableDirectQuery"
BS = chr(92)
flat = html
for _ in range(4):
    flat = flat.replace(BS + BS, BS).replace(BS + "/", "/")
urls = sorted(set(re.findall(r'https://static\.cdninstagram\.com/rsrc\.php/[^"\s' + re.escape(BS) + r']+?\.js', flat)))
print("distinct JS urls in HTML (all escapings)", len(urls))
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"}
pat = re.compile(r'__d\("' + NAME + r'_threadsRelayOperation",\[\],\(function\([a-z,]+\)\{[a-z]\.exports="(\d+)"')


def get(u):
    fn = out / (re.sub(r'[^A-Za-z0-9]', '_', u[-60:]) + ".js")
    body = fn.read_text(encoding="utf-8") if fn.exists() else requests.get(u, headers=H, timeout=60).text
    fn.write_text(body, encoding="utf-8")
    m = pat.search(body)
    return (u, m.group(1)) if m else None


with ThreadPoolExecutor(6) as ex:
    hits = [h for h in ex.map(get, urls) if h]
for u, d in hits:
    print("FOUND", d, u[:110])
print("hits", len(hits))
