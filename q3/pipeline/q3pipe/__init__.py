"""Q3 PTT 八卦板全文搜尋 ETL pipeline：每小時抓新文＋重抓 7 天內文章 → 原始 HTML 存 GCS → MERGE 進 BigQuery → 每日不變量檢查 → 查詢 API。"""
