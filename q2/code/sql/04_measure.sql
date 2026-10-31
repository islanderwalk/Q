-- 量測 SQL。run_q2.py 以 "-- @query <名稱>" 切段，每段取第一列 JSON。
-- 需要 pageinspect 擴充（get_raw_page / heap_page_items / gin_metapage_info）。

-- @query distinct_check
-- 造資料正確性：內文不同段數（repeat 模式應為 1,000；distinct 模式應接近列數）、
-- 讚數的不同值個數（應 > 1，否則內容被快取成常數）
-- message_runs：依實體順序（ctid）內文換段的次數；repeat 模式應等於段數（每段連續 1,000 則）
SELECT count(*)::bigint                   AS rows,
       count(DISTINCT message)::bigint    AS distinct_messages,
       count(DISTINCT like_cnt)::bigint   AS distinct_like_cnt,
       count(DISTINCT post_id)::bigint    AS distinct_post_ids,
       (SELECT count(*) FROM (
            SELECT message, lag(message) OVER (ORDER BY ctid) AS prev FROM fb_posts) AS s
        WHERE message IS DISTINCT FROM prev)::bigint AS message_runs
FROM fb_posts;

-- @query trgm_sanity
-- pg_trgm 能否抽出中文三字詞（LC_CTYPE 是否把 CJK 視為字母）：trgm_per_row 應 > 0
SELECT array_length(show_trgm(message), 1)::bigint AS trgm_per_row FROM fb_posts LIMIT 1;

-- @query table_size
SELECT pg_relation_size('fb_posts')::bigint               AS table_bytes,
       (pg_relation_size('fb_posts') / 8192)::bigint      AS table_pages,
       (SELECT count(*) FROM fb_posts)::bigint            AS rows;

-- @query heap_page0_lp
SELECT count(*)::int                         AS rows_on_page0,
       avg(lp_len)::numeric(10, 2)           AS avg_lp_len,
       min(lp_len)                           AS min_lp_len,
       max(lp_len)                           AS max_lp_len
FROM heap_page_items(get_raw_page('fb_posts', 0))
WHERE lp_flags = 1;

-- @query avg_column_size
SELECT avg(pg_column_size(t))::numeric(10, 1) AS avg_row_datum_bytes,
       avg(pg_column_size(t.message))::numeric(10, 1) AS avg_message_bytes
FROM fb_posts AS t;

-- @query index_sizes
SELECT indexrelname AS index_name,
       pg_relation_size(indexrelid)::bigint AS index_bytes
FROM pg_stat_user_indexes
WHERE relname = 'fb_posts'
ORDER BY indexrelname;

-- @query gin_bigram_meta
SELECT n_entries, n_total_pages, n_entry_pages, n_data_pages
FROM gin_metapage_info(get_raw_page('fb_posts_bigram_gin', 0));

-- @query gin_trgm_meta
SELECT n_entries, n_total_pages, n_entry_pages, n_data_pages
FROM gin_metapage_info(get_raw_page('fb_posts_trgm_gin', 0));

-- @query gin_simple_meta
SELECT n_pending_pages, n_entries, n_total_pages, n_entry_pages, n_data_pages
FROM gin_metapage_info(get_raw_page('fb_posts_simple_gin', 0));
