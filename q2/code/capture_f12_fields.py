"""從 FB 單則貼文頁取出 Q2 貼文表欄位，並記下每個值在頁面資料裡的路徑。

對應證據：q2/evidence/f12/（README.md、fb_post_fields.json、F12 截圖）。

做法
- 用 Playwright 開一個「已登入的瀏覽器設定檔副本」（--profile 指定；腳本不碰帳密）。
- 只載入一次貼文頁，讀頁面內嵌的 <script type="application/json">，在記憶體裡 JSON.parse，
  找出 post_id 相符的 node_v2，依鍵名取欄位並輸出「路徑 + 值 + 來源」。
- --dialog：再點一下心情圖示，攔截 CometUFIReactionsDialogQuery 的 GraphQL 回應，
  取 top_reactions.summary[i].reaction_count（貼文設定 HIDE_COUNTS 時畫面不顯示數字）。

只讀
- 不按讚、不留言、不追蹤、不送出任何表單；除了 --dialog 的那一下點擊，不做其他操作。
- 不存整頁 HTML、不存整份回應、不存 cookie 或任何請求參數；輸出只有下列欄位。
- 遇到登入頁、checkpoint 或安全驗證就停止並回報。

用法
    python capture_f12_fields.py --profile <瀏覽器設定檔副本目錄> --url <貼文網址> [--dialog] [-o out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

from playwright.sync_api import sync_playwright

STOP_WORDS = ("/login", "/checkpoint", "two_step_verification", "captcha")


def walk(o, path=""):
    """深度優先走訪，產出 (路徑, 鍵, 值)。"""
    if isinstance(o, dict):
        for k, v in o.items():
            p = f"{path}.{k}" if path else k
            yield p, k, v
            yield from walk(v, p)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")


def find_node(root, post_id):
    """回傳 (node_v2 的路徑, node_v2)；以 node_v2.post_id 比對。"""
    for p, k, v in walk(root):
        if k == "node_v2" and isinstance(v, dict) and v.get("post_id") == post_id:
            return p, v
    return None, None


def first(node, base, key, pred=lambda p, v: True):
    for p, k, v in walk(node, base):
        if k == key and "comment" not in p and pred(p, v):
            return p, v
    return None, None


def extract(root):
    # 先找任一含 post_id + creation_time 的 node_v2
    post_id = None
    for p, k, v in walk(root):
        if k == "node_v2" and isinstance(v, dict) and "post_id" in v and "creation_time" in v:
            post_id = v["post_id"]
            break
    if not post_id:
        return None
    base, node = find_node(root, post_id)
    out = {}

    def put(name, p, v):
        if p is not None:
            out[name] = {"path": p, "value": v}

    put("post_id", f"{base}.post_id", node["post_id"])
    put("creation_time", f"{base}.creation_time", node["creation_time"])
    put("profile_id", *first(node, base, "id", lambda p, v: p.endswith("feedback.owning_profile.id")))
    put("page_name", *first(node, base, "name", lambda p, v: p.endswith("feedback.owning_profile.name")))
    put("page_id", *first(node, base, "id", lambda p, v: p.endswith("delegate_page.id")))
    put("message", *first(node, base, "text", lambda p, v: p.endswith("message.story.message.text")))
    p, v = first(node, base, "reaction_count", lambda p, v: isinstance(v, dict) and "count" in v)
    put("reaction_count", p and p + ".count", v and v["count"])
    p, v = first(node, base, "share_count", lambda p, v: isinstance(v, dict) and "count" in v)
    put("share_count", p and p + ".count", v and v["count"])
    put("reaction_display_strategy", *first(node, base, "reaction_display_strategy"))
    p, v = first(node, base, "top_reactions", lambda p, v: isinstance(v, dict) and "edges" in v)
    if p:
        out["reactions"] = [
            {"name": e["node"]["localized_name"], "reaction_id": e["node"]["id"],
             "path": f"{p}.edges[{i}].reaction_count", "value": e["reaction_count"],
             "i18n_reaction_count": e.get("i18n_reaction_count")}
            for i, e in enumerate(v["edges"])]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="已登入瀏覽器設定檔的副本目錄")
    ap.add_argument("--url", required=True)
    ap.add_argument("--dialog", action="store_true", help="點開心情對話框，取 GraphQL 回應的各心情數字")
    ap.add_argument("-o", "--out", default="fb_post_fields.json")
    a = ap.parse_args()

    dialog = []
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(a.profile, headless=False, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def on_response(resp):
            if "/api/graphql/" not in resp.url:
                return
            if resp.request.headers.get("x-fb-friendly-name") != "CometUFIReactionsDialogQuery":
                return
            m = re.search(r"(?:^|&)doc_id=(\d+)", resp.request.post_data or "")
            try:
                first_line = resp.text().splitlines()[0]
                summary = json.loads(first_line)["data"]["node"]["top_reactions"]["summary"]
            except Exception:
                return
            dialog.append({"friendly_name": "CometUFIReactionsDialogQuery", "doc_id": m and m.group(1),
                           "reactions": [{"name": s["reaction"]["localized_name"], "reaction_id": s["reaction"]["id"],
                                          "path": f"data.node.top_reactions.summary[{i}].reaction_count",
                                          "value": s["reaction_count"]} for i, s in enumerate(summary)]})

        page.on("response", on_response)
        page.goto(a.url, wait_until="domcontentloaded")
        time.sleep(6)
        if any(w in page.url for w in STOP_WORDS):
            sys.exit(f"停止：被導到 {page.url.split('?')[0]}（登入或安全驗證）")

        scripts = page.eval_on_selector_all('script[type="application/json"]', "els => els.map(e => e.textContent)")
        result = None
        for i, s in enumerate(scripts):
            if "creation_time" not in s or "node_v2" not in s:
                continue
            got = extract(json.loads(s))
            if got:
                result = {"source": {"type": "document-embedded", "script_index": i}, "fields": got}
                break
        del scripts  # 不留整頁資料

        if a.dialog:
            btn = page.locator('[aria-label*="心情"], [aria-label*="reactions"]').first
            btn.click()
            time.sleep(5)
        ctx.close()

    if result is None:
        sys.exit("找不到：頁面內嵌資料裡沒有這則貼文的 node_v2")
    if dialog:
        result["dialog_graphql"] = dialog[-1]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"寫入 {a.out}：{', '.join(result['fields'])}")


if __name__ == "__main__":
    main()
