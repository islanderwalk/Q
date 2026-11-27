# 證據：回補真的照「渠道 × 月」切批，在 ES 裡怎麼切

擷取時間 2026-11-14 16:02（台北）。來源是 WSL Ubuntu 上 docker compose 專案 qsearch-q6 的 Elasticsearch 9.5.5（localhost:9200）和 PostgreSQL 回補帳本（127.0.0.1:55433）。每張圖都是當下發出請求、把原始回應排版後截圖，產生腳本是同目錄的 `capture.js`。

| 圖 | 證明什麼 |
|---|---|
| 01-es-cluster-indices.png | 本機 ES 單節點，posts 索引 743,667 篇 |
| 02-es-mapping-no-physical-split.png | ES 沒有按渠道或月份實體切分：只有一個 posts 索引、3 個 shard、沒有自訂 routing；channel、month 是 keyword 欄位 |
| 03-es-agg-channel-month.png | 用 terms 聚合數出每個渠道每個月有幾篇 |
| 04-ledger-177-partitions.png | 帳本一列就是一個分區，分區編號為「渠道/月」，共 177 列（ptt、youtube、instagram 各 45，facebook 23，threads 19） |
| 05-ledger-vs-es-count.png | 已跑完的 5 個分區（2026-09 × 5 渠道）：帳本 raw_count 和 ES 用 channel＋month＋新版 seg_ver 篩出的篇數逐批相同 |
| 06-es-sample-doc.png | 一篇實際文件：_id 就是 post_id，_source 帶 channel、month，新斷詞寫在 terms_v2 |
| 07-code-partition-source.png | 程式碼：分區從原文表 GROUP BY channel, month 產生；讀一批用 WHERE channel AND month |
| 08-code-ledger-es.png | 程式碼：建帳本、由新到舊領批、寫進 ES 時算 month、閘門用 channel＋month 圈出這一批 |

## 怎麼切

1. 原文表（BigQuery，本機用檔案對應元件）用 `GROUP BY channel, month` 列出所有分區，見 rawsrc.py 第 132–143 行。
2. 每個分區在 PostgreSQL 帳本 backfill_ledger 寫一列，分區編號是「渠道/月」，見 job.py 第 410 行。
3. Worker 依 `ORDER BY month DESC, channel` 一次領一批，見 job.py 第 202 行。
4. 讀這一批原文時用 `WHERE channel = @channel AND month = @month`，見 rawsrc.py 第 63、68 行。
5. 斷詞後用 Bulk update 寫回同一個 posts 索引，_id 是 post_id，見 es.py 第 52 行。month 由 published_at 前 7 碼算出，見 pipeline.py 第 84、93 行。
6. 閘門用 channel＋month 兩個 term 篩出這一批來對帳，見 job.py 第 158–160 行。

所以在 Elasticsearch 裡，「渠道×月」不是實體切分（沒有分索引、沒有 routing），而是每篇文件帶 channel、month 兩個 keyword 欄位，查詢時篩出來的邏輯範圍。

## 和 GitHub 對得上

圖 07、08 引用的 5 支檔案（rawsrc.py、job.py、pipeline.py、es.py、pg/init.sql），本機 `git hash-object` 和 GitHub islanderwalk/claude-code 分支 claude/history-crawl-head-increment-2026-11-03 上的 blob sha 逐一比對過，全部相同。

## 不一致處（照實寫）

- **ES 範圍比帳本大：** ES 有 2026-10、2026-11 的資料，以及 media 渠道（861 篇），帳本沒有。帳本只列原文表到 2026-09 的 5 個渠道；更新的資料是串流進來的。
- **聚合的首月差一個月：** 圖 03 的 first 是 published_at 以 UTC 顯示，所以 2023-01-01 台北時間的文章會顯示成 2022-12。month 欄位取的是原字串前 7 碼，帳本最早是 2023-01。
- **seg_ver 總數比 5 批多：** ES 裡 seg_ver_v2 = 22026111201 的文件共 95,268 篇，比 5 批合計的 65,789 多。多出來的是串流斷詞服務寫的；用 channel＋month 篩時，逐批數字完全相同。
- **只跑完 5 個分區：** 177 個分區只跑完 5 個（狀態 awaiting_pm，等 PM 首月抽查），其餘 172 個是 pending。

## Kibana 截圖（只用題目的 ES，不經 PG）

2026-11-14 用 Kibana 9.5.5 Discover 的 ES|QL 直接查 posts 索引，腳本 `capture_kibana.js`。

| 圖 | ES|QL／頁面 | 看到什麼 |
|---|---|---|
| `09-kibana-esql-channel-month.png` | `FROM posts \| STATS posts = COUNT(*) BY channel, month` | 渠道×月共 189 組，每組一列篇數 |
| `10-kibana-esql-partitions-per-channel.png` | 再對上一步依渠道統計月數 | ptt／instagram／youtube 各 47 個月（2023-01～2026-11）、facebook 25（2024-11 起）、threads 21（2025-03 起）、media 2（2026-10 起） |
| `11-kibana-esql-ptt-2026-09.png` | `WHERE channel == "ptt" AND month == "2026-09"` | 32,652 篇，就是一批的範圍 |
| `12-kibana-index-posts.png` | Index Management → posts | 只有一個索引：3 primaries／0 replicas、743,667 篇、1018.68 MB |

判讀：ES 裡沒有按月份或渠道拆成多個索引；「一批」就是 `channel` 和 `month` 兩個 keyword 欄位的篩選條件。189 組比帳本的 177 組多 12 組，是 2026-10、2026-11 兩個月（5 渠道 × 2 = 10 組）加上 media 的 2 組。
