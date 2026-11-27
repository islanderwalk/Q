"""在 Kibana（Dev Tools Console）裡跑 ES 查詢，截下實際畫面（不加任何排版，紅框之後再疊）。

前置：WSL 內 q2demo-es、q2demo-kibana 在跑（Kibana 在 127.0.0.1:15610），fb_posts 索引已灌好（run_demo.py）。
輸出：kibana_raw.png（無紅框）、kibana_boxes.json（要框的三筆分數的位置，供 compose.py 用）
"""
import json
import os
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
KB = "http://127.0.0.1:15610"
QUERY = """GET fb_posts/_search?filter_path=hits.total,hits.hits._score,hits.hits._source.content
{
  "size": 9,
  "_source": ["content"],
  "query": { "match_phrase": { "content": "颱風" } }
}"""

W, H = 1500, 830


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_context(permissions=["clipboard-read","clipboard-write"], viewport={"width": W, "height": H}, device_scale_factor=1).new_page()
        pg.goto(KB + "/app/dev_tools#/console")
        pg.wait_for_timeout(5000)
        # 關掉橫幅與右下角提示
        for name in ("Dismiss",):
            for el in pg.get_by_role("button", name=name).all():
                try:
                    el.click(timeout=1500)
                except Exception:
                    pass
        pg.wait_for_timeout(800)
        # 清空範例、貼上查詢、執行
        pg.locator(".monaco-editor").first.click()
        pg.keyboard.press("Control+A")
        pg.keyboard.press("Delete")
        pg.evaluate("t=>navigator.clipboard.writeText(t)", QUERY)
        pg.keyboard.press("Control+V")
        pg.wait_for_timeout(500)
        pg.keyboard.press("Control+Home")
        pg.keyboard.press("Control+Enter")
        pg.wait_for_timeout(3500)
        pg.screenshot(path=os.path.join(HERE, "kibana_raw.png"))
        b.close()


if __name__ == "__main__":
    main()
