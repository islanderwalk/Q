import asyncio, json, pathlib
from playwright.async_api import async_playwright
T = json.loads(pathlib.Path("../../answer/src/ptt_targets.json").read_text(encoding="utf-8"))
async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1000, "height": 900}, device_scale_factor=1, locale="zh-TW")
        await ctx.add_cookies([{"name": "over18", "value": "1", "domain": "www.ptt.cc", "path": "/"}])
        pg = await ctx.new_page()
        for label, aid in [("deleted", T["deleted"][0]["aid"]), ("truncated", T["truncated"][0]["aid"])]:
            await asyncio.sleep(1.5)
            r = await pg.goto(f"https://www.ptt.cc/bbs/Gossiping/{aid}.html", wait_until="domcontentloaded")
            body = (await pg.inner_text("body"))[:300].replace("\n", " | ")
            print(label, aid, r.status, body)
        await b.close()
asyncio.run(main())
