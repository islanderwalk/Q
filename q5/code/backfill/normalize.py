"""互動計數欄位的正規化（Q5 (c) 回補用）：Threads 轉發／引用／分享數、IG Reels 觀看數。

規則（依 q5/README.md 與 q5/evidence 的實測）：
- 計數一律取 JSON 整數，不讀畫面文字（Threads 畫面「1.2 萬」是 JSON 12,716 無條件捨去；
  IG 縮圖「4.2M」是 play_count 4,298,087 無條件捨去）。
- JSON 的 null 保留為 None，不補 0（分享旁沒有數字時 reshare_count 就是 null）。
- 「null」與「不知道」分開：key 存在且值為 null 是觀測結果；key 不存在、型別錯（例如字串「1.2萬」）
  或負數是「不知道」，列在 unknown，回補時不拿它蓋掉別次抓取的值。
- 畫面的轉發數 = repost_count + quote_count（q5 核對 18 篇全部符合），本函式只存兩個分量。
- 歷史欄位名可能不一致：依別名表依序尋找。每組的第一個名稱是實抓看到的，其餘都是假設。
- 同一筆同時有兩個名稱且值不同：Threads 以實抓看到的名稱為準（舊名只是改名）；
  IG 的 play_count 與 video_view_count 可能是不同指標（播放次數與觀看次數），值不同時當成不知道、不寫入，
  等 PM 決定要哪一個。

別名的證據等級（2026-10-29、10-30 用 Playwright 未登入實抓）：
- 實抓看到：Threads 的 post.text_post_app_info.{repost_count, quote_count, reshare_count}
  （q5/evidence/a-dataflow/threads-evidence-panel.png 的 JSON 片段，@threads 16 篇＋Use media 2 篇；
  讀取程式 q5/evidence/src/threads_verify.js）；IG Reels 分頁 node.play_count
  （q5/evidence/playwright/ig_reel_fields.json，12 筆）。
- 實抓沒看到：IG 的 video_view_count（題目用的名稱）。capture_gaps.py 在 Reels 分頁與單支 Reel 頁
  搜尋 video_view_count、play_count、view_count、ig_play_count、video_play_count 五個 key，
  只記錄「值是數字」的；結果只有 play_count。值為 null 的同名 key 有沒有出現，這份證據看不出來。
- 假設（任何證據都沒出現，要用 GCS 原始檔逐一核對後才能定案）：
  textPostAppInfo、repostCount、repost_cnt 等 camelCase／縮寫舊名，以及 IG 舊檔用 video_view_count。
- shared/capture/fixtures/threads_graphql_sample.json 只留 code、帳號、taken_at、內文，
  沒有任何計數欄位，不能拿來核對欄位名。
"""

# 容器：第一個是實抓看到的，第二個是假設
CONTAINERS = ("text_post_app_info", "textPostAppInfo")

# 每組：(實抓看到的名稱, 假設的舊名...)；用到第一個以外的名稱會記 alias_used 旗標
ALIASES = {
    "repost_count": ("repost_count", "repostCount", "repost_cnt"),
    "quote_count": ("quote_count", "quoteCount", "quote_cnt"),
    "reshare_count": ("reshare_count", "reshareCount", "reshare_cnt"),
}

# IG 觀看數：ES 欄位沿用題目名稱 video_view_count，來源 key 以實抓的 play_count 為準。
# play_count 與題目說的 video_view_count 定義是否相同沒有查證，上線前要跟 PM 確認；
# 在那之前，兩個 key 同時出現且值不同的那一筆當成不知道（見 normalize_ig_views）。
IG_VIEW_ALIASES = ("play_count", "video_view_count")

STATUS_OK = "ok"
STATUS_MISSING_CONTAINER = "missing_container"
STATUS_MISSING_FIELD = "missing_field"


def _as_count(value):
    """回傳 (值, 問題旗標)。None 保留；非整數（含字串「1.2萬」、浮點 35.0）標為 invalid。"""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int):
        return None, "invalid_type"
    if value < 0:
        return None, "negative"
    return value, None


def _pick(obj: dict, field: str, aliases, out: dict, conflict_is_unknown=False):
    """依別名順序取值，寫入 out[field]；缺漏、型別問題、用到的別名記在 out["flags"]，
    不知道的欄位記在 out["unknown"]。同一筆同時有兩個別名且值不同時記 alias_conflict；
    conflict_is_unknown 為 True 時，這種情況也當成不知道（值為 None）。"""
    present = [k for k in aliases if k in obj]
    if not present:
        out[field] = None
        out["flags"].append(f"missing:{field}")
        out["unknown"].append(field)
        return
    key = present[0]
    value, problem = _as_count(obj[key])
    out[field] = value
    if problem:
        out["flags"].append(f"{problem}:{field}")
        out["unknown"].append(field)
    if key != aliases[0]:
        out["flags"].append(f"alias_used:{field}={key}")
    if any(obj[k] != obj[key] for k in present[1:]):
        out["flags"].append(f"alias_conflict:{field}")
        if conflict_is_unknown and field not in out["unknown"]:
            out[field] = None
            out["unknown"].append(field)


def normalize_counts(post: dict) -> dict:
    """從單篇 Threads 貼文（post 物件）取出三個計數。

    回傳 {"repost_count", "quote_count", "reshare_count", "status", "flags", "unknown"}。
    - 找不到容器 → status=missing_container，三個計數都是 None（不是 0），三個都列在 unknown。
    - key 不存在 → flags 記 "missing:<欄位>"，值為 None，列在 unknown。
    """
    container, used = None, None
    for name in CONTAINERS:
        if isinstance(post.get(name), dict):
            container, used = post[name], name
            break
    if container is None:
        return {"repost_count": None, "quote_count": None, "reshare_count": None,
                "status": STATUS_MISSING_CONTAINER,
                "flags": ["missing_container"], "unknown": list(ALIASES)}

    out = {"status": STATUS_OK, "flags": [], "unknown": []}
    if used != CONTAINERS[0]:
        out["flags"].append(f"alias_used:container={used}")
    for field, aliases in ALIASES.items():
        _pick(container, field, aliases, out)
    return out


def normalize_ig_views(node: dict) -> dict:
    """從單支 IG Reel（Reels 分頁的 node 物件）取出觀看數，存成 video_view_count。

    回傳 {"video_view_count", "status", "flags", "unknown"}；兩個 key 都沒有時 status=missing_field、值為 None。
    play_count 與 video_view_count 同時出現且值不同時，記 alias_conflict 並當成不知道：
    兩者可能是不同指標，不能默默把 play_count 寫進 video_view_count。
    """
    out = {"status": STATUS_OK, "flags": [], "unknown": []}
    _pick(node, "video_view_count", IG_VIEW_ALIASES, out, conflict_is_unknown=True)
    if "missing:video_view_count" in out["flags"]:
        out["status"] = STATUS_MISSING_FIELD
    return out


def screen_repost(counts: dict):
    """畫面「轉發數」= repost_count + quote_count；任一為 None 時回傳 None（不把缺值當 0）。"""
    r, q = counts.get("repost_count"), counts.get("quote_count")
    if r is None or q is None:
        return None
    return r + q
