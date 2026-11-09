# Q2 FB 貼文欄位 — 畫面與 F12 對照證據

目標貼文：NASA 粉專〈Welcome to the Artemis Accords, Vietnam〉
<https://www.facebook.com/NASA/posts/pfbid02i7hXRsxkXQbb9mvtxBb5jCBdb4pk8xitpnP3cCZF6C56PXk5SNU8sfuwEobZzV5zl>

每個欄位有三份對照：

- **畫面截圖**：FB 頁面上看得到的地方。
- **F12 截圖**：DevTools 搜尋到該值的位置。
- **JSON**：[`fb_post_fields.json`](fb_post_fields.json) 裡的完整路徑、值與來源。

截圖上紅框與白底說明框都是**截圖時加上的標註**，每張都寫了擷取時間。

## 欄位對照

`N` = 貼文頁內嵌 JSON 裡這則貼文的 `…__bbox.result.data.node_v2`；
`F` = `N.comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context.comet_ufi_summary_and_actions_renderer.feedback`。
完整路徑見 JSON。

| 欄位 | 畫面截圖 | F12 截圖 | JSON 路徑 | 值 |
|---|---|---|---|---|
| 貼文 ID | 畫面不顯示 | [fb_f12_post_id.png](fb_f12_post_id.png) | `N.post_id` | `1671214107707292` |
| 粉專 ID（Page ID） | 畫面不顯示 | [fb_f12_page_id.png](fb_f12_page_id.png) | `N.comet_sections.context_layout.story.comet_sections.actor_photo.story.actors[0].delegate_page.id` | `54971236771` |
| 粉專 ID（貼文擁有者 profile ID） | 畫面不顯示 | [fb_f12_profile_id.png](fb_f12_profile_id.png) | `N.feedback.owning_profile.id` | `100044561550831` |
| 粉專名稱 | [fb_post_annotated.png](fb_post_annotated.png) | [fb_f12_page_name.png](fb_f12_page_name.png) | `N.feedback.owning_profile.name` | NASA - National Aeronautics and Space Administration |
| 發文時間 | [fb_post_time_tooltip.png](fb_post_time_tooltip.png)（滑過顯示「2026年10月30日 星期五上午5:45」） | [fb_f12_creation_time.png](fb_f12_creation_time.png) | `N.creation_time` | `1793310300` = 2026-10-30 05:45:00 UTC+8 |
| 內文 | [fb_post_annotated.png](fb_post_annotated.png) | [fb_f12_message.png](fb_f12_message.png) | `N.comet_sections.content.story.comet_sections.message.story.message.text` | Welcome to the Artemis Accords, Vietnam 🇻🇳 … https://go.nasa.gov/4BvnGSZ |
| 心情總數 | [fb_post_annotated.png](fb_post_annotated.png)（只有圖示，沒有數字） | [fb_f12_reaction_count.png](fb_f12_reaction_count.png) | `F.adaptive_ufi_action_renderers[0].feedback.reaction_count.count` | `27063` |
| 分享數 | [fb_post_annotated.png](fb_post_annotated.png)（「1,406」） | [fb_f12_share_count.png](fb_f12_share_count.png) | `F.adaptive_ufi_action_renderers[2].feedback.share_count.count` | `1406` |
| 讚 | [fb_post_reactions_dialog.png](fb_post_reactions_dialog.png) | [fb_f12_reaction_like.png](fb_f12_reaction_like.png) | `F.top_reactions.edges[0].reaction_count` | `18961` |
| 大心 | 同上 | [fb_f12_reaction_love.png](fb_f12_reaction_love.png) | `F.top_reactions.edges[1].reaction_count` | `7510` |
| 加油 | 同上 | [fb_f12_reaction_care.png](fb_f12_reaction_care.png) | `F.top_reactions.edges[2].reaction_count` | `377` |
| 哇 | 同上 | [fb_f12_reaction_wow.png](fb_f12_reaction_wow.png) | `F.top_reactions.edges[3].reaction_count` | `121` |
| 哈 | 同上 | [fb_f12_reaction_haha.png](fb_f12_reaction_haha.png) | `F.top_reactions.edges[4].reaction_count` | `79` |
| 怒 | 同上 | [fb_f12_reaction_angry.png](fb_f12_reaction_angry.png) | `F.top_reactions.edges[5].reaction_count` | `9` |
| 嗚 | 同上 | [fb_f12_reaction_sad.png](fb_f12_reaction_sad.png) | `F.top_reactions.edges[6].reaction_count` | `6` |
| （心情顯示設定） | 畫面只有圖示 | [fb_f12_reaction_display_strategy.png](fb_f12_reaction_display_strategy.png) | `F.adaptive_ufi_action_renderers[0].feedback.reaction_display_config.reaction_display_strategy` | `HIDE_COUNTS` |
| 七種心情（第二來源） | [fb_post_reactions_dialog.png](fb_post_reactions_dialog.png) | [fb_f12_reactions_dialog_graphql.png](fb_f12_reactions_dialog_graphql.png) | GraphQL `CometUFIReactionsDialogQuery`（doc_id `28949165141359436`）`data.node.top_reactions.summary[i].reaction_count` | 讚 18965 · 大心 7510 · 加油 377 · 哇 121 · 哈 79 · 怒 9 · 嗚 6 |

每種心情的 ID（`top_reactions.edges[i].node.id`）是固定值：

| 心情 | ID |
|---|---|
| 讚 | 1635855486666999 |
| 大心 | 1678524932434102 |
| 加油 | 613557422527858 |
| 哇 | 478547315650144 |
| 哈 | 115940658764963 |
| 怒 | 444813342392137 |
| 嗚 | 908563459236466 |

不同語系拿到的心情名稱不一樣，所以對應時要用心情 ID 比對，不要用名稱。

找不到的欄位：無。表中每個欄位都在頁面資料裡找到了。

## 觀察

1. **這則貼文畫面上看不到任何心情數字。** 貼文資料設定 `reaction_display_strategy = HIDE_COUNTS`：
   - 貼文下方只有心情圖示。
   - 心情對話框只列「全部／讚／大心／加油／更多」與名稱。
   - 數字只在頁面內嵌資料和對話框的 GraphQL 回應裡。

   爬蟲不能靠畫面文字取心情數，要讀資料。
2. **粉專 ID 有兩個值。** 貼文資料裡的擁有者是 profile ID `100044561550831`（`__typename` 為 `User`）。對外的 Page ID `54971236771` 在 `actors[0].delegate_page.id`，和粉專透明度頁的 ID 相同（見 [`../playwright/fb_page_transparency_id.png`](../playwright/fb_page_transparency_id.png)）。「粉絲頁 ID」欄位存 Page ID，profile ID 另存備查。
3. **數字會隨時間變動，不同截圖的總數不一定相同。** 這段期間貼文持續有人按心情：

   | 時間（UTC+8） | 擷取方式 | 讚 | 總數 |
   |---|---|---|---|
   | 2026-11-04 23:52 | 第 1 次載入（[fb_post_annotated.png](fb_post_annotated.png) 說明框） | — | 27,059 |
   | 2026-11-04 23:54 | 第 1 次載入時開啟的心情對話框（[fb_post_reactions_dialog.png](fb_post_reactions_dialog.png)） | 18,960 | 27,062 |
   | 2026-11-04 23:56 | 第 2 次載入（F12 截圖與 JSON 主值） | 18,961 | 27,063 |
   | 2026-11-05 00:06 | 再次開啟心情對話框（[fb_f12_reactions_dialog_graphql.png](fb_f12_reactions_dialog_graphql.png)） | 18,965 | 27,067 |

   後六種心情這段時間沒有變化。同一次載入裡，七種相加等於總數：18961+7510+377+121+79+9+6 = 27063。

   所以每筆紀錄都要附擷取時間，寫回資料庫時以最新一次為準。
4. **發文時間存 Unix 秒。** 畫面顯示「10月30日上午5:45」，會隨語系與時區變化，也會變成「14 小時」這類相對時間。`creation_time` 是絕對值，存它再依需要轉時區。

## 擷取方式

- **時間**：2026-11-04 23:47 – 2026-11-05 00:09（UTC+8）。
- **瀏覽器**：Chromium（Playwright persistent context），開一份已登入設定檔的**副本**。用完已刪除。
- **頁面載入**：共 2 次，間隔超過 5 秒。
  - 第 1 次：畫面截圖，滑過時間顯示完整時間，開啟心情對話框。
  - 第 2 次：先開 DevTools 再重新整理，讓 Sources 全域搜尋涵蓋文件本身。
- **F12 操作**：
  - Sources：用 `Ctrl+Shift+F` 全域搜尋，大多用 regex。一般搜尋會把 `"post_id":"…"` 拆成幾個詞分別比對，所以多數欄位用 `.{n}` 跳過引號與冒號，把鍵名和值綁在同一個搜尋式裡。
  - 七種心情：用心情 ID 加數字搜尋，確保搜到的是同一筆。
  - 對話框數字：在 Network 面板搜尋 `reaction_count_reduced`，找到 `graphql/` 回應，看 Response 分頁。
- **只讀**：沒有按讚、留言、追蹤、送出。全程沒有遇到登入挑戰或安全驗證。
- **重跑腳本**：[`../../code/capture_f12_fields.py`](../../code/capture_f12_fields.py)。
  - 載入一次頁面，只解析內嵌 JSON 取上表欄位。
  - 加 `--dialog` 會再取對話框 GraphQL 的心情數。
  - 不存 HTML、不存 cookie 與請求參數。

## 遮蔽

遮蔽方式和 [`q6/code/meta-login/redact.py`](../../../q6/code/meta-login/redact.py) 一樣：GaussianBlur(14) 加紅框。

已遮蔽的項目：

- 瀏覽器分頁標題。
- 右上角登入帳號頭像、名稱與通知數。
- 左右側欄。
- 留言者名稱、頭像與留言內容。
- 留言輸入框。
- 底部狀態列：滑過連結時會顯示帶追蹤參數的網址。

沒有遮蔽的項目：NASA 粉專本身的名稱、ID、內文與數字。這些就是要證明的欄位。

repo 裡不放這些東西：

- 整頁 HTML、整份 GraphQL 回應。
- cookie、`fb_dtsg`、`lsd`、`jazoest` 等請求參數。
- 登入帳號自己的 ID 與名稱。

擷取用的原始檔只放在暫存區，整理完已刪除。
