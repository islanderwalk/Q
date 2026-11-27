import sys, re, json, requests
from pathlib import Path
out = Path(sys.argv[1]); acct = sys.argv[2]
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
     "Accept-Language": "en-US,en;q=0.9", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
r = requests.get(f"https://www.threads.com/@{acct}", headers=H, timeout=30)
html = r.text
(out / f"{acct}.html").write_text(html, encoding="utf-8")
srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
print("status", r.status_code, "bytes", len(html.encode()), "script src", len(srcs))
for name in ["BarcelonaProfileThreadsTabRefetchableDirectQuery", "BarcelonaProfileThreadsTabDirectQuery", "_threadsRelayOperation"]:
    print(name, html.count(name))
for m in re.finditer(r'BarcelonaProfileThreadsTabRefetchableDirectQuery[^"]{0,80}', html):
    print("  ctx:", html[max(0,m.start()-200):m.end()+200].replace("\n"," ")[:500]); break
(out / f"{acct}_srcs.json").write_text(json.dumps(srcs, indent=0))
print("\n".join(srcs[:5]))
