-- 真的 pg_bigm（只在 run_q2.py --pg-bigm 時使用）。需要裝了 pg_bigm 的伺服器，
-- 例如 shared/infra/onprem/postgres-bigm/Dockerfile 以 PG_BASE=postgres:16.15-bookworm 建的映像。
-- run_q2.py 以 "-- @pg_bigm <名稱>" 切段：index 建索引、like 是查詢（參數同 05_queries.sql）、meta 量 GIN metapage。

-- @pg_bigm index
CREATE EXTENSION IF NOT EXISTS pg_bigm;
CREATE INDEX fb_posts_pg_bigm_gin ON fb_posts USING gin (message gin_bigm_ops);

-- @pg_bigm like
SELECT count(*) FROM fb_posts
WHERE message LIKE likequery(%(term)s);

-- @pg_bigm meta
SELECT n_entries, n_total_pages, n_entry_pages, n_data_pages
FROM gin_metapage_info(get_raw_page('fb_posts_pg_bigm_gin', 0));
