"""QSearch Q6 斷詞服務（真實環境版）。

python -m qseg service   串流：訂閱 Pub/Sub（模擬器或真 GCP），斷詞後條件寫入 ES
python -m qseg job       回補：從 PostgreSQL 帳本領一個分區（渠道×月），斷詞、寫入、過閘門 G1–G3、記 checkpoint
python -m qseg shadow    每日影子比對：抽樣重算，與 ES 裡的 terms_v2 比，差異須為 0
python -m qseg segment   讀 stdin 的 JSON 一行一篇，輸出斷詞結果（除錯用）
"""
