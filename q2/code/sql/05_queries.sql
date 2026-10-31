-- 查詢計時 SQL（參數：%(term)s 為 4 字中文詞，%(page_id)s 為粉絲頁 ID）。
-- run_q2.py 以 "-- @query <名稱>" 切段；每段跑 1 次暖機 + 3 次計時，取中位數。
-- 三種中文全文查詢的結果數應該一致（都是「子字串出現在內文」），由 run_q2.py 核對。

-- @query bigram_contains
SELECT count(*) FROM fb_posts
WHERE q2_bigrams(message) @> q2_bigrams(%(term)s)
  AND strpos(message, %(term)s) > 0;

-- @query trgm_like
SELECT count(*) FROM fb_posts
WHERE message LIKE ('%%' || %(term)s || '%%');

-- @query simple_tsvector
SELECT count(*) FROM fb_posts
WHERE to_tsvector('simple', message) @@ plainto_tsquery('simple', %(term)s);

-- @query btree_page_id
SELECT count(*) FROM fb_posts WHERE page_id = %(page_id)s;
